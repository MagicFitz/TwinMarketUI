"""Safe read/write access to the two local model configuration files."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import requests
import yaml

from server.settings import Settings


ModelKind = Literal["language", "embedding"]

_FILES: dict[ModelKind, str] = {
    "language": "api.yaml",
    "embedding": "embedding.yaml",
}


class ModelSettingsService:
    """Expose a deliberately small, credential-safe model settings contract."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_all(self) -> dict[str, dict[str, Any]]:
        return {kind: self.get(kind) for kind in _FILES}

    def get(self, kind: ModelKind) -> dict[str, Any]:
        config = self._read(kind)
        api_key = config.get("api_key")
        if isinstance(api_key, list):
            api_key_configured = any(str(value).strip() for value in api_key)
        else:
            api_key_configured = bool(str(api_key or "").strip())

        shared = {
            "kind": kind,
            "last_test": self._last_test(kind, config),
            "configured": bool(config),
            "provider": self._text(config.get("provider")),
            "model_name": self._text(config.get("model_name")),
            "base_url": self._text(config.get("base_url")),
            "timeout": self._number(config.get("timeout")),
            "api_key_configured": api_key_configured,
        }
        if kind == "language":
            api_type = self._text(config.get("api_type")) or "openai"
            return {"api_type": api_type, **shared, "provider": shared["provider"] or api_type}
        return {
            **shared,
            "endpoint": self._text(config.get("endpoint")),
            "max_retries": self._number(config.get("max_retries")),
        }

    def update(self, kind: ModelKind, changes: dict[str, Any], *, api_key_provided: bool) -> dict[str, Any]:
        config = self._read(kind)
        allowed = {"provider", "model_name", "base_url", "timeout"}
        if kind == "language":
            allowed.add("api_type")
        else:
            allowed.update({"endpoint", "max_retries"})

        for key in allowed:
            if key in changes:
                value = changes[key]
                if value is None or value == "":
                    config.pop(key, None)
                else:
                    config[key] = value

        if api_key_provided:
            raw_key = str(changes.get("api_key") or "").strip()
            if raw_key:
                # Language-model callers support key rotation, hence the list.
                config["api_key"] = [raw_key] if kind == "language" else raw_key
            else:
                config.pop("api_key", None)

        self._validate(kind, config)
        self._write(kind, config)
        return self.get(kind)

    def test(self, kind: ModelKind, overrides: dict[str, Any]) -> dict[str, Any]:
        """Send one minimal request to the configured (or unsaved) endpoint and time it.

        Network failures are a test result, not an API error: they come back with
        ``ok: false`` and a readable message.  The API key is never echoed back.
        """
        stored = self._read(kind)
        config = {**stored, **{key: value for key, value in overrides.items() if key != "api_key" and value not in (None, "")}}
        self._validate(kind, config)
        api_key = str(overrides.get("api_key") or "").strip()
        if not api_key and self._target(kind, config) == self._target(kind, stored):
            # 只把已保存的密钥发往它原本配置的地址，避免改了 Base URL 后把密钥发给新主机。
            api_key = self._stored_key(stored)
        timeout = min(float(config.get("timeout") or 30), 60)
        url = self._target(kind, config)
        if not url:
            raise ValueError("base_url 不能为空")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if kind == "language":
            payload = {"model": config["model_name"], "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1, "temperature": 0}
        else:
            payload = {"model": config["model_name"], "input": "ping" if self._provider(config) == "openai" else ["ping"]}

        result: dict[str, Any] = {
            "kind": kind,
            "ok": False,
            "url": url,
            "model_name": config["model_name"],
            "used_api_key": bool(api_key),
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "status_code": None,
        }
        started = time.perf_counter()
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            result["latency_ms"] = round((time.perf_counter() - started) * 1000)
            result["status_code"] = response.status_code
            if response.ok:
                result.update(self._describe_success(kind, config, response))
            else:
                result["message"] = self._describe_http_error(response, api_key)
        except requests.Timeout:
            result["message"] = f"请求超时（{timeout:g} 秒内未响应）"
        except requests.ConnectionError:
            result["message"] = f"无法连接到 {urlparse(url).netloc}，请检查 Base URL 与网络"
        except requests.RequestException as error:
            result["message"] = f"请求失败：{type(error).__name__}"

        # 只有测的正是已保存的配置时才记住结果，用于设置页显示“上次测试”。
        if stored and not overrides.get("api_key") and self._fingerprint(kind, config) == self._fingerprint(kind, stored):
            self._save_test(kind, stored, result)
        return result

    def _describe_success(self, kind: ModelKind, config: dict[str, Any], response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {"message": "服务返回了非 JSON 响应"}
        if kind == "language":
            if not isinstance(body, dict) or not body.get("choices"):
                return {"message": "响应缺少 choices，可能不是 OpenAI 兼容的 Chat Completions 接口"}
            return {"ok": True, "message": "连接正常", "response_model": str(body.get("model") or config["model_name"])}
        vectors = body.get("embeddings") if isinstance(body, dict) and "embeddings" in body else [item.get("embedding") for item in (body.get("data") or [])] if isinstance(body, dict) else []
        if not vectors or not isinstance(vectors[0], list):
            return {"message": "响应中没有向量数据，可能不是兼容的 Embeddings 接口"}
        return {"ok": True, "message": "连接正常", "dimension": len(vectors[0])}

    @staticmethod
    def _describe_http_error(response: requests.Response, api_key: str) -> str:
        hint = {401: "认证失败，请检查 API Key", 403: "无权访问该模型或接口", 404: "接口或模型不存在，请检查 Base URL 与模型名称", 429: "请求被限流"}.get(response.status_code, "")
        try:
            body = response.json()
            detail = body.get("error", body) if isinstance(body, dict) else body
            detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            detail = str(detail)
        except ValueError:
            detail = response.text
        detail = " ".join(detail.split())[:240]
        if api_key:
            detail = detail.replace(api_key, "***")
        return "；".join(part for part in (f"HTTP {response.status_code}", hint, detail) if part)

    def _target(self, kind: ModelKind, config: dict[str, Any]) -> str:
        base = str(config.get("base_url") or "").rstrip("/")
        if kind == "language":
            return f"{base}/chat/completions" if base else ""
        endpoint = str(config.get("endpoint") or "").strip()
        if endpoint:
            return endpoint
        if not base:
            return ""
        # 与 util/InformationDB.py 的 EmbeddingWorker 拼接规则保持一致。
        return f"{base}/api/embed" if self._provider(config) == "ollama" else f"{base}/embeddings"

    @staticmethod
    def _provider(config: dict[str, Any]) -> str:
        return "ollama" if str(config.get("provider") or "openai").strip().lower() in {"ollama", "local"} else "openai"

    @staticmethod
    def _stored_key(config: dict[str, Any]) -> str:
        value = config.get("api_key")
        if isinstance(value, list):
            value = next((item for item in value if str(item).strip()), "")
        return str(value or "").strip()

    # 最近一次测试只对同一组地址/模型有效；修改配置后自动失效。
    def _fingerprint(self, kind: ModelKind, config: dict[str, Any]) -> str:
        identity = [self._target(kind, config), config.get("model_name"), self._stored_key(config)]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def _tests_path(self) -> Path:
        return self.settings.registry_path.parent / "model_tests.json"

    def _last_test(self, kind: ModelKind, config: dict[str, Any]) -> dict[str, Any] | None:
        try:
            saved = json.loads(self._tests_path().read_text(encoding="utf-8")).get(kind)
        except (FileNotFoundError, ValueError):
            return None
        if not saved or saved.get("fingerprint") != self._fingerprint(kind, config):
            return None
        return {key: value for key, value in saved.items() if key != "fingerprint"}

    def _save_test(self, kind: ModelKind, config: dict[str, Any], result: dict[str, Any]) -> None:
        path = self._tests_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        data[kind] = {**result, "fingerprint": self._fingerprint(kind, config)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path(self, kind: ModelKind) -> Path:
        return self.settings.project_path(Path("config") / _FILES[kind])

    def _read(self, kind: ModelKind) -> dict[str, Any]:
        path = self._path(kind)
        if not path.is_file():
            return {}
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ValueError(f"{_FILES[kind]} 不是有效的 YAML 配置") from error
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{_FILES[kind]} 必须是键值配置")
        return value

    def _write(self, kind: ModelKind, config: dict[str, Any]) -> None:
        path = self._path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A same-directory replace avoids leaving a partly-written credential file.
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                yaml.safe_dump(config, output, allow_unicode=True, sort_keys=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _text(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _number(value: Any) -> int | None:
        return value if isinstance(value, int | float) and not isinstance(value, bool) else None

    @staticmethod
    def _validate(kind: ModelKind, config: dict[str, Any]) -> None:
        model_name = str(config.get("model_name") or "").strip()
        if not model_name:
            raise ValueError("模型名称不能为空")
        if len(model_name) > 200:
            raise ValueError("模型名称不能超过 200 个字符")
        for name in ("base_url", "endpoint"):
            value = config.get(name)
            if value in (None, ""):
                continue
            parsed = urlparse(str(value))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{name} 必须是完整的 http(s) 地址")
        for name in ("timeout", "max_retries"):
            if name not in config:
                continue
            if not isinstance(config[name], int | float) or isinstance(config[name], bool) or config[name] < 0:
                raise ValueError(f"{name} 必须是非负数字")
        # Agent.py 只支持 OpenAI 兼容接口。
        if kind == "language" and str(config.get("api_type") or "openai") not in {"openai", "openrouter", "vllm"}:
            raise ValueError("不支持的语言模型 API 类型（仅支持 OpenAI 兼容接口）")
