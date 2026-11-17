"""Offline tests for provider -> versioned warehouse -> template plan -> frozen bundle."""
import gzip
import io
import json
import os
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import requests
from fastapi.testclient import TestClient

from market_service.bundles import BundleService
from market_service.errors import MarketError
from market_service.indices import build_indices, ratio
from market_service.providers.tushare import FIELDS, TushareProvider
from market_service.registry import MarketRegistry
from market_service.seed import seed
from market_service.templates import builtin_templates, ensure_builtin, publish
from market_service.tests.fixtures import SECRET, FakeTushare, ServiceFixture
from market_service.warehouse import Warehouse


class ProviderTests(unittest.TestCase):
    def provider(self, post):
        return TushareProvider(SECRET, post=post, sleep=lambda duration: None, interval=0)

    def response(self, payload, status=200):
        return SimpleNamespace(status_code=status, json=lambda: payload)

    def test_permission_does_not_retry_or_expose_supplier_message(self):
        post = Mock(return_value=self.response({"code": 2002, "msg": "无权限 " + SECRET}))
        with self.assertRaises(MarketError) as caught:
            self.provider(post).query("daily", {})
        self.assertEqual(caught.exception.code, "TUSHARE_PERMISSION_DENIED")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(post.call_count, 1)

    def test_invalid_token_and_malformed_responses_are_stable_errors(self):
        for payload, code in (({"code": 2002, "msg": "token invalid " + SECRET}, "TUSHARE_AUTH_FAILED"),
                              ({"code": 0, "data": {"fields": ["ts_code"], "items": [[1, 2]]}}, "TUSHARE_INVALID_RESPONSE"),
                              ({"code": 0}, "TUSHARE_INVALID_RESPONSE")):
            with self.subTest(code=code), self.assertRaises(MarketError) as caught:
                self.provider(Mock(return_value=self.response(payload))).query("daily", {})
            self.assertEqual(caught.exception.code, code)

    def test_transient_errors_retry_and_empty_success_is_valid(self):
        empty = self.response({"code": 0, "data": {"fields": FIELDS["daily"].split(","), "items": []}})
        for first in (requests.Timeout(SECRET), self.response({}, status=503), self.response({"code": 2002, "msg": "每分钟最多访问"})):
            post = Mock(side_effect=[first, empty])
            self.assertEqual(self.provider(post).query("daily", {}), [])
            self.assertEqual(post.call_count, 2)

    def test_exhausted_retry_does_not_expose_network_exception(self):
        post = Mock(side_effect=requests.Timeout(SECRET))
        with self.assertRaises(MarketError) as caught:
            self.provider(post).query("daily", {})
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(post.call_count, 4)

    def test_pagination_refuses_silent_repeated_page(self):
        names = FIELDS["daily"].split(",")
        payload = {"code": 0, "data": {"fields": names, "items": [[str(i), "20250113", 10, 10, 0, 1, 1] for i in range(6000)]}}
        with self.assertRaises(MarketError) as caught:
            self.provider(Mock(return_value=self.response(payload))).fetch("daily", {})
        self.assertEqual(caught.exception.code, "DATA_DUPLICATE_ROWS")


class IndexTests(unittest.TestCase):
    def test_harmonic_valuation_nonpositive_missing_and_coverage(self):
        constituents = [{"ts_code": "a", "weight": .8}, {"ts_code": "b", "weight": .2}]
        self.assertEqual(ratio({"a": {"pe_ttm": 10}, "b": {"pe_ttm": 20}}, constituents, "pe_ttm"), (1 / .09, 1.0))
        self.assertEqual(ratio({"a": {"pe_ttm": -1}, "b": {"pe_ttm": 20}}, constituents, "pe_ttm"), (None, .2))

    def test_suspension_returns_zero_and_ma_uses_only_prior_and_current(self):
        assets = {"TEST": {"name": "Test", "constituents": [{"ts_code": "a", "weight": .9}, {"ts_code": "b", "weight": .1}]}}
        history = pd.DataFrame([{"ts_code": "TEST", "date": "2025-01-10", "close": 10, "pre_close": 10, "amount": 1, "vol": 100}])
        daily = {"2025-01-13": {"a": {"close": 11, "pre_close": 10, "pct_chg": 10, "vol": 1, "amount": 1}}}
        output, warnings, coverage = build_indices(assets, history, daily, {}, ["2025-01-13"])
        self.assertAlmostEqual(output.iloc[-1]["close"], 10.9)
        self.assertAlmostEqual(output.iloc[-1]["ma_hfq_5"], 10.45)
        self.assertTrue(warnings)
        self.assertEqual(coverage[0]["weight"], .9)


class SourceAndSyncTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        seed(self.mds_settings, self.service.warehouse)

    def test_source_secret_keep_replace_clear_and_environment_priority(self):
        source = self.service.source
        self.assertEqual(source.token(), SECRET)
        self.assertNotIn(SECRET, json.dumps(source.get()))
        self.assertEqual(source.path.stat().st_mode & 0o777, 0o600)
        source.update({"enabled": False})
        self.assertEqual(source.token(), SECRET)
        source.update({"enabled": True, "token": "replacement"})
        self.assertEqual(source.token(), "replacement")
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "environment-secret"}):
            source.update({"token": ""})
            self.assertEqual(source.token(), "environment-secret")
            self.assertEqual(source.get()["credential_source"], "environment")
        self.assertFalse(source.get()["token_configured"])

    def test_capabilities_are_separate_and_invalidated_on_secret_update(self):
        result = self.service.source.test(FakeTushare(self.seed_dir, denied=True).provider())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["capabilities"]["daily_basic"]["status"], "permission_denied")
        self.assertNotIn(SECRET, json.dumps(result))
        self.service.source.update({"token": "new-token"})
        self.assertEqual(self.service.source.get()["status"], "untested")

    def test_seed_imports_history_calendar_assets_and_day_precision_news_once(self):
        warehouse = self.service.warehouse
        self.assertEqual(warehouse.latest_version(), 1)
        self.assertIsNone(seed(self.mds_settings, warehouse))
        self.assertEqual(warehouse.bounds("index_daily", "date", 1)["through"], "2025-01-10")
        news = warehouse.rows("news_articles", 1)
        self.assertEqual(len(news), 4)
        self.assertTrue(all(item["time_precision"] == "day" and item["published_at"].endswith("T00:00:00+08:00") for item in news))
        self.assertTrue(warehouse.versions()[0]["summary"]["warnings"])

    def test_sync_writes_a_new_version_with_units_and_chaining(self):
        job = self.sync()
        self.assertEqual(job["data_version"], 2)
        self.assertEqual(job["progress"], 1)
        history = self.service.history(2)
        price = self.service.history(1).query("ts_code == 'TLEI' and date == '2025-01-10'").iloc[0]["close"]
        new = history[(history.ts_code == "TLEI") & (history.date >= "2025-01-13")]
        self.assertAlmostEqual(new.iloc[0]["close"], price * 1.01)
        self.assertAlmostEqual(new.iloc[1]["close"], price * 1.01 ** 2)
        self.assertEqual(new.iloc[0]["vol"], 2000)
        self.assertEqual(new.iloc[0]["amount"], 4000)
        self.assertEqual(new.iloc[0]["pe_ttm"], 20)
        self.assertTrue(pd.isna(new.iloc[0]["elg_amount_net"]))
        # Version 1 is still readable exactly as seeded.
        self.assertEqual(self.service.history(1)["date"].max(), "2025-01-10")
        self.assertTrue(self.mds_settings.path("raw", job["job_id"], "request_manifest.jsonl").is_file())
        for path in self.mds_settings.home.rglob("*"):
            if path.is_file() and path.suffix not in {".gz"}:
                self.assertNotIn(SECRET.encode(), path.read_bytes())

    def test_identical_sync_creates_no_version_but_revision_does(self):
        self.sync()
        unchanged = self.sync()
        self.assertEqual((unchanged["data_version"], unchanged["stage"]), (2, "unchanged"))
        revised = self.sync(FakeTushare(self.seed_dir, close=102))
        self.assertEqual(revised["data_version"], 3)
        changes = self.service.warehouse.version(3)["summary"]["changes"]["index_daily"]
        self.assertEqual(changes, {"inserted": 0, "revised": 20})
        old = self.service.history(2).query("ts_code == 'TLEI' and date == '2025-01-13'").iloc[0]["close"]
        new = self.service.history(3).query("ts_code == 'TLEI' and date == '2025-01-13'").iloc[0]["close"]
        self.assertNotAlmostEqual(old, new)

    def test_optional_permission_degrades_but_incomplete_required_data_cannot_publish(self):
        job = self.sync(FakeTushare(self.seed_dir, denied=True))
        self.assertEqual(self.service.warehouse.version(job["data_version"])["summary"]["datasets"]["daily_basic"], "missing")
        job = self.service.create(self.request(), launch=False)
        incomplete = FakeTushare(self.seed_dir, incomplete=True)
        result = self.service.execute(job["job_id"], incomplete.provider())
        self.assertEqual((result["status"], result["error_code"]), ("failed", "DATA_COVERAGE_INCOMPLETE"))
        self.assertEqual(self.service.warehouse.latest_version(), 2)
        self.assertFalse(any(call["api_name"] == "daily" and call["params"]["trade_date"] == "20250114"
                             for call in incomplete.calls))
        self.assertFalse(self.mds_settings.path("staging", job["job_id"]).exists())

    def test_suspended_constituent_of_small_basket_warns(self):
        suspended = self.fake.assets["TLEI"]["constituents"][1]["ts_code"]
        self.fake.codes.remove(suspended)
        summary = self.service.warehouse.version(self.sync()["data_version"])["summary"]
        self.assertLess(summary["constituent_coverage_min"], .8)
        self.assertTrue(any("2025-01-13 至 2025-01-14" in item and "停牌" in item for item in summary["warnings"]))

    def test_end_date_before_synced_history_is_rejected(self):
        with self.assertRaises(MarketError) as caught:
            self.service.create(self.request(start_date="2025-01-08", end_date="2025-01-09"), launch=False)
        self.assertEqual(caught.exception.code, "INVALID_DATE_RANGE")

    def test_cancel_conflict_and_restart_persistence(self):
        job = self.service.create(self.request(), launch=False)
        with self.assertRaises(MarketError) as caught:
            self.service.create(self.request(), launch=False)
        self.assertEqual(caught.exception.code, "SYNC_JOB_CONFLICT")
        self.service.registry.cancel(job["job_id"])
        self.assertEqual(self.service.execute(job["job_id"], self.fake.provider())["status"], "cancelled")
        self.assertEqual(MarketRegistry(self.mds_settings).job(job["job_id"])["status"], "cancelled")
        self.assertEqual(self.service.warehouse.latest_version(), 1)

    def test_cancel_during_fetch_never_publishes(self):
        job = self.service.create(self.request(), launch=False)
        original = self.fake.post

        def cancel(url, **kwargs):
            if kwargs["json"]["api_name"] == "daily":
                self.service.registry.cancel(job["job_id"])
            return original(url, **kwargs)

        result = self.service.execute(job["job_id"], TushareProvider(SECRET, post=cancel, interval=0))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.service.warehouse.latest_version(), 1)


