import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.process_manager import ProcessManager
from server.registry import RunRegistry
from server.tests.test_api_compat import create_fixture_settings


class FakeOpenAI(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible server: accepts only the key ``good-key``."""

    requests: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._send(401, {"error": {"message": "Invalid API key good-key... rejected"}})
        if self.path == "/v1/chat/completions":
            return self._send(200, {"model": body["model"], "choices": [{"message": {"content": "p"}}]})
        if self.path == "/v1/embeddings":
            return self._send(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        return self._send(404, {"error": {"message": "not found"}})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ModelConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeOpenAI.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        settings = create_fixture_settings(Path(self.temp.name))
        registry = RunRegistry(settings.registry_path)
        self.client = TestClient(create_app(settings, registry=registry, manager=ProcessManager(registry, settings)))

    def tearDown(self):
        self.temp.cleanup()

    def save(self, kind, **fields):
        response = self.client.put(f"/api/settings/models/{kind}", json=fields)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_language_model_success_reports_latency_and_is_remembered(self):
        self.save("language", model_name="qwen", base_url=self.base, api_key="good-key")
        result = self.client.post("/api/settings/models/language/test").json()
        self.assertTrue(result["ok"], result)
        self.assertIsInstance(result["latency_ms"], int)
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["response_model"], "qwen")
        self.assertEqual(FakeOpenAI.requests[-1]["body"]["max_tokens"], 1)
        saved = self.client.get("/api/settings/models").json()["language"]
        self.assertTrue(saved["last_test"]["ok"])
        # 修改模型后，上一次测试结果不再适用。
        self.save("language", model_name="other", base_url=self.base)
        self.assertIsNone(self.client.get("/api/settings/models").json()["language"]["last_test"])

    def test_auth_failure_is_a_result_and_key_is_not_echoed(self):
        self.save("language", model_name="qwen", base_url=self.base, api_key="bad-key")
        result = self.client.post("/api/settings/models/language/test").json()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 401)
        self.assertIn("API Key", result["message"])
        self.assertNotIn("bad-key", json.dumps(result))

    def test_unsaved_values_can_be_tested_without_saving(self):
        self.save("embedding", provider="openai", model_name="bge", base_url=self.base, api_key="bad-key")
        result = self.client.post("/api/settings/models/embedding/test", json={"api_key": "good-key"}).json()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["dimension"], 3)
        self.assertEqual(FakeOpenAI.requests[-1]["path"], "/v1/embeddings")
        self.assertEqual(FakeOpenAI.requests[-1]["body"]["input"], "ping")
        # 用未保存的 Key 测试不覆盖“上次测试”，也不写入配置。
        saved = self.client.get("/api/settings/models").json()["embedding"]
        self.assertIsNone(saved["last_test"])

    def test_saved_key_is_not_sent_to_a_changed_base_url(self):
        self.save("language", model_name="qwen", base_url=self.base, api_key="good-key")
        other = self.base.replace("127.0.0.1", "localhost")
        result = self.client.post("/api/settings/models/language/test", json={"base_url": other}).json()
        self.assertFalse(result["used_api_key"])
        self.assertIsNone(FakeOpenAI.requests[-1]["auth"])
        self.assertEqual(result["status_code"], 401)

    def test_unreachable_host_and_invalid_url(self):
        self.save("language", model_name="qwen", base_url="http://127.0.0.1:9/v1", timeout=2)
        result = self.client.post("/api/settings/models/language/test").json()
        self.assertFalse(result["ok"])
        self.assertIn("无法连接", result["message"])
        invalid = self.client.post("/api/settings/models/language/test", json={"base_url": "not-a-url"})
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
