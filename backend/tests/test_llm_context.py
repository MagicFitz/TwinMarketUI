"""LLM context recording per Agent call and the backend reader."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from server.services.llm_context import agent_llm_context
from runtime.agent_run import AgentRun
from tests.test_agent import completion, make_agent

DATE = "2023-06-15"


def _make_final_decision(agent, messages, **kwargs):
    """Named like the trading_agent step so caller detection can be checked."""
    return agent.get_response(messages=messages, **kwargs)


class LLMContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.folder.name)
        self.run = AgentRun(None, "42", DATE, context_dir=self.run_dir / "llm_context" / DATE)
        self.run.begin()
        self.run.start("interpretation")

    def tearDown(self):
        self.folder.cleanup()

    def read(self, **kwargs):
        return agent_llm_context(self.run_dir, "42", **kwargs)

    def test_each_call_records_step_messages_and_new_content(self):
        agent = make_agent(context_window=4000, max_output_tokens=200)
        agent.client.chat.completions.create = Mock(return_value=completion("买入"))
        history = [{"role": "user", "content": "今天的新闻"}]

        with self.run.bound():
            _make_final_decision(agent, history)
            _make_final_decision(agent, history + [{"role": "assistant", "content": "买入"},
                                                   {"role": "user", "content": "请给出订单"}])

        record = self.read()["attempts"][0]
        first, second = record["calls"]
        self.assertEqual(first["step"], "_make_final_decision")
        self.assertEqual(first["stage"], "interpretation")
        self.assertEqual(len(first["message_ids"]), 2)  # 补上的 system + 用户消息
        self.assertEqual(first["new_ids"], first["message_ids"])
        self.assertEqual(len(second["message_ids"]), 4)
        self.assertEqual(len(second["new_ids"]), 1)  # 只有“请给出订单”是新的；回复“买入”在上一轮已作为回复出现过
        self.assertGreater(second["est_input_tokens"], first["est_input_tokens"])
        self.assertEqual(record["messages"][first["reply_id"]]["content"], "买入")
        self.assertEqual(record["context_window"], 4000)
        # 消息文本只存一份
        self.assertEqual(len(record["messages"]), 4)

    def test_context_overflow_is_recorded_as_a_failed_call(self):
        agent = make_agent(context_window=100, max_output_tokens=40)
        agent.client.chat.completions.create = Mock()

        with self.run.bound():
            result = agent.get_response(user_input="x" * 2000)

        self.assertIn("上下文窗口", result["error"])
        call = self.read()["attempts"][0]["calls"][0]
        self.assertEqual(call["status"], "error")
        self.assertIn("上下文窗口", call["error"])
        agent.client.chat.completions.create.assert_not_called()

    def test_retry_attempts_are_kept_separately(self):
        agent = make_agent(context_window=4000)
        agent.client.chat.completions.create = Mock(return_value=completion())
        retry = self.run.next_attempt()
        retry.begin()
        for run in (self.run, retry):
            with run.bound():
                agent.get_response(user_input="hi")

        attempts = self.read()["attempts"]
        self.assertEqual([item["attempt"] for item in attempts], [1, 2])

    def test_reader_rejects_path_like_ids_and_handles_missing_data(self):
        with self.assertRaises(ValueError):
            agent_llm_context(self.run_dir, "../etc")
        with self.assertRaises(ValueError):
            agent_llm_context(self.run_dir, "42", date="../../x")
        self.assertEqual(agent_llm_context(self.run_dir, "7"), {"agent_id": "7", "date": None, "dates": [], "attempts": []})


if __name__ == "__main__":
    unittest.main()
