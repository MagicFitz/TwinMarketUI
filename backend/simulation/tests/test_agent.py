"""Unit tests for the OpenAI-compatible LLM request boundary.

These use a fake client: the "[API错误] ..." lines printed while they run are
the simulated failures being exercised, not real network calls.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import httpx2
import openai

from simulation.agent import BaseAgent

REQUEST = httpx2.Request("POST", "http://llm.test/v1/chat/completions")


def status_error(cls, status, message):
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def make_agent(**attributes):
    agent = BaseAgent.__new__(BaseAgent)
    agent.model_name = "test-model"
    agent.default_system_prompt = "system"
    agent.max_retries = 3
    agent.retry_delay = 0
    agent.context_window = 100
    agent.max_output_tokens = 40
    agent._log_api_event = Mock()
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    for name, value in attributes.items():
        setattr(agent, name, value)
    return agent


def completion(text="ok"):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class RequestBoundaryTests(unittest.TestCase):
    def test_max_tokens_is_limited_by_context_and_model_output(self):
        agent = make_agent()
        effective, input_tokens = agent._effective_max_tokens(
            [{"role": "user", "content": "x" * 100}], 999
        )
        self.assertGreater(input_tokens, 0)
        self.assertLessEqual(effective, 40)
        self.assertLessEqual(effective, 100 - input_tokens - 32)

    def test_context_limit_fails_before_a_provider_request(self):
        agent = make_agent()
        with self.assertRaisesRegex(ValueError, "上下文窗口"):
            agent._effective_max_tokens([{"role": "user", "content": "x" * 1000}], 1)

    def test_request_carries_clamped_max_tokens(self):
        agent = make_agent()
        create = Mock(return_value=completion())
        agent.client.chat.completions.create = create

        agent._BaseAgent__call_api([{"role": "user", "content": "hi"}], max_tokens=9)

        self.assertEqual(create.call_args.kwargs["max_tokens"], 9)
        self.assertEqual(create.call_args.kwargs["model"], "test-model")

    def test_deterministic_400_and_auth_errors_are_not_retried(self):
        for error in (status_error(openai.BadRequestError, 400, "invalid parameter"),
                      status_error(openai.AuthenticationError, 401, "invalid api key")):
            agent = make_agent()
            create = Mock(side_effect=error)
            agent.client.chat.completions.create = create
            with self.assertRaises(type(error)):
                agent._BaseAgent__call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(create.call_count, 1)

    def test_rate_limit_and_server_errors_are_retried(self):
        for error in (status_error(openai.RateLimitError, 429, "rate limited"),
                      status_error(openai.InternalServerError, 502, "bad gateway")):
            agent = make_agent(max_retries=2)
            create = Mock(side_effect=[error, completion()])
            agent.client.chat.completions.create = create
            agent._BaseAgent__call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(create.call_count, 2)

    def test_timeout_and_connection_errors_are_retried(self):
        for error in (openai.APITimeoutError(request=REQUEST), openai.APIConnectionError(request=REQUEST)):
            agent = make_agent(max_retries=2)
            create = Mock(side_effect=[error, completion()])
            agent.client.chat.completions.create = create
            agent._BaseAgent__call_api([{"role": "user", "content": "hi"}])
            self.assertEqual(create.call_count, 2)

    def test_get_response_adds_system_prompt_without_mutating_history(self):
        agent = make_agent()
        create = Mock(return_value=completion("你好"))
        agent.client.chat.completions.create = create
        history = [{"role": "user", "content": "hi"}]

        result = agent.get_response(messages=history)

        self.assertEqual(result["response"], "你好")
        self.assertEqual(create.call_args.kwargs["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(history, [{"role": "user", "content": "hi"}])

    def test_get_response_returns_error_dict_after_final_failure(self):
        agent = make_agent(max_retries=1)
        agent.client.chat.completions.create = Mock(side_effect=status_error(openai.AuthenticationError, 401, "invalid api key"))

        self.assertIn("invalid api key", agent.get_response(user_input="hi")["error"])


if __name__ == "__main__":
    unittest.main()
