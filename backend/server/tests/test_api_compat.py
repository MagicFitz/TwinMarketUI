import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.process_manager import ProcessManager
from server.registry import RunRegistry
from server.settings import Settings


def create_fixture_settings(root: Path) -> Settings:
    data_dir = root / "data"
    runs_dir = root / "runs"
    (root / "config").mkdir(parents=True)
    (root / "data" / "belief").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    data_dir.mkdir(exist_ok=True)
    runs_dir.mkdir()

    (root / "config" / "api.yaml").write_text("model_name: fixture-model\n", encoding="utf-8")
    (root / "config" / "api_example.yaml").write_text("model_name: fixture-model\n", encoding="utf-8")
    (root / "config" / "embedding.yaml").write_text("model_name: fixture-embedding\n", encoding="utf-8")
    (root / "data" / "belief" / "belief_1000_0129.csv").write_text("user_id,belief\n1,0\n", encoding="utf-8")
    for name in ("stock_data.csv", "trading_days.csv", "sorted_impact_news.pkl"):
        (data_dir / name).write_text("fixture\n", encoding="utf-8")
    (root / "docs" / "public.md").write_text("---\npublished: true\n---\n# Public\nBODY", encoding="utf-8")

    db = data_dir / "sys_1000.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE Profiles (
                user_id TEXT, user_type TEXT, location TEXT, gender TEXT,
                bh_disposition_effect_category TEXT, bh_lottery_preference_category TEXT,
                bh_underdiversification_category TEXT, current_cash REAL,
                total_value REAL, return_rate REAL, cur_positions TEXT, created_at TEXT,
                bh_annual_turnover_category TEXT, trade_count_category TEXT,
                bh_total_return_category TEXT, sys_prompt TEXT, prompt TEXT,
                self_description TEXT, fol_ind TEXT, ini_cash REAL
            );
            CREATE TABLE Strategy (user_id TEXT, strategy TEXT);
            CREATE TABLE StockProfile (
                stock_id TEXT, name TEXT, industry TEXT, weight REAL, description TEXT
            );
            CREATE TABLE StockData (date TEXT, stock_id TEXT, close_price REAL, pct_chg REAL, vol REAL);
            """
        )
        connection.execute(
            """INSERT INTO Profiles (
                user_id, user_type, location, gender, bh_disposition_effect_category,
                bh_lottery_preference_category, bh_underdiversification_category, current_cash,
                total_value, return_rate, cur_positions, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("1", "retail", "Shanghai", "F", "low", "low", "low", 100.0, 110.0, 0.1, "{}", "2023-06-15"),
        )
        connection.execute("INSERT INTO Strategy VALUES(?, ?)", ("1", "hold"))
        connection.execute("INSERT INTO StockProfile VALUES(?,?,?,?,?)", ("000001", "Fixture", "Bank", 1.0, "fixture asset"))
        connection.execute("INSERT INTO StockData VALUES(?,?,?,?,?)", ("2023-06-15", "000001", 10.0, 1.0, 100.0))

    return Settings(
        project_root=root.resolve(),
        data_dir=data_dir.resolve(),
        runs_dir=runs_dir.resolve(),
        registry_path=(root / "server" / "data" / "runs.sqlite3").resolve(),
        allowed_origins=("https://frontend.example.test",),
    )


class ApiCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = create_fixture_settings(Path(self.temp.name))
        self.registry = RunRegistry(self.settings.registry_path)
        self.manager = ProcessManager(self.registry, self.settings)
        self.client = TestClient(create_app(self.settings, registry=self.registry, manager=self.manager))

    def tearDown(self):
        self.temp.cleanup()

    def test_names_may_repeat_and_ids_are_time_based(self):
        body = {"name": "实验 1", "config": {"user_count": 1, "config_path": "config/api_example.yaml"}}
        first = self.client.post("/api/runs", json=body)
        second = self.client.post("/api/runs", json=body)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["name"], second.json()["name"])
        self.assertNotEqual(first.json()["run_id"], second.json()["run_id"])
        for run in (first.json(), second.json()):
            self.assertRegex(run["run_id"], r"^\d{8}-\d{6}-[0-9a-f]{4}$")
            self.assertTrue((self.settings.runs_dir / run["run_id"]).is_dir())
        explicit = {**body, "run_id": "fixed-id"}
        self.assertEqual(self.client.post("/api/runs", json=explicit).status_code, 201)
        self.assertEqual(self.client.post("/api/runs", json=explicit).status_code, 409)

    def test_control_routes_keep_statuses_and_runtime_response_shape(self):
        response = self.client.post(
            "/api/runs",
            json={
                "name": "API test",
                "run_id": "api-test",
                "config": {"user_count": 1, "config_path": "config/api_example.yaml"},
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "draft")
        manifest = json.loads((self.settings.runs_dir / "api-test" / "manifest.json").read_text())
        self.assertIn("code_version", manifest)
        self.assertIn("market_trading_days", manifest["inputs"])
        self.assertEqual(manifest["command_summary"], "server.worker -> python -m simulation (arguments stored in Registry config)")

        runtime = self.client.get("/api/runs/api-test/runtime")
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["run_id"], "api-test")
        self.assertIn("lifecycle", runtime.json())
        failed_resume = self.client.post("/api/runs/api-test/resume")
        self.assertEqual(failed_resume.status_code, 409)
        self.assertIn("error", failed_resume.json())

    def test_legacy_read_routes_keep_their_dtos_and_not_found_status(self):
        catalog = self.client.get("/api/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(set(catalog.json()), {"group", "market_range", "inventory", "connections"})

        documentation = self.client.get("/api/documentation")
        self.assertEqual(documentation.status_code, 200)
        self.assertEqual(documentation.json(), {"items": [{"path": "public.md", "title": "Public", "category": "文档", "order": 100}]})
        detail = self.client.get("/api/documentation?path=public.md")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["content"], "# Public\nBODY")
        self.assertEqual(self.client.get("/api/documentation?path=private.md").status_code, 404)

        members = self.client.get("/api/runs/template/agents")
        self.assertEqual(members.status_code, 200)
        self.assertEqual(set(members.json()), {"items", "total", "page"})
        self.assertEqual(members.json()["items"][0]["user_id"], "1")
        detail = self.client.get("/api/runs/template/agents/1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["strategy"], "hold")

        market = self.client.get("/api/runs/template/market")
        self.assertEqual(market.status_code, 200)
        self.assertEqual(set(market.json()), {"assets", "selected", "prices", "source"})
        self.assertEqual(market.json()["selected"], "000001")

    def test_agent_distribution_and_prompts_use_the_earliest_snapshot(self):
        columns = (
            "user_id, user_type, location, gender, bh_disposition_effect_category, "
            "bh_lottery_preference_category, bh_underdiversification_category, bh_annual_turnover_category, "
            "trade_count_category, bh_total_return_category, sys_prompt, prompt, self_description, "
            "fol_ind, ini_cash, current_cash, total_value, return_rate, cur_positions, created_at"
        )
        holding = json.dumps({"000001": {"shares": 100, "ratio": 75.0}, "UNKNOWN": {"shares": 0, "ratio": 0}})
        rows = [
            ("2", "大V", "北京", "男", "高", "低", "中", "低", "中", "高", "短", "长", "述",
             '["金融", "科技"]', 1000.0, 250.0, 1000.0, 0.0, holding, "2023-01-01"),
            # 后续快照：提示词为空、资产已变化，不能影响画像和初始统计。
            ("2", "大V", "北京", "男", "高", "低", "中", "低", "中", "高", "", "", "",
             '["金融", "科技"]', 1000.0, 1000.0, 1000.0, 0.0, "{}", "2023-01-02"),
            ("3", "普通股民", "北京", "", "低", "低", "中", "低", "中", "高", "", "", "",
             "not json", 5000.0, 5000.0, 5000.0, 0.0, "not json", "2023-01-01"),
        ]
        with sqlite3.connect(self.settings.data_dir / "sys_1000.db") as connection:
            connection.execute("DELETE FROM Profiles")
            connection.executemany(f"INSERT INTO Profiles ({columns}) VALUES ({','.join('?' * 20)})", rows)
            connection.execute("INSERT INTO Strategy VALUES(?, ?)", ("2", "技术面"))

        response = self.client.get("/api/runs/template/agents/distribution")
        self.assertEqual(response.status_code, 200)
        stats = response.json()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["location"], [{"name": "北京", "value": 2}])
        self.assertEqual(stats["gender"], [{"name": "未知", "value": 1}, {"name": "男", "value": 1}])
        self.assertEqual(stats["strategy"], [{"name": "技术面", "value": 1}, {"name": "未知", "value": 1}])
        self.assertEqual(stats["initial_cash"], [{"name": 1000.0, "value": 1}, {"name": 5000.0, "value": 1}])
        self.assertEqual(stats["holdings"], [{"name": "Fixture", "value": 1}])
        self.assertEqual(stats["industries"], [{"name": "科技", "value": 1}, {"name": "金融", "value": 1}])
        exposure = {item["name"]: item["value"] for item in stats["exposure"]}
        self.assertEqual((exposure["0–20%"], exposure["60–80%"], sum(exposure.values())), (1, 1, 2))
        self.assertEqual(stats["behaviors"][0], {"name": "处置效应", "低": 1, "中": 0, "高": 1})

        detail = self.client.get("/api/runs/template/agents/2").json()
        self.assertEqual((detail["sys_prompt"], detail["prompt"], detail["self_description"]), ("短", "长", "述"))
        self.assertEqual(detail["current_cash"], 1000.0)

    def test_model_settings_can_be_updated_without_returning_api_keys(self):
        initial = self.client.get("/api/settings/models")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["language"]["model_name"], "fixture-model")
        self.assertEqual(initial.json()["language"]["provider"], "openai")
        self.assertNotIn("api_key", initial.json()["language"])

        saved = self.client.put(
            "/api/settings/models/language",
            json={
                "api_type": "openai",
                "provider": "vllm",
                "model_name": "qwen-test",
                "base_url": "http://127.0.0.1:8000/v1",
                "timeout": 45,
                "api_key": "local-secret",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["model_name"], "qwen-test")
        self.assertTrue(saved.json()["api_key_configured"])
        self.assertNotIn("api_key", saved.json())
        stored = (self.settings.project_root / "config" / "api.yaml").read_text(encoding="utf-8")
        self.assertIn("local-secret", stored)

        embedding = self.client.put(
            "/api/settings/models/embedding",
            json={
                "provider": "ollama",
                "model_name": "bge-m3",
                "base_url": "http://127.0.0.1:11434",
                "endpoint": "http://127.0.0.1:11434/api/embed",
                "timeout": 60,
                "max_retries": 2,
            },
        )
        self.assertEqual(embedding.status_code, 200)
        self.assertEqual(embedding.json()["endpoint"], "http://127.0.0.1:11434/api/embed")

    def test_model_settings_rejects_invalid_endpoint(self):
        response = self.client.put(
            "/api/settings/models/embedding",
            json={"provider": "ollama", "model_name": "bge", "base_url": "not a URL", "endpoint": ""},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("base_url", response.json()["error"])

    def test_allowed_origin_receives_cors_headers(self):
        origin = "https://frontend.example.test"
        preflight = self.client.options(
            "/api/runs",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
        self.assertIn("POST", preflight.headers["access-control-allow-methods"])


if __name__ == "__main__":
    unittest.main()
