"""
AI代理基础类模块

封装对 OpenAI 兼容接口（OpenAI、OpenRouter、vLLM 等）的调用：
统一的重试策略、上下文窗口保护、请求日志，以及向运行时上报 LLM 重试与失败。
"""

import json
import logging
import os
import random
import sys
import time
import uuid

import yaml
from openai import APIConnectionError, APIStatusError, OpenAI

from simulation.runtime.agent_run import current_agent_run
from simulation.runtime.llm_context import estimate_tokens, utc_now


logger = logging.getLogger(__name__)
_THIS_FILE = os.path.abspath(__file__)

# 默认系统提示词
sys_default_prompt = "You are a helpful assistant."

# 未在配置中声明时使用的模型上限；按需在 api.yaml 中用 context_window / max_output_tokens 覆盖。
DEFAULT_CONTEXT_WINDOW = 32768
DEFAULT_MAX_OUTPUT_TOKENS = 8192

# 只对暂时性错误重试：超时/连接失败（APIConnectionError 包含 APITimeoutError）、限流和服务端错误。
RETRYABLE_STATUS = {408, 409, 429}


class BaseAgent:
    """
    OpenAI 兼容接口的 LLM 代理。

    api.yaml 字段：
        model_name, base_url        必填
        api_key                     字符串或列表（随机选一个）；未开启鉴权的 vLLM 可省略
        timeout                     单次请求超时（秒），默认 120
        max_retries, retry_delay    重试次数（含首次请求）与退避基数，默认 3 / 1 秒
        context_window, max_output_tokens   模型上限，用于收缩 max_tokens
        chat_template_kwargs        可选，原样透传给 vLLM 的对话模板，如 {enable_thinking: false}
    """

    def __init__(self, system_prompt=sys_default_prompt, config_path="./config/api.yaml"):
        # 相对路径以 backend/ 目录为基准
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), config_path)
        with open(config_path, "r") as config_file:
            self.config = yaml.safe_load(config_file)

        api_keys = self.config.get("api_key") or ["EMPTY"]
        if isinstance(api_keys, str):
            api_keys = [api_keys]
        self.model_name = self.config["model_name"]
        self.base_url = self.config["base_url"]
        self.default_system_prompt = system_prompt
        self.max_retries = int(self.config.get("max_retries", 3))
        self.retry_delay = float(self.config.get("retry_delay", 1))
        self.context_window = int(self.config.get("context_window", DEFAULT_CONTEXT_WINDOW))
        self.max_output_tokens = int(self.config.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS))
        self.chat_template_kwargs = self.config.get("chat_template_kwargs")
        self.client = OpenAI(
            api_key=random.choice(api_keys),
            base_url=self.base_url,
            timeout=self.config.get("timeout", 120),
            # 重试只由 __call_api 负责；SDK 默认的 2 次隐式重试会让单次调用耗时成倍增加。
            max_retries=0,
        )

    @staticmethod
    def _estimate_input_tokens(messages):
        """粗略估算输入 token，仅用于保护上下文窗口。"""
        return estimate_tokens(messages)

    @staticmethod
    def _calling_step():
        """发起本次调用的 Agent.py 之外的函数名，例如 _make_final_decision。"""
        frame = sys._getframe(1)
        while frame is not None and os.path.abspath(frame.f_code.co_filename) == _THIS_FILE:
            frame = frame.f_back
        return frame.f_code.co_name if frame is not None else "unknown"

    def _effective_max_tokens(self, messages, requested_max_tokens):
        input_tokens = self._estimate_input_tokens(messages)
        # 预留少量余量，避免估算误差把接近上限的请求变成服务端 400。
        available = self.context_window - input_tokens - max(32, self.context_window // 1000)
        effective = min(int(requested_max_tokens), self.max_output_tokens, available)
        if effective < 1:
            raise ValueError(
                "输入估算为 %s tokens，已超过 %s 的上下文窗口 %s"
                % (input_tokens, self.model_name, self.context_window)
            )
        return effective, input_tokens

    @staticmethod
    def _status(error):
        return getattr(error, "status_code", None)

    @classmethod
    def _error_code(cls, error):
        return cls._status(error) or error.__class__.__name__

    @classmethod
    def _is_retryable(cls, error):
        if isinstance(error, APIConnectionError):
            return True
        status = cls._status(error) if isinstance(error, APIStatusError) else None
        return status is not None and (status in RETRYABLE_STATUS or status >= 500)

    def _log_api_event(self, **event):
        logger.info("llm_request %s", json.dumps(event, ensure_ascii=False, default=str))

    def __call_api(self, messages, temperature=0.9, max_tokens=8192, top_p=0.9,
                   frequency_penalty=0.5, presence_penalty=0.5, **kwargs):
        """调用接口并返回原始响应；仅对暂时性错误重试，最终失败时抛出异常。"""
        agent_run = current_agent_run()
        record = {"step": self._calling_step(), "messages": messages, "model": self.model_name,
                  "context_window": self.context_window, "started_at": utc_now()}
        clock = time.perf_counter()
        try:
            effective_max_tokens, input_tokens = self._effective_max_tokens(messages, max_tokens)
        except ValueError as e:
            # 调用方通常吞掉这个错误后才崩溃；记下来，让 Agent 的失败原因指向上下文超限。
            if agent_run is not None:
                agent_run.note_llm_error("ContextWindowExceeded", str(e))
                agent_run.record_llm_call(**record, max_tokens=None, latency_ms=0, error=e)
            raise
        try:
            response = self.__request(messages, effective_max_tokens, input_tokens, temperature, top_p,
                                      frequency_penalty, presence_penalty, **kwargs)
        except Exception as e:
            if agent_run is not None:
                agent_run.record_llm_call(**record, max_tokens=effective_max_tokens,
                                          latency_ms=(time.perf_counter() - clock) * 1000, error=e)
            raise
        if agent_run is not None:
            agent_run.record_llm_call(**record, max_tokens=effective_max_tokens,
                                      latency_ms=(time.perf_counter() - clock) * 1000, response=response)
        return response

    def __request(self, messages, effective_max_tokens, input_tokens, temperature, top_p,
                  frequency_penalty, presence_penalty, **kwargs):
        """带重试地发送一次请求。"""
        request = {
            "model": self.model_name, "messages": messages, "max_tokens": effective_max_tokens,
            "temperature": temperature, "top_p": top_p,
            "frequency_penalty": frequency_penalty, "presence_penalty": presence_penalty,
            **kwargs,
        }
        # 非 OpenAI 标准参数，SDK 只接受经 extra_body 放入请求体；未配置时不发送，避免其他服务商拒绝。
        if self.chat_template_kwargs:
            request["extra_body"] = {"chat_template_kwargs": self.chat_template_kwargs, **kwargs.get("extra_body", {})}
        request_id = str(uuid.uuid4())
        for attempt in range(1, self.max_retries + 1):
            started_at = time.perf_counter()
            try:
                response = self.client.chat.completions.create(**request)
            except Exception as e:
                retryable = self._is_retryable(e)
                self._log_api_event(
                    event="error", model=self.model_name, request_id=request_id,
                    latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    input_tokens=input_tokens, attempt=attempt,
                    error_code=self._error_code(e), retryable=retryable,
                )
                print(f"[API错误] {str(e)}")
                agent_run = current_agent_run()
                if not retryable or attempt == self.max_retries:
                    if agent_run is not None:
                        agent_run.note_llm_error(str(self._error_code(e)), str(e))
                    raise
                if agent_run is not None:
                    agent_run.retrying("llm", message=f"LLM 请求失败，准备第 {attempt + 1}/{self.max_retries} 次请求",
                                       input_summary={"llm_attempt": attempt + 1, "max_retries": self.max_retries},
                                       error_code=str(self._error_code(e)), error_summary=str(e))
                time.sleep(self.retry_delay * attempt)
                continue
            usage = getattr(response, "usage", None)
            self._log_api_event(
                event="success", model=self.model_name, request_id=request_id,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
                input_tokens=getattr(usage, "prompt_tokens", input_tokens),
                output_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None), attempt=attempt,
                effective_max_tokens=effective_max_tokens,
            )
            return response

    def get_response(self, user_input=None, system_prompt=None, temperature=0.9, max_tokens=4096,
                     top_p=0.9, frequency_penalty=0.5, presence_penalty=0.5, debug=False,
                     messages=None, **kwargs):
        """
        获取模型回复。

        可传入单条 user_input，或完整的 messages 历史（不会被修改）；缺少 system 消息时自动补上。

        Returns:
            dict: {"response": 文本, "total_tokens": 数量}；出错时返回 {"error": "Error: ..."}
        """
        try:
            messages = [dict(message) for message in messages] if messages else []
            if not any(msg["role"] == "system" for msg in messages):
                messages.insert(0, {"role": "system", "content": system_prompt or self.default_system_prompt})
            if user_input is not None:
                messages.append({"role": "user", "content": user_input})

            response = self.__call_api(
                messages=messages, temperature=temperature, max_tokens=max_tokens, top_p=top_p,
                frequency_penalty=frequency_penalty, presence_penalty=presence_penalty, **kwargs,
            )
            usage = getattr(response, "usage", None)
            result = {
                "response": response.choices[0].message.content,
                "total_tokens": getattr(usage, "total_tokens", 0),
            }
            if debug:
                print("\033[92m" + f"[响应] {result['response']}" + "\033[0m")
            return result
        except Exception as e:
            print("\033[91m" + f"[错误] {str(e)}" + "\033[0m")
            return {"error": f"Error: {str(e)}"}


# 记录模块加载时间
start_time = time.time()
start_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_time))
print("模块加载开始时间:", start_date)
