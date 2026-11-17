"""Plans (read-only coverage checks) and bundles (frozen per-run market inputs).

A bundle is addressed by its inputs: the same template version, date range and
data version always yield the same ``bundle_id``. Its files keep the layout the
simulation already reads (``stock_data.csv``, ``trading_days.csv``,
``sorted_impact_news.pkl`` ...) plus ``rules.json`` and ``bundle.json``.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
from datetime import date, timedelta
from uuid import uuid4

import pandas as pd

from .errors import MarketError
from .registry import now
from .templates import digest, parse_ref
from .warehouse import INDEX_COLUMNS_KEY

CALENDAR_COLUMNS = ["cal_date", "is_open", "pretrade_date"]
STATIC_FILES = ("market_assets.yaml", "stock_profile.csv")


def fingerprint(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return {"sha256": hasher.hexdigest(), "bytes": path.stat().st_size}


def iso(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise MarketError("INVALID_DATE_RANGE", "日期必须使用 YYYY-MM-DD 格式") from None


def cutoff(day: date, hhmm: str) -> str:
    """Information cutoff of simulation day ``day`` as a comparable +08:00 timestamp."""
    return f"{day.isoformat()}T{hhmm}:00+08:00"


def days(start: date, end: date):
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def check(item, label, status, message, **detail):
    return {"item": item, "label": label, "status": status, "message": message, "detail": detail}


class BundleService:
    def __init__(self, settings, registry, warehouse):
        self.settings, self.registry, self.warehouse = settings, registry, warehouse

    def _template(self, ref):
        template_id, version = parse_ref(ref)
        return self.registry.template(template_id, version)

    def _version(self, data_version):
        latest = self.warehouse.latest_version()
        if latest is None:
            raise MarketError("DATA_BRIDGE_MISSING", "数据仓库为空，请先导入种子数据", 503)
        if data_version is None:
            return latest
        if not isinstance(data_version, int) or self.warehouse.version(data_version) is None:
            raise MarketError("DATA_VERSION_NOT_FOUND", "数据版本不存在", 404)
        return data_version

    # ------------------------------------------------------------------- plan

    def plan(self, template_ref, start_date, end_date, data_version=None):
        template = self._template(template_ref)
        body = template["body"]
        version = self._version(data_version)
        start, end = iso(start_date), iso(end_date)
        if start > end:
            raise MarketError("INVALID_DATE_RANGE", "开始日期不能晚于结束日期")
        calendar = {row["cal_date"]: row for row in self.warehouse.rows("calendar", version)}
        bars = self.warehouse.bar_counts(version)
        covered = [day for day in bars if day <= end.isoformat()]
        effective_through = max(covered) if covered else None
        opened = sorted(day for day, row in calendar.items() if row["is_open"] == "open")
        in_range = [day for day in opened if start.isoformat() <= day <= end.isoformat()]
        checks = []

        calendar_through = max(calendar) if calendar else None
        checks.append(check("calendar", "交易日历", "ok" if calendar_through and calendar_through >= end.isoformat() else "block",
                            f"交易日历覆盖至 {calendar_through}" if calendar_through and calendar_through >= end.isoformat()
                            else f"交易日历只到 {calendar_through}，请同步至 {end.isoformat()}", through=calendar_through))

        missing = [day for day in in_range if bars.get(day, 0) < 10]
        checks.append(check("bars", "指数日线", "block" if missing or not in_range else "ok",
                            (f"{len(missing)} 个交易日缺少行情，最早 {missing[0]}，请回补" if missing else
                             "所选区间没有交易日" if not in_range else f"{len(in_range)} 个交易日行情完整，有效至 {effective_through}"),
                            through=effective_through, trading_days=len(in_range), missing=missing[:10]))

        lookback = body["data"]["history"]["lookback_trading_days"]
        prior = [day for day in opened if day < start.isoformat() and bars.get(day, 0) == 10]
        complete = [day for day in opened if bars.get(day, 0) == 10]
        earliest = (date.fromisoformat(complete[lookback - 1]) + timedelta(days=1)).isoformat() if len(complete) >= lookback else None
        checks.append(check("history", "回看历史", "ok" if len(prior) >= lookback else "block",
                            f"开始日前有 {len(prior)} 个交易日（需要 {lookback}）" if len(prior) >= lookback
                            else f"开始日前只有 {len(prior)} 个交易日，需要 {lookback} 个"
                            + (f"；最早可从 {earliest} 开始" if earliest else ""),
                            available=len(prior), required=lookback, earliest_run_date=earliest))

        news = body["data"]["news"]
        news_check, _ = self._news_coverage(news, body["session"]["information_cutoff"], version, start, end, set(in_range))
        checks.append(news_check)
        checks.append(check("retrieval", "新闻检索索引", "disabled", "检索索引尚未提供，Agent 的新闻检索返回空"))

        warnings = [item["message"] for item in checks if item["status"] == "warn"]
        return {"ok": all(item["status"] != "block" for item in checks),
                "template": {"template_id": template["template_id"], "version": template["version"],
                             "ref": f"{template['template_id']}@{template['version']}", "sha256": template["sha256"],
                             "name": body["name"], "body": body},
                "data_version": version, "start_date": start.isoformat(), "end_date": end.isoformat(),
                "earliest_run_date": earliest, "effective_through": effective_through, "checks": checks,
                "warnings": warnings}

    @staticmethod
    def _news_window(hhmm, start, end):
        """Articles whose information cutoff falls in ``[start, end]``, plus the per-day windows."""
        windows = [(day, cutoff(day - timedelta(days=1), hhmm), cutoff(day, hhmm)) for day in days(start, end)]
        return windows, windows[0][1], windows[-1][2]

    def _news_coverage(self, news, hhmm, version, start, end, trading_days):
        if not news["enabled"]:
            return check("news", "新闻", "disabled", "模板未启用新闻"), []
        windows, low, high = self._news_window(hhmm, start, end)
        times = sorted(self.warehouse.news_times(version, low, high, news["sources"], news["include_synthetic"]))
        per_day, index = {}, 0
        for day, lower, upper in windows:
            while index < len(times) and times[index][0] <= upper:
                if times[index][0] > lower:
                    per_day[day.isoformat()] = per_day.get(day.isoformat(), 0) + 1
                index += 1
        gaps = sorted(day for day in trading_days if per_day.get(day, 0) < news["min_daily_articles"])
        day_precision = sum(1 for _, precision in times if precision == "day")
        if gaps:
            status = news["on_gap"]
            message = f"{len(gaps)}/{len(trading_days)} 个交易日新闻不足 {news['min_daily_articles']} 条，最早 {gaps[0]}"
        else:
            status, message = "ok", f"{len(times)} 条新闻覆盖全部 {len(trading_days)} 个交易日"
        detail = {"articles": len(times), "trading_days": len(trading_days), "gap_count": len(gaps), "gaps": gaps[:10],
                  "day_precision_articles": day_precision, "sources": news["sources"]}
        result = check("news", "新闻", status, message, **detail)
        notes = ["部分新闻只有日期精度，按当日 00:00 计入，可能含日内前视"] if day_precision else []
        return result, notes

    # ----------------------------------------------------------------- bundle

    @staticmethod
    def bundle_id(template, start, end, version):
        key = json.dumps([template["sha256"], start, end, version])
        return "b-" + hashlib.sha256(key.encode()).hexdigest()[:20]

    def bundle_path(self, bundle_id):
        return self.settings.path("bundles", bundle_id)

    def create(self, template_ref, start_date, end_date, data_version=None):
        plan = self.plan(template_ref, start_date, end_date, data_version)
        if not plan["ok"]:
            blocked = "；".join(item["message"] for item in plan["checks"] if item["status"] == "block")
            raise MarketError("PLAN_BLOCKED", f"数据不满足模板要求：{blocked}", 409)
        template = self._template(plan["template"]["ref"])
        bundle_id = self.bundle_id(template, plan["start_date"], plan["end_date"], plan["data_version"])
        target = self.bundle_path(bundle_id)
        try:
            metadata = self.registry.bundle(bundle_id)
            if target.is_dir():
                return metadata
        except MarketError:
            pass
        stage = self.settings.path("bundles", f".staging-{bundle_id}-{uuid4().hex[:8]}")
        stage.mkdir(parents=True)
        try:
            metadata = self._write(stage, bundle_id, template, plan)
            try:
                os.rename(stage, target)
            except OSError:
                if not target.is_dir():
                    raise
                shutil.rmtree(stage, ignore_errors=True)  # A concurrent request produced the same bundle.
            with self.registry.connection() as db:
                db.execute("INSERT OR IGNORE INTO bundles VALUES (?,?,?)",
                           (bundle_id, json.dumps(metadata, ensure_ascii=False), metadata["created_at"]))
            return self.registry.bundle(bundle_id)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _write(self, stage, bundle_id, template, plan):
        body, version = template["body"], plan["data_version"]
        start, end = iso(plan["start_date"]), iso(plan["end_date"])
        columns = self.warehouse.setting(INDEX_COLUMNS_KEY) or []
        stock = pd.DataFrame(self.warehouse.rows("index_daily", version, "date<=?", (end.isoformat(),)))
        stock = stock[[c for c in columns if c in stock] + [c for c in stock if c not in columns]]
        stock.sort_values(["ts_code", "date"]).to_csv(stage / "stock_data.csv", index=False)
        calendar = pd.DataFrame(self.warehouse.rows("calendar", version, "cal_date<=?", (end.isoformat(),)))
        calendar[CALENDAR_COLUMNS].sort_values("cal_date").to_csv(stage / "trading_days.csv", index=False)
        for name in STATIC_FILES:
            content = self.warehouse.static(name, version)
            if content is None:
                raise MarketError("DATA_BRIDGE_MISSING", f"数据仓库缺少 {name}")
            (stage / name).write_bytes(content)

        news = body["data"]["news"]
        hhmm = body["session"]["information_cutoff"]
        _, notes = self._news_coverage(news, hhmm, version, start, end,
                                       {c for c in calendar.cal_date if start.isoformat() <= c <= end.isoformat()})
        broadcast = self._broadcast(stage, news, hhmm, version, start, end)
        rules = {"template": f"{template['template_id']}@{template['version']}", "session": body["session"],
                 "rules": body["rules"],
                 "news": {"enabled": news["enabled"], "information_cutoff": hhmm, "broadcast": news["broadcast"],
                          "retrieval": {**news["retrieval"], "available": False}}}
        (stage / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

        files = {str(path.relative_to(stage)): fingerprint(path) for path in sorted(stage.rglob("*")) if path.is_file()}
        metadata = {"schema_version": 1, "bundle_id": bundle_id, "created_at": now(),
                    "template": {"template_id": template["template_id"], "version": template["version"],
                                 "ref": f"{template['template_id']}@{template['version']}",
                                 "sha256": template["sha256"], "name": body["name"], "body": body},
                    "data_version": version, "start_date": plan["start_date"], "end_date": plan["end_date"],
                    "earliest_run_date": plan["earliest_run_date"], "effective_through": plan["effective_through"],
                    "checks": plan["checks"], "warnings": plan["warnings"] + notes + ["新闻检索索引尚未提供，Agent 的新闻检索返回空"],
                    "news": broadcast, "files": files}
        (stage / "bundle.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in stage.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        assert digest(body) == template["sha256"]
        return metadata

    def _broadcast(self, stage, news, hhmm, version, start, end):
        """Daily broadcast lists, never containing an article published after that day's cutoff."""
        frame_days = [day.isoformat() for day in days(start, end)]
        if not news["enabled"]:
            pd.DataFrame({"cal_date": frame_days, "news": [[] for _ in frame_days]}).to_pickle(stage / "sorted_impact_news.pkl")
            return {"enabled": False, "articles": 0, "broadcast_days": 0}
        windows, low, high = self._news_window(hhmm, start, end)
        where = self.warehouse.news_filter(news["sources"], news["include_synthetic"])
        articles = self.warehouse.rows("news_articles", version, where, (low, high, *news["sources"]))
        (stage / "news").mkdir()
        with gzip.GzipFile(stage / "news" / "articles.jsonl.gz", "wb", mtime=0) as handle:
            for article in articles:
                handle.write((json.dumps(article, ensure_ascii=False, sort_keys=True) + "\n").encode())
        top_k = news["broadcast"]["top_k"]
        lists, records = [], []
        for day, lower, upper in windows:
            selected = [a for a in articles if lower < a["published_at"] <= upper]
            # legacy-v1: the legacy file's within-day order, then publication time.
            selected.sort(key=lambda a: (a.get("rank_hint", 0), a["published_at"], a["article_id"]))
            selected = selected[:top_k] if top_k else selected
            if any(a["published_at"] > upper for a in selected):
                raise MarketError("LOOKAHEAD_DETECTED", f"{day} 的广播包含截止时刻之后的新闻")
            lists.append([a["content"] for a in selected])
            records.append({"date": day.isoformat(), "window": [lower, upper], "article_ids": [a["article_id"] for a in selected]})
        pd.DataFrame({"cal_date": frame_days, "news": lists}).to_pickle(stage / "sorted_impact_news.pkl")
        with gzip.GzipFile(stage / "news" / "broadcast.jsonl.gz", "wb", mtime=0) as handle:
            for record in records:
                handle.write((json.dumps(record, ensure_ascii=False) + "\n").encode())
        return {"enabled": True, "articles": len(articles), "broadcast_days": sum(1 for item in lists if item),
                "ranking": news["broadcast"]["ranking"], "top_k": top_k}

    # ---------------------------------------------------------------- serving

    def verified_path(self, bundle_id):
        metadata = self.registry.bundle(bundle_id)
        folder = self.bundle_path(bundle_id)
        for name, expected in metadata["files"].items():
            target = (folder / name).resolve()
            if not target.is_relative_to(folder) or not target.is_file() or fingerprint(target) != expected:
                raise MarketError("BUNDLE_INTEGRITY_FAILED", "数据包文件缺失或指纹不匹配", 500)
        return metadata, folder

    def archive(self, bundle_id):
        """A cached ``.tar.gz`` of the bundle directory; members are plain relative files."""
        _, folder = self.verified_path(bundle_id)
        target = self.settings.path("archives", bundle_id + ".tar.gz")
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{bundle_id}-{uuid4().hex[:8]}.tmp")
            with tarfile.open(temporary, "w:gz") as tar:
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=str(path.relative_to(folder)), recursive=False)
            os.replace(temporary, target)
        return target
