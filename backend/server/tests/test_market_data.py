"""Run creation against the market data service: plan, pinned bundle, offline start and legacy snapshots."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.legacy_snapshots import fingerprint
from server.market_bundle import MarketInputError, MdsClient, unsupported_rules
from server.registry import RunRegistry
from server.run_service import create_run, validate_start
from server.settings import Settings
from market_service.templates import publish
from market_service.tests.fixtures import ROOT, SECRET, ServiceFixture
from script.market_inputs import EmptyNewsIndex, news_for_date, open_trading_dates
from script.prepare_simulation_run import STOCK_COLUMNS, prepare_database


def offline(*_):
    raise MarketInputError("MDS_UNAVAILABLE", "无法连接市场数据服务", 503)


class RunMarketFixture(ServiceFixture):
    def setUp(self):
        super().setUp()
        (self.root / "config" / "api.yaml").write_text("model_name: fixture\n")
        (self.root / "util" / "belief").mkdir(parents=True)
        (self.root / "util" / "belief" / "belief_1000_0129.csv").write_text("user_id,belief\n1,0.5\n")
        with sqlite3.connect(self.seed_dir / "sys_1000.db") as db:
            db.executescript("CREATE TABLE Profiles(user_id TEXT,created_at TEXT); CREATE TABLE Strategy(user_id TEXT,strategy TEXT); CREATE TABLE TradingDetails(user_id TEXT);")
            db.execute("INSERT INTO Profiles VALUES ('1','2023-06-14 00:00:00')")
            db.execute("INSERT INTO Strategy VALUES ('1','hold')")
            db.execute("CREATE TABLE StockData(" + ",".join(column + " " + ("TEXT" if column in {"stock_id", "date"} else "REAL") for column in STOCK_COLUMNS) + ")")
        self.settings = Settings(self.root, self.seed_dir, self.root / "runs", self.root / "server" / "data" / "runs.sqlite3")
        self.mds = TestClient(self.mds_app())
        self.sync()
        self.transport = self.in_process
        self.clients = [patch(target, lambda settings: MdsClient(lambda *args: self.transport(*args)))
                        for target in ("server.run_service.mds_client", "server.app.main.mds_client")]
        for item in self.clients:
            item.start()
        self.registry = RunRegistry(self.settings.registry_path)

    def tearDown(self):
        for item in self.clients:
            item.stop()
        super().tearDown()

    def in_process(self, method, path, body):
        response = self.mds.request(method, path, json=body)
        return response.status_code, response.content

    def template_run(self, **config):
        return create_run(self.registry, {"name": "template-run", "config": {
            "market_template": "cn-a-industry10", "start_date": "2025-01-13", "end_date": "2025-01-14", **config}}, self.settings)


class TemplateRunTests(RunMarketFixture):
    def test_run_pins_template_version_data_version_and_bundle(self):
        run = self.template_run()
        config, folder = run["config"], Path(run["output_dir"])
        self.assertEqual((config["market_data_mode"], config["market_template"], config["data_version"]),
                         ("template", "cn-a-industry10@1", 2))
        manifest = json.loads((folder / "manifest.json").read_text())
        environment = manifest["market_environment"]
        self.assertEqual(environment["template"], "cn-a-industry10@1")
        self.assertEqual(environment["data_version"], 2)
        self.assertEqual(manifest["inputs"]["market_stock_data"]["sha256"], environment["files"]["stock_data.csv"]["sha256"])
        self.assertTrue(manifest["research_warnings"])
        self.assertEqual(self.mds.get(f"/v1/bundles/{environment['bundle_id']}").json()["refs"], [run["run_id"]])
        news = pd.read_pickle(folder / "market" / "sorted_impact_news.pkl")
        self.assertEqual(news_for_date(news, pd.Timestamp("2025-01-13")), ["周一新闻甲", "周一新闻乙"])
        self.assertEqual(validate_start(run, settings=self.settings), [])

    def test_later_revision_does_not_change_a_pinned_run_and_copies_keep_the_version(self):
        run = self.template_run()
        before = fingerprint(Path(run["output_dir"]) / "market" / "stock_data.csv")
        from market_service.tests.fixtures import FakeTushare
        self.assertEqual(self.sync(FakeTushare(self.seed_dir, close=103))["data_version"], 3)
        self.assertEqual(fingerprint(Path(run["output_dir"]) / "market" / "stock_data.csv"), before)
        client = TestClient(create_app(self.settings))
        copied = client.post(f"/api/runs/{run['run_id']}/copy").json()
        self.assertEqual(copied["config"]["data_version"], 2)
        self.assertEqual(self.template_run()["config"]["data_version"], 3)

    def test_draft_edit_rebundles_and_delete_releases_the_reference(self):
        client = TestClient(create_app(self.settings))
        body = {"name": "api-run", "config": {"market_template": "cn-a-industry10", "start_date": "2025-01-13", "end_date": "2025-01-14"}}
        created = client.post("/api/runs", json=body)
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["run_id"]
        first = json.loads((self.settings.runs_dir / run_id / "manifest.json").read_text())["market_environment"]["bundle_id"]
        body["config"]["start_date"] = "2025-01-14"
        self.assertEqual(client.patch(f"/api/runs/{run_id}", json=body).status_code, 200)
        second = json.loads((self.settings.runs_dir / run_id / "manifest.json").read_text())["market_environment"]["bundle_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(client.get(f"/api/runs/{run_id}").json()["checkpoint"]["next_date"], "2025-01-14")
        self.assertEqual(client.delete(f"/api/runs/{run_id}").status_code, 204)
        self.assertEqual(self.mds.get(f"/v1/bundles/{second}").json()["refs"], [])

    def test_blocked_plan_snapshot_mode_and_unsupported_rules_are_refused(self):
        client = TestClient(create_app(self.settings))
        blocked = client.post("/api/runs", json={"name": "x", "config": {"market_template": "cn-a-industry10",
                                                                          "start_date": "2025-01-13", "end_date": "2025-01-15"}})
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "PLAN_BLOCKED"))
        snapshot = client.post("/api/runs", json={"name": "x", "config": {"market_snapshot_id": "market-old"}})
        self.assertEqual(snapshot.status_code, 422)
        body = self.service.registry.template("cn-a-industry10")["body"]
        publish(self.service.registry, {**body, "rules": {**body["rules"], "price_limit_pct": 20}})
        unsupported = client.post("/api/runs", json={"name": "x", "config": {"market_template": "cn-a-industry10@2",
                                                                              "start_date": "2025-01-13", "end_date": "2025-01-14"}})
        self.assertEqual(unsupported.json()["code"], "RULE_UNSUPPORTED")
        self.assertIn("price_limit_pct=20", unsupported.json()["error"])
        self.assertEqual(unsupported_rules(body), [])
        self.assertFalse(self.settings.runs_dir.exists() and any(self.settings.runs_dir.iterdir()))

    def test_service_offline_blocks_creation_but_not_start(self):
        run = self.template_run()
        self.transport = offline
        client = TestClient(create_app(self.settings))
        created = client.post("/api/runs", json={"name": "x", "config": {"market_template": "cn-a-industry10",
                                                                          "start_date": "2025-01-13", "end_date": "2025-01-14"}})
        self.assertEqual((created.status_code, created.json()["code"]), (503, "MDS_UNAVAILABLE"))
        self.assertEqual(validate_start(run, settings=self.settings), [])
        stock = Path(run["output_dir"]) / "market" / "stock_data.csv"
        stock.chmod(0o644)
        stock.write_text("tampered")
        self.assertEqual(validate_start(run, settings=self.settings), ["运行数据包的文件指纹不匹配"])

    def test_worker_passes_bundle_without_any_credential(self):
        from server.worker import run_worker
        run = self.template_run()
        self.registry.transition(run["run_id"], "queued")
        self.transport = offline
        secrets = {"TUSHARE_TOKEN": SECRET, "TWINMARKET_MDS_TOKEN": "service-secret"}
        with patch.dict(os.environ, secrets), patch("server.worker.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as execute:
            self.assertEqual(run_worker(run["run_id"], settings=self.settings), 0)
        prepare, simulate = execute.call_args_list
        self.assertIn("--market-snapshot", prepare.args[0])
        for call in (prepare, simulate):
            env = call.kwargs["env"]
            self.assertFalse(set(secrets) & set(env))
            self.assertEqual(env["TWINMARKET_DATA_DIR"], str(Path(run["output_dir"]) / "market"))
            self.assertEqual(env["TWINMARKET_RULES"], str(Path(run["output_dir"]) / "market" / "rules.json"))
            self.assertTrue(env["TWINMARKET_MARKET_BUNDLE_ID"].startswith("b-"))
            self.assertNotIn("TWINMARKET_MARKET_SNAPSHOT_ID", env)

    def test_private_history_is_prepared_from_the_bundle(self):
        run = self.template_run()
        private = Path(run["output_dir"]) / "user.db"
        prepare_database(self.seed_dir / "sys_1000.db", private, 1, market_snapshot=Path(run["output_dir"]) / "market",
                         start_date="2025-01-13")
        with sqlite3.connect(private) as db:
            self.assertEqual(db.execute("SELECT max(date) FROM StockData").fetchone()[0], "2025-01-10")
            self.assertGreaterEqual(db.execute("SELECT count(DISTINCT date) FROM StockData").fetchone()[0], 30)
            self.assertEqual(db.execute("SELECT created_at FROM Profiles").fetchone()[0], "2025-01-12 00:00:00")


class LegacySnapshotRunTests(RunMarketFixture):
    def test_existing_snapshot_runs_still_verify_and_start_offline(self):
        snapshot_id = "market-20250114-legacy"
        folder = self.seed_dir / "market_snapshots" / snapshot_id
        folder.mkdir(parents=True)
        for name in ("stock_data.csv", "trading_days.csv"):
            shutil.copyfile(self.seed_dir / name, folder / name)
        shutil.copyfile(self.seed_dir / "sorted_impact_news.pkl", folder / "sorted_impact_news.pkl")
        output = self.settings.runs_dir / "old-run"
        output.mkdir(parents=True)
        files = {name: fingerprint(folder / name) for name in ("stock_data.csv", "trading_days.csv", "sorted_impact_news.pkl")}
        (output / "manifest.json").write_text(json.dumps({"market_environment": {"snapshot_id": snapshot_id, "files": files},
                                                          "research_warnings": ["无新闻"]}))
        config = {"user_count": 1, "start_date": "2025-01-13", "end_date": "2025-01-14", "template_db": "data/sys_1000.db",
                  "belief_source": "util/belief/belief_1000_0129.csv", "config_path": "config/api.yaml",
                  "market_data_mode": "snapshot", "market_snapshot_id": snapshot_id, "allow_missing_news": True}
        run = self.registry.create("old-run", "old", "", config, output)
        self.transport = offline
        self.assertEqual(validate_start(run, settings=self.settings), [])
        (folder / "stock_data.csv").write_text("tampered")
        self.assertEqual(validate_start(run, settings=self.settings), ["快照文件指纹不匹配"])
        copied = TestClient(create_app(self.settings)).post("/api/runs/old-run/copy")
        self.assertEqual(copied.status_code, 422)


class InputTests(ServiceFixture):
    def test_actual_calendar_date_not_previous_trade_date_and_empty_news(self):
        calendar = pd.DataFrame({"cal_date": ["2025-01-10", "2025-01-11"], "pretrade_date": ["2025-01-09", "2025-01-10"], "is_open": [1, 0]})
        self.assertEqual(open_trading_dates(calendar), {pd.Timestamp("2025-01-10")})
        news = pd.DataFrame({"cal_date": ["2025-01-10"], "news": [[]]})
        self.assertEqual(news_for_date(news, pd.Timestamp("2025-01-11")), [])
        self.assertEqual(EmptyNewsIndex().search_news_batch(queries=["old news"]), [[]])


class SimulationSmokeTests(RunMarketFixture):
    def test_one_user_two_days_fake_model_real_matching_settlement_and_results(self):
        from server.results import results_agents
        from script.prepare_simulation_run import write_beliefs
        # Production-sized fixture schema, copied into a private database.
        template = self.seed_dir / "real-template.db"
        shutil.copyfile(ROOT / "data" / "sys_1000.db", template)
        run = self.template_run()
        bundle = Path(run["output_dir"]) / "market"
        folder = self.root / "smoke"
        folder.mkdir()
        selected = prepare_database(template, folder / "users.db", 1, market_snapshot=bundle, start_date="2025-01-13")
        beliefs = self.root / "smoke-beliefs.csv"
        beliefs.write_text("user_id,belief\n" + selected[0] + ",观察市场\n")
        write_beliefs(beliefs, folder / "belief.csv", selected)
        (folder / "state.json").write_text(json.dumps({"status": "draft", "checkpoint": {}}))
        env = {**os.environ, "TWINMARKET_DATA_DIR": str(bundle), "TWINMARKET_MARKET_BUNDLE_ID": json.loads(
            (bundle / "bundle.json").read_text())["bundle_id"], "MPLCONFIGDIR": str(folder / "matplotlib"), "PYTHONPATH": str(ROOT)}
        result = subprocess.run([sys.executable, "-m", "server.tests.market_smoke_runner", str(folder)], cwd=ROOT,
                                env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout[-10000:] + result.stderr[-6000:])
        state = json.loads((folder / "state.json").read_text())
        self.assertEqual(state["checkpoint"]["last_completed_date"], "2025-01-14")
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        self.assertEqual([item for item in events if item["event"] == "stage_failed"], [], result.stdout[-10000:])
        observed = json.loads((folder / "observations.json").read_text())
        self.assertEqual(observed, [{"simulation_date": "2025-01-13", "observation_through": "2025-01-10"},
                                    {"simulation_date": "2025-01-14", "observation_through": "2025-01-13"}])
        with sqlite3.connect(folder / "users.db") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM StockData WHERE date='2025-01-14'").fetchone()[0], 10)
            self.assertEqual(db.execute("SELECT count(*) FROM Profiles WHERE created_at='2025-01-14 00:00:00'").fetchone()[0], 1)
        transactions = folder / "simulation_results" / "2025-01-13" / "transactions_2025-01-13.csv"
        self.assertTrue(transactions.is_file(), result.stdout[-10000:])
        self.assertIn("stock_code", pd.read_csv(transactions).columns)
        # One user has no counterparty; submitted orders and zero fills are valid.
        self.assertTrue((folder / "trading_records" / "2025-01-13_orders.json").is_file())
        shutil.copyfile(folder / "users.db", folder / "user_1.db")
        items = results_agents({"output_dir": str(folder), "config": {"user_count": 1}}, 1, 50)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["as_of"], "2025-01-14 00:00:00")
