"""Agent timeouts, real failure stages and LLM retry reporting."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import openai

from simulation.runtime import EventStore, RuntimeObserver, RuntimeSnapshotReducer
from simulation.runtime.agent_run import AgentRun
from simulation.engine import run_agents_for_day
from simulation.tests.test_agent import REQUEST, completion, make_agent, status_error

DATE = "2023-06-15"


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.folder.name) / "events.jsonl")
        self.observer = RuntimeObserver("run-test", self.store)
        self.release = threading.Event()  # 解除被放弃线程的阻塞，避免测试进程退出时挂起

    def tearDown(self):
        self.release.set()
        self.folder.cleanup()

    def snapshot(self):
        reducer = RuntimeSnapshotReducer("run-test")
        reducer.apply_all(self.store.after(0))
        return reducer.as_dict()

    def agent(self, agent_id):
        return next(item for item in self.snapshot()["agents"] if item["agent_id"] == agent_id)

    def test_exception_is_recorded_at_the_stage_it_happened(self):
        def task(user_id, run):
            run.begin()
            run.start("state"); run.complete("state")
            run.start("perception")
            try:
                raise KeyError("missing news row")
            except KeyError as error:
                run.fail(error)
            return user_id, {"error": "x"}, None, None

        run_agents_for_day(["7"], task, self.observer, DATE, max_workers=1, agent_timeout=5)

        agent = self.agent("7")
        self.assertEqual(agent["status"], "failed")
        self.assertEqual(agent["error"]["stage"], "perception")
        self.assertEqual(agent["error"]["code"], "KeyError")
        self.assertEqual(agent["stages"]["commit"]["status"], "pending")
        self.assertEqual(self.snapshot()["agent_counts"]["failed"], 1)

    def test_hung_agent_times_out_is_retried_once_and_then_given_up(self):
        def task(user_id, run):
            run.begin()
            run.start("interpretation")
            self.release.wait(10)  # 模拟永不返回的 LLM 调用
            run.complete("interpretation")  # 被放弃后不应再出现
            return user_id, {}, {"late": True}, None

        started = time.monotonic()
        outcomes = run_agents_for_day(["1", "2"], task, self.observer, DATE, max_workers=4, agent_timeout=0.3)

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(sorted(user for user, *_ in outcomes), ["1", "2"])
        self.assertTrue(all(decision is None for _, _, decision, _ in outcomes))
        agent = self.agent("1")
        self.assertEqual(agent["status"], "failed")
        self.assertEqual(agent["attempt"], 2)
        self.assertEqual(agent["error"]["stage"], "interpretation")
        self.assertEqual(agent["error"]["code"], "AgentTimeout")
        # 线程放行后写出的事件必须被屏蔽。
        self.release.set()
        time.sleep(0.2)
        self.assertFalse(any(e.event.value == "stage_completed" and e.stage == "interpretation" for e in self.store.after(0)))

    def test_retry_after_timeout_can_succeed(self):
        calls = []

        def task(user_id, run):
            run.begin()
            calls.append(run.attempt)
            run.start("interpretation")
            if run.attempt == 1:
                self.release.wait(10)
            run.complete("interpretation")
            run.start("commit"); run.complete("commit")
            return user_id, {}, {"ok": True}, None

        outcomes = run_agents_for_day(["9"], task, self.observer, DATE, max_workers=2, agent_timeout=0.3)

        self.assertEqual(outcomes, [("9", {}, {"ok": True}, None)])
        self.assertEqual(calls, [1, 2])
        agent = self.agent("9")
        self.assertEqual(agent["status"], "completed")
        self.assertIsNone(agent["error"])

    def test_queued_agents_do_not_time_out_while_waiting_for_a_worker(self):
        def task(user_id, run):
            run.begin()
            time.sleep(0.2)
            return user_id, {}, {"ok": True}, None

        outcomes = run_agents_for_day(["a", "b", "c"], task, self.observer, DATE, max_workers=1, agent_timeout=0.35)

        self.assertTrue(all(decision == {"ok": True} for _, _, decision, _ in outcomes))

    def test_llm_retry_is_reported_on_the_current_stage(self):
        agent = make_agent()
        timeout = openai.APITimeoutError(request=REQUEST)
        agent.client.chat.completions.create = Mock(side_effect=[timeout, completion()])
        run = AgentRun(self.observer, "5", DATE)
        run.begin()
        run.start("interpretation")

        with run.bound():
            agent._BaseAgent__call_api([{"role": "user", "content": "hi"}], max_tokens=9)

        state = self.agent("5")
        self.assertEqual(state["status"], "retrying")
        self.assertEqual(state["stages"]["interpretation"]["substage"], "llm")
        self.assertEqual(state["stages"]["interpretation"]["error_summary"], "Request timed out.")

    def test_swallowed_llm_failure_is_named_in_the_later_error(self):
        agent = make_agent()
        failure = status_error(openai.InternalServerError, 502, "Error code: 502")
        agent.client.chat.completions.create = Mock(side_effect=failure)
        run = AgentRun(self.observer, "8", DATE)
        run.begin()
        run.start("interpretation")

        with run.bound():
            with self.assertRaises(openai.InternalServerError):
                agent._BaseAgent__call_api([{"role": "user", "content": "hi"}], max_tokens=9)
            run.fail(TypeError("expected string or bytes-like object, got 'NoneType'"))

        error = self.agent("8")["error"]
        self.assertEqual(error["code"], "TypeError")
        self.assertIn("502", error["summary"])

    def test_new_simulation_day_resets_the_agent_pipeline(self):
        AgentRun(self.observer, "3", DATE).fail(RuntimeError("day one"))
        run = AgentRun(self.observer, "3", "2023-06-16")
        run.start("state")

        state = self.agent("3")
        self.assertEqual(state["simulation_date"], "2023-06-16")
        self.assertEqual(state["status"], "running")
        self.assertIsNone(state["error"])


if __name__ == "__main__":
    unittest.main()
