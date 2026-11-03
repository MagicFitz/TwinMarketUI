"""Legacy start-date alignment, whole-day Agent failure, reclassification and retry."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.process_manager import ProcessManager
from server.registry import ConflictError, RunRegistry
from server.run_service import create_run
from server.runtime_service import reclassify_completed_runs, runtime_snapshot, store_for
from server.settings import Settings
from runtime import EventStore, RuntimeObserver
from runtime.event import RuntimeEvent
from runtime.outcome import first_all_failed_day
from runtime.schemas import EventType, Scope
from script.prepare_simulation_run import prepare_database

ROOT = Path(__file__).resolve().parents[2]


def agent_event(event_id, event, day, agent, stage="commit", error=None):
    return RuntimeEvent(event_id, "r", event, Scope.AGENT, stage, simulation_date=day, agent_id=agent, error_summary=error)


class OutcomeRuleTests(unittest.TestCase):
    def test_first_day_without_any_working_agent(self):
        events = [agent_event(1, EventType.COMPLETED, "2023-06-19", "a"),
                  agent_event(2, EventType.FAILED, "2023-06-19", "b", "state", "boom"),
                  agent_event(3, EventType.FAILED, "2023-06-20", "a", "state", "'user_id'"),
                  agent_event(4, EventType.FAILED, "2023-06-20", "b", "state", "'user_id'")]
        self.assertEqual(first_all_failed_day(events), "2023-06-20 全部 2 个 Agent 处理失败。示例错误：'user_id'")
        self.assertIsNone(first_all_failed_day(events[:2]))
        # A timed-out Agent that succeeded on its retry counts as working.
        retried = [agent_event(1, EventType.FAILED, "2023-06-19", "a", "state", "timeout"),
                   agent_event(2, EventType.COMPLETED, "2023-06-19", "a")]
        self.assertIsNone(first_all_failed_day(retried))


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        data = self.root / "data"
        data.mkdir()
        for name in ("stock_data.csv", "trading_days.csv", "sorted_impact_news.pkl", "sys_1000.db"):
            shutil.copyfile(ROOT / "data" / name, data / name)
        (self.root / "config").mkdir()
        (self.root / "config" / "api.yaml").write_text("model_name: fixture\n")
        (self.root / "util" / "belief").mkdir(parents=True)
        shutil.copyfile(ROOT / "util" / "belief" / "belief_1000_0129.csv", self.root / "util" / "belief" / "belief_1000_0129.csv")
        self.settings = Settings(self.root, data, self.root / "runs", self.root / "server" / "data" / "runs.sqlite3")
        self.registry = RunRegistry(self.settings.registry_path)

    def tearDown(self):
        self.temporary.cleanup()

    def legacy_run(self, start="2023-06-19", end="2023-06-20"):
        return create_run(self.registry, {"name": "legacy", "config": {"start_date": start, "end_date": end}}, self.settings)

    def finish(self, run, status, events):
        """Record a finished attempt: events plus the registry state the worker would write."""
        store = store_for(run)
        for item in events:
            store.append(item)
        self.registry.transition(run["run_id"], "queued")
        self.registry.transition(run["run_id"], "running")
        return self.registry.transition(run["run_id"], status, health="degraded")


class LegacyAlignmentTests(RunFixture):
    def test_late_start_moves_accounts_and_history_to_the_day_before(self):
        output = self.root / "late.db"
        prepare_database(self.settings.data_path("sys_1000.db"), output, 2, start_date="2023-06-19",
                         legacy_history=self.settings.data_dir)
        with sqlite3.connect(output) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT DISTINCT created_at FROM Profiles")}, {"2023-06-18 00:00:00"})
            self.assertEqual(db.execute("SELECT max(date) FROM StockData").fetchone()[0], "2023-06-16")

    def test_default_start_keeps_the_template_untouched(self):
        output, original = self.root / "default.db", self.settings.data_path("sys_1000.db")
        prepare_database(original, output, 2, start_date="2023-06-15", legacy_history=self.settings.data_dir)
        with sqlite3.connect(output) as db, sqlite3.connect(original) as template:
            self.assertEqual({row[0] for row in db.execute("SELECT DISTINCT created_at FROM Profiles")}, {"2023-06-14 00:00:00"})
            self.assertEqual(db.execute("SELECT count(*) FROM StockData").fetchone(),
                             template.execute("SELECT count(*) FROM StockData").fetchone())

    def test_creation_refuses_dates_the_builtin_files_cannot_cover(self):
        with self.assertRaisesRegex(ValueError, "至少需要 30 个"):
            self.legacy_run("2023-01-10", "2023-01-11")
        with self.assertRaisesRegex(ValueError, "只覆盖到 2025-01-10"):
            self.legacy_run("2024-12-30", "2025-01-15")

    def test_worker_passes_the_legacy_history_and_reports_preparation_errors(self):
        from server.worker import run_worker
        run = self.legacy_run()
        self.registry.transition(run["run_id"], "queued")
        failing = SimpleNamespace(returncode=1, stdout="", stderr="Traceback\nValueError: 历史不足\n")
        with patch("server.worker.subprocess.run", return_value=failing) as execute:
            self.assertEqual(run_worker(run["run_id"], settings=self.settings), 1)
        arguments = execute.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--legacy-history") + 1], str(self.settings.data_dir))
        self.assertEqual(arguments[arguments.index("--start-date") + 1], "2023-06-19")
        self.assertEqual(self.registry.get(run["run_id"])["failure_reason"], "运行准备失败：ValueError: 历史不足")


class FailureStatusTests(RunFixture):
    def test_simulation_failure_message_and_checkpoint_reach_the_registry(self):
        from server.worker import run_worker
        run = self.legacy_run()
        self.registry.transition(run["run_id"], "queued")
        folder = Path(run["output_dir"])

        def simulate(command, **kwargs):
            if "simulation.py" in command[1]:
                RuntimeObserver(run["run_id"], store_for(run)).failed(
                    Scope.DAY, "agents", simulation_date="2023-06-20", error_code="AllAgentsFailed",
                    error_summary="运行已停止：2023-06-20 全部 10 个 Agent 处理失败")
                (folder / "state.json").write_text(json.dumps({"checkpoint": {"last_completed_date": "2023-06-19", "next_date": "2023-06-20"}}))
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch("server.worker.subprocess.run", side_effect=simulate):
            self.assertEqual(run_worker(run["run_id"], settings=self.settings), 1)
        failed = self.registry.get(run["run_id"])
        self.assertEqual((failed["status"], failed["health"]), ("failed", "degraded"))
        self.assertEqual(failed["failure_reason"], "运行已停止：2023-06-20 全部 10 个 Agent 处理失败")
        self.assertEqual(failed["checkpoint"]["last_completed_date"], "2023-06-19")

    def test_completed_runs_with_a_dead_day_are_reclassified_once(self):
        broken, partial = self.legacy_run(), self.legacy_run()
        self.finish(broken, "completed", [agent_event(0, EventType.FAILED, "2023-06-19", "a", "state", "'user_id'")])
        self.finish(partial, "completed", [agent_event(0, EventType.FAILED, "2023-06-19", "a", "state", "x"),
                                           agent_event(0, EventType.COMPLETED, "2023-06-19", "b")])
        self.assertEqual(reclassify_completed_runs(self.registry), [broken["run_id"]])
        self.assertEqual(self.registry.get(broken["run_id"])["status"], "failed")
        self.assertIn("全部 1 个 Agent 处理失败", self.registry.get(broken["run_id"])["failure_reason"])
        self.assertEqual(self.registry.get(partial["run_id"])["status"], "completed")
        self.assertEqual(reclassify_completed_runs(self.registry), [])


class RetryTests(RunFixture):
    def manager(self):
        return ProcessManager(self.registry, self.settings)

    def test_failed_run_without_progress_restarts_and_archives_the_attempt(self):
        run = self.legacy_run()
        folder = Path(run["output_dir"])
        (folder / "user_10.db").write_text("broken prepared data")
        self.finish(run, "failed", [agent_event(0, EventType.FAILED, "2023-06-19", "a", "state", "'user_id'")])
        last_event = store_for(run).last_id
        with patch("server.process_manager.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())) as launch:
            retried = self.manager().retry(run["run_id"])
        self.assertEqual(retried["status"], "queued")
        self.assertNotIn("--resume", launch.call_args.args[0])
        attempts = list((folder / "attempts").iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertTrue((attempts[0] / "user_10.db").is_file())
        self.assertFalse((folder / "user_10.db").exists())
        self.assertTrue((folder / "manifest.json").is_file())
        self.assertEqual(json.loads((folder / "state.json").read_text())["checkpoint"]["next_date"], "2023-06-19")
        events = store_for(run).after(0)
        self.assertEqual([(e.event_id, e.event) for e in events], [(last_event + 1, EventType.RETRYING)])
        self.assertIsNone(first_all_failed_day(events))
        self.assertEqual(runtime_snapshot(self.registry.get(run["run_id"]))["agents"], [])
        self.assertIsNone(self.registry.get(run["run_id"])["failure_reason"])

    def test_auto_resumes_after_a_committed_day_and_restart_can_be_forced(self):
        run = self.legacy_run()
        folder = Path(run["output_dir"])
        for name in ("user_10.db", "forum_10.db", "belief_10.csv"):
            (folder / name).write_text("x")
        self.finish(run, "failed", [])
        self.registry.update(run["run_id"], checkpoint_json=json.dumps({"last_completed_date": "2023-06-19", "next_date": "2023-06-20"}))
        with patch("server.process_manager.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())) as launch:
            self.manager().retry(run["run_id"])
        self.assertIn("--resume", launch.call_args.args[0])
        self.registry.transition(run["run_id"], "cancelled")
        with patch("server.process_manager.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())) as launch:
            self.manager().retry(run["run_id"], "restart")
        self.assertNotIn("--resume", launch.call_args.args[0])
        self.assertTrue((folder / "attempts").is_dir())

    def test_retry_api_rejects_other_states_and_modes(self):
        run = self.legacy_run()
        client = TestClient(create_app(self.settings, registry=self.registry))
        self.assertEqual(client.post(f"/api/runs/{run['run_id']}/retry").status_code, 409)
        self.assertEqual(client.post(f"/api/runs/{run['run_id']}/retry", json={"mode": "later"}).status_code, 422)
        with self.assertRaises(ConflictError):
            self.manager().retry(run["run_id"])


class SimulationStopsTests(unittest.TestCase):
    """The real simulation loop stops on the first day where every Agent failed."""

    def test_all_agents_failing_stops_before_the_checkpoint(self):
        from script.prepare_simulation_run import write_beliefs
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            selected = prepare_database(ROOT / "data" / "sys_1000.db", folder / "users.db", 1, start_date="2023-06-19",
                                        legacy_history=ROOT / "data")
            beliefs = folder / "beliefs.csv"
            beliefs.write_text("user_id,belief\n" + selected[0] + ",观察市场\n")
            write_beliefs(beliefs, folder / "belief.csv", selected)
            (folder / "state.json").write_text(json.dumps({"status": "draft", "checkpoint": {}}))
            env = {**os.environ, "TWINMARKET_DATA_DIR": str(ROOT / "data"), "TWINMARKET_MARKET_BUNDLE_ID": "test",
                   "SMOKE_BREAK_AGENTS": "1", "SMOKE_START": "2023-06-19", "SMOKE_END": "2023-06-20",
                   "MPLCONFIGDIR": str(folder / "matplotlib"), "PYTHONPATH": str(ROOT)}
            result = subprocess.run([sys.executable, "-m", "server.tests.market_smoke_runner", str(folder)], cwd=ROOT,
                                    env=env, text=True, capture_output=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AllAgentsFailed", result.stderr)
            events = EventStore(folder / "events.jsonl").after(0, 100000)
            stop = [e for e in events if e.scope == Scope.DAY and e.event == EventType.FAILED]
            self.assertEqual([(e.simulation_date, e.error_code) for e in stop], [("2023-06-19", "AllAgentsFailed")])
            self.assertIn("运行已停止：2023-06-19 全部 1 个 Agent 处理失败", stop[0].error_summary)
            self.assertFalse(json.loads((folder / "state.json").read_text())["checkpoint"].get("last_completed_date"))
            self.assertFalse(any(e.simulation_date == "2023-06-20" for e in events))


if __name__ == "__main__":
    unittest.main()