class TemplateTests(ServiceFixture):
    def test_builtin_templates_validate_and_publish_bumps_version(self):
        registry = self.service.registry
        ensure_builtin(registry)
        ensure_builtin(registry)  # Idempotent.
        self.assertEqual({t["template_id"] for t in builtin_templates()}, {"cn-a-industry10", "cn-a-industry10-nonews"})
        body = registry.template("cn-a-industry10")["body"]
        published = publish(registry, {**body, "rules": {**body["rules"], "price_limit_pct": 20}})
        self.assertEqual(published["version"], 2)
        self.assertEqual(registry.template("cn-a-industry10", 1)["body"]["rules"]["price_limit_pct"], 10)
        for broken in ({**body, "schema_version": 2}, {**body, "template_id": "Bad Id"},
                       {**body, "data": {**body["data"], "news": {**body["data"]["news"], "sources": ["unknown"]}}},
                       {**body, "session": {**body["session"], "information_cutoff": "9am"}}):
            with self.subTest(broken=broken.get("template_id")), self.assertRaises(MarketError) as caught:
                publish(registry, broken)
            self.assertEqual(caught.exception.code, "TEMPLATE_INVALID")


class PlanAndBundleTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(self.mds_app())
        self.sync()
        self.bundles = BundleService(self.mds_settings, self.service.registry, Warehouse(self.mds_settings))

    def plan(self, template="cn-a-industry10", start="2025-01-13", end="2025-01-14", **extra):
        return self.client.post("/v1/plans", json={"template": template, "start_date": start, "end_date": end, **extra})

    def test_plan_reports_coverage_and_blocks_missing_data(self):
        body = self.plan().json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["template"]["ref"], "cn-a-industry10@1")
        self.assertEqual(body["data_version"], 2)
        statuses = {item["item"]: item["status"] for item in body["checks"]}
        self.assertEqual(statuses, {"calendar": "ok", "bars": "ok", "history": "ok", "news": "ok", "retrieval": "disabled"})
        future = self.plan(end="2025-01-15").json()
        self.assertFalse(future["ok"])
        self.assertEqual(next(i for i in future["checks"] if i["item"] == "calendar")["status"], "block")
        early = self.plan(start="2023-01-04", end="2023-01-05").json()
        self.assertEqual(next(i for i in early["checks"] if i["item"] == "history")["status"], "block")
        self.assertIn("最早可从", next(i for i in early["checks"] if i["item"] == "history")["message"])
        # Pinned older version: the synced days do not exist there.
        self.assertFalse(self.plan(data_version=1).json()["ok"])
        self.assertEqual(self.plan(data_version=99).status_code, 404)
        self.assertEqual(self.plan(template="missing").status_code, 404)
        nonews = self.plan(template="cn-a-industry10-nonews").json()
        self.assertEqual(next(i for i in nonews["checks"] if i["item"] == "news")["status"], "disabled")

    def test_news_gap_follows_template_policy(self):
        registry = self.service.registry
        body = registry.template("cn-a-industry10")["body"]
        strict = publish(registry, {**body, "data": {**body["data"], "news": {**body["data"]["news"],
                                                                                "min_daily_articles": 2, "on_gap": "block"}}})
        result = self.plan(template=f"cn-a-industry10@{strict['version']}").json()
        self.assertFalse(result["ok"])
        news = next(i for i in result["checks"] if i["item"] == "news")
        self.assertEqual((news["status"], news["detail"]["gaps"]), ("block", ["2025-01-14"]))

    def test_bundle_is_deterministic_read_only_and_free_of_lookahead(self):
        # Minute-precision news published after Monday's 09:00 cutoff belongs to Tuesday.
        self.service.warehouse.write(job_id=None, kind="test", summary={}, rows={"news_articles": [{
            "article_id": "late-monday", "source": "tushare.news", "channel": "cls", "published_at": "2025-01-13T10:30:00+08:00",
            "time_precision": "minute", "title": "盘中快讯", "content": "周一盘中快讯", "content_sha256": "x",
            "url": None, "ts_codes": [], "synthetic": False}]})
        response = self.client.post("/v1/bundles", json={"template": "cn-a-industry10", "start_date": "2025-01-13",
                                                         "end_date": "2025-01-14"})
        self.assertEqual(response.status_code, 201, response.text)
        metadata = response.json()
        again = self.client.post("/v1/bundles", json={"template": "cn-a-industry10", "start_date": "2025-01-13",
                                                      "end_date": "2025-01-14", "data_version": metadata["data_version"]}).json()
        self.assertEqual(again["bundle_id"], metadata["bundle_id"])
        _, folder = self.bundles.verified_path(metadata["bundle_id"])
        news = pd.read_pickle(folder / "sorted_impact_news.pkl").set_index("cal_date")["news"].to_dict()
        self.assertEqual(news, {"2025-01-13": ["周一新闻甲", "周一新闻乙"], "2025-01-14": ["周一盘中快讯", "周二新闻"]})
        broadcast = [json.loads(line) for line in gzip.open(folder / "news" / "broadcast.jsonl.gz", "rt")]
        self.assertEqual(broadcast[0]["window"], ["2025-01-12T09:00:00+08:00", "2025-01-13T09:00:00+08:00"])
        stock = pd.read_csv(folder / "stock_data.csv")
        self.assertEqual(stock["date"].max(), "2025-01-14")
        self.assertEqual(list(stock.columns[:4]), ["ts_code", "Industry", "category", "date"])
        self.assertEqual(json.loads((folder / "rules.json").read_text())["rules"]["price_limit_pct"], 10)
        self.assertEqual(folder.joinpath("stock_data.csv").stat().st_mode & 0o222, 0)
        self.assertIn("部分新闻只有日期精度，按当日 00:00 计入，可能含日内前视", metadata["warnings"])
        archive = self.client.get(f"/v1/bundles/{metadata['bundle_id']}/archive")
        with tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz") as tar:
            self.assertEqual(sorted(tar.getnames()), sorted([*metadata["files"], "bundle.json"]))
        self.assertEqual(self.client.put(f"/v1/bundles/{metadata['bundle_id']}/refs/run-1").json()["refs"], ["run-1"])
        self.assertEqual(self.client.delete(f"/v1/bundles/{metadata['bundle_id']}/refs/run-1").json()["refs"], [])

    def test_blocked_plan_refuses_bundle_and_tamper_is_detected(self):
        blocked = self.client.post("/v1/bundles", json={"template": "cn-a-industry10", "start_date": "2025-01-13",
                                                        "end_date": "2025-01-15"})
        self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "PLAN_BLOCKED"))
        metadata = self.bundles.create("cn-a-industry10", "2025-01-13", "2025-01-14")
        stock = self.bundles.bundle_path(metadata["bundle_id"]) / "stock_data.csv"
        stock.chmod(0o644)
        stock.write_text("tampered")
        self.assertEqual(self.client.get(f"/v1/bundles/{metadata['bundle_id']}/archive").json()["code"], "BUNDLE_INTEGRITY_FAILED")

    def test_api_redaction_jobs_versions_series_and_news(self):
        result = self.client.get("/v1/sources/tushare")
        self.assertNotIn(SECRET, result.text)
        self.assertEqual(self.client.put("/v1/sources/tushare", json={"enabled": True}).status_code, 200)
        with patch("market_service.sync.subprocess.Popen", return_value=SimpleNamespace(pid=os.getpid())) as launch:
            response = self.client.post("/v1/jobs", json=self.request())
        self.assertEqual(response.status_code, 202)
        self.assertNotIn(SECRET, str(launch.call_args))
        self.assertEqual(launch.call_args.args[0][2], "market_service.worker")
        result = self.service.execute(response.json()["job_id"], self.fake.provider())
        self.assertEqual(self.client.get(f"/v1/jobs/{result['job_id']}").json()["status"], "completed")
        self.assertEqual(self.client.get("/v1/versions").json()["items"][0]["data_version"], 2)
        series = self.client.get("/v1/market/series", params={"asset": "TLEI"}).json()
        self.assertEqual((series["selected"], series["prices"][-1]["date"], series["prices"][-1]["vol"]), ("TLEI", "2025-01-14", 2000))
        self.assertEqual(self.client.get("/v1/market/series", params={"asset": "UNKNOWN"}).json()["selected"],
                         series["assets"][0]["stock_id"])
        news = self.client.get("/v1/news", params={"from": "2025-01-13", "to": "2025-01-13"}).json()
        self.assertEqual(news["total"], 2)
        overview = self.client.get("/v1/overview").json()
        self.assertEqual(overview["datasets"]["news"]["legacy"]["rows"], 4)
        self.assertTrue(next(t for t in overview["templates"] if t["template_id"] == "cn-a-industry10")["default"])

    def test_token_guards_every_v1_route_when_configured(self):
        client = TestClient(self.mds_app(token="service-token"))
        self.assertEqual(client.get("/v1/health").status_code, 401)
        self.assertEqual(client.get("/v1/health", headers={"Authorization": "Bearer service-token"}).status_code, 200)
        self.assertEqual(client.get("/health/live").status_code, 200)
