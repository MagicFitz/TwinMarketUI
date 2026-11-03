"""Offline fixtures shared by the service tests and the simulation backend's contract tests."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from market_service.assets import load_assets
from market_service.providers.tushare import FIELDS, TushareProvider
from market_service.settings import MdsSettings
from market_service.sync import MarketSyncService

ROOT = Path(__file__).resolve().parents[2]
SECRET = "market-secret-canary"
SEED_FILES = ("stock_data.csv", "trading_days.csv", "stock_profile.csv", "market_assets.yaml")
# Legacy daily news around the synced dates: Friday, Monday (two items) and Tuesday.
SEED_NEWS = {"2025-01-10": ["周五旧闻"], "2025-01-13": ["周一新闻甲", "周一新闻乙"], "2025-01-14": ["周二新闻"]}


class FakeTushare:
    """Answers the fixed Tushare endpoints for 2025-01-11 .. 2025-01-14 after the seed history."""

    def __init__(self, data_dir, *, denied=False, incomplete=False, close=101):
        self.assets = load_assets(data_dir / "market_assets.yaml")[0]["assets"]
        self.codes = [c["ts_code"] for asset in self.assets.values() for c in asset["constituents"]]
        self.calendar = pd.read_csv(data_dir / "trading_days.csv").to_dict("records")
        previous = "2025-01-10"
        for day in pd.date_range("2025-01-11", "2025-01-14"):
            opened = day.weekday() < 5
            self.calendar.append({"cal_date": day.strftime("%Y-%m-%d"), "is_open": "open" if opened else "close", "pretrade_date": previous})
            if opened:
                previous = day.strftime("%Y-%m-%d")
        self.denied, self.incomplete, self.close = denied, incomplete, close
        self.calls = []

    def post(self, url, **kwargs):
        body = kwargs["json"]
        self.calls.append(body)
        api, params = body["api_name"], body["params"]
        if api == "daily_basic" and self.denied:
            return SimpleNamespace(status_code=200, json=lambda: {"code": 2002, "msg": f"无权限 {SECRET}"})
        if api == "trade_cal":
            rows = [{"cal_date": row["cal_date"].replace("-", ""), "is_open": 1 if row["is_open"] == "open" else 0,
                     "pretrade_date": row["pretrade_date"].replace("-", "")} for row in self.calendar
                    if params["start_date"] <= row["cal_date"].replace("-", "") <= params["end_date"]]
        else:
            rows = []
            for code in self.codes[:1] if self.incomplete else self.codes:
                if api == "daily":
                    rows.append({"ts_code": code, "trade_date": params.get("trade_date", "20250114"), "close": self.close,
                                 "pre_close": 100, "pct_chg": self.close - 100, "vol": 10, "amount": 2})
                else:
                    rows.append({"ts_code": code, "trade_date": params.get("trade_date", "20250114"),
                                 "pe_ttm": 20, "pb": 2, "ps_ttm": 4, "dv_ttm": 3})
            if "ts_code" in params:
                rows = [row for row in rows if row["ts_code"] == params["ts_code"]]
        names = FIELDS[api].split(",")
        payload = {"code": 0, "data": {"fields": names, "items": [[row[key] for key in names] for row in rows]}}
        return SimpleNamespace(status_code=200, json=lambda: payload)

    def provider(self):
        return TushareProvider(SECRET, post=self.post, sleep=lambda duration: None, interval=0)


class ServiceFixture(unittest.TestCase):
    """A temporary project with seed files and an MDS home; the service seeds on first use."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"TUSHARE_TOKEN": ""})
        self.env.start()
        self.seed_dir = self.root / "data"
        self.seed_dir.mkdir()
        for name in SEED_FILES:
            shutil.copyfile(ROOT / "data" / name, self.seed_dir / name)
        pd.DataFrame({"cal_date": list(SEED_NEWS), "news": list(SEED_NEWS.values())}).to_pickle(
            self.seed_dir / "sorted_impact_news.pkl")
        (self.root / "config").mkdir()
        self.mds_settings = MdsSettings(self.root, self.root / "mds", self.seed_dir)
        self.service = MarketSyncService(self.mds_settings)
        self.service.source.update({"token": SECRET})
        self.fake = FakeTushare(self.seed_dir)

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def mds_app(self, **changes):
        from market_service.app import create_app

        settings = self.mds_settings if not changes else MdsSettings(**{
            "project_root": self.root, "home": self.root / "mds", "seed_dir": self.seed_dir, **changes})
        return create_app(settings)

    def request(self, **changes):
        return {"mode": "backfill", "start_date": "2025-01-13", "end_date": "2025-01-14", **changes}

    def sync(self, fake=None, **changes):
        job = self.service.create(self.request(**changes), launch=False)
        result = self.service.execute(job["job_id"], (fake or self.fake).provider())
        self.assertEqual(result["status"], "completed", result)
        return result
