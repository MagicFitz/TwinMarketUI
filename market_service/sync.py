"""Daily synchronization orchestrator shared by HTTP, CLI and the detached worker.

A successful job writes one new warehouse ``data_version``; nothing that a
bundle already froze is ever modified.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .assets import load_assets_bytes
from .data_sources import DataSourceService
from .errors import Cancelled, MarketError
from .indices import TRANSFORM_VERSION, build_indices
from .normalize import normalize_constituents
from .providers.tushare import FIELDS, TushareProvider
from .registry import MarketRegistry
from .warehouse import INDEX_COLUMNS_KEY, Warehouse


def iso_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise MarketError("INVALID_DATE_RANGE", "日期必须使用 YYYY-MM-DD 格式") from None


def supplier_date(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date().isoformat()
    except ValueError:
        raise MarketError("TUSHARE_INVALID_RESPONSE", "供应商日期格式无效") from None


def latest_closed_date():
    current = datetime.now(ZoneInfo("Asia/Shanghai"))
    # Conservatively wait until 18:45 for post-close data to settle.
    return (current.date() if (current.hour, current.minute) >= (18, 45)
            else current.date() - timedelta(days=1)).isoformat()


def frame(rows, columns=None):
    result = pd.DataFrame(rows)
    if columns:
        result = result[[c for c in columns if c in result] + [c for c in result if c not in columns]]
    return result


class MarketSyncService:
    def __init__(self, settings):
        self.settings, self.registry = settings, MarketRegistry(settings)
        self.warehouse = Warehouse(settings)
        self.source = DataSourceService(settings)

    def history(self, version=None):
        """Index history visible at ``version`` (default: latest), as a DataFrame."""
        version = version or self.warehouse.latest_version()
        if version is None:
            raise MarketError("DATA_BRIDGE_MISSING", "数据仓库为空，请先导入种子数据")
        return frame(self.warehouse.rows("index_daily", version), self.warehouse.setting(INDEX_COLUMNS_KEY))

    def assets(self, version=None):
        version = version or self.warehouse.latest_version()
        raw = self.warehouse.static("market_assets.yaml", version) if version else None
        if raw is None:
            raise MarketError("ASSET_DEFINITION_INVALID", "数据仓库缺少资产定义")
        document, digest = load_assets_bytes(raw)
        return document, digest

    def overview(self):
        version = self.warehouse.latest_version()
        dates = sorted(self.history(version)["date"].unique()) if version else []
        assets, asset_hash = self.assets(version) if version else (None, None)
        return {"data_version": version, "versions": self.warehouse.versions(10), "jobs": self.jobs(),
                "source": self.source.get(), "assets": assets, "asset_definition_sha256": asset_hash,
                "baseline_through": dates[-1] if dates else None, "latest_closed_date": latest_closed_date(),
                "suggested_start_date": dates[-3] if len(dates) >= 3 else (dates[-1] if dates else None)}

    def jobs(self):
        jobs = self.registry.jobs()
        for job in jobs:
            if (job["status"] == "queued" and not job["pid"] and
                    (datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"])).total_seconds() > 30):
                job.update(self.registry.update_job(job["job_id"], status="failed", error_code="SYNC_WORKER_START_FAILED",
                                                   error="排队任务未启动，可重新同步"))
            if job["status"] in {"queued", "running"} and job["pid"]:
                try:
                    # A crashed worker launched by this server stays a zombie, which
                    # still answers kill(0); reap it first so it reads as exited.
                    try:
                        if os.waitpid(job["pid"], os.WNOHANG)[0]:
                            raise ProcessLookupError
                    except ChildProcessError:
                        pass
                    os.kill(job["pid"], 0)
                except ProcessLookupError:
                    job.update(self.registry.update_job(job["job_id"], status="cancelled" if job["cancel_requested"] else "failed",
                        error_code="SYNC_WORKER_EXITED", error="同步进程意外退出，可重新同步", pid=None))
        return jobs

    def create(self, request, *, launch=True):
        self.jobs()  # Recover stale workers before admitting another writer.
        source = self.source.get()
        if not source["enabled"] or not source["token_configured"]:
            raise MarketError("DATA_SOURCE_UNAVAILABLE", "请在 Setting 配置并启用 Tushare")
        if request.get("source_id", "tushare") != "tushare":
            raise MarketError("DATA_SOURCE_UNSUPPORTED", "目前仅支持 Tushare")
        mode = request.get("mode", "incremental")
        if mode not in {"incremental", "backfill"}:
            raise MarketError("INVALID_SYNC_MODE", "请选择增量更新或日期回补")
        end = iso_date(request.get("end_date") or latest_closed_date())
        if end > latest_closed_date():
            raise MarketError("MARKET_NOT_CLOSED", "仅允许同步已收盘且入库的日期")
        dates = sorted(set(self.history()["date"]))
        if len(dates) < 2:
            raise MarketError("DATA_BRIDGE_MISSING", "本地历史行情为空")
        # Re-fetch three recent trading days so supplier revisions become a new version.
        start = iso_date(request.get("start_date") if mode == "backfill" else dates[max(1, len(dates) - 3)])
        if start > end or start <= dates[0]:
            raise MarketError("INVALID_DATE_RANGE", "同步范围须有桥接历史且不能跳过未同步日期")
        if end < dates[-1]:
            raise MarketError("INVALID_DATE_RANGE", f"结束日期不能早于本地已同步日期 {dates[-1]}，否则会丢弃之后的行情")
        datasets = request.get("datasets") or ["trade_cal", "daily", "daily_basic"]
        if not {"trade_cal", "daily"}.issubset(datasets) or set(datasets) - set(FIELDS):
            raise MarketError("DATASET_UNSUPPORTED", "交易日历和日线必选；本期可选每日估值")
        job = self.registry.create_job({"source_id": "tushare", "mode": mode, "start_date": start, "end_date": end,
            "datasets": sorted(set(datasets)), "baseline_version": self.warehouse.latest_version()})
        if launch:
            env = {**os.environ, **self.settings.as_environ()}
            try:
                process = subprocess.Popen([sys.executable, "-m", "market_service.worker", "--job-id", job["job_id"]],
                    cwd=self.settings.project_root, env=env, start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Do not overwrite terminal state if the worker finishes immediately.
                with self.registry.connection() as db:
                    db.execute("UPDATE sync_jobs SET pid=? WHERE job_id=? AND status IN ('queued','running')",
                               (process.pid, job["job_id"]))
            except OSError:
                return self.registry.update_job(job["job_id"], status="failed", error_code="SYNC_WORKER_START_FAILED",
                                                error="无法启动同步进程")
        return self.registry.job(job["job_id"])

    def execute(self, job_id, provider=None):
        if not self.registry.claim(job_id, os.getpid()):
            return self.registry.job(job_id)
        job = self.registry.job(job_id)
        request = job["request"]
        stage = self.settings.path("staging", job_id)
        archive = self.settings.path("raw", job_id)

        def check():
            if self.registry.job(job_id)["cancel_requested"]:
                raise Cancelled()

        def progress(phase, value):
            check()
            self.registry.update_job(job_id, stage=phase, progress=value)

        def retry(code, delay):
            self.registry.update_job(job_id, stage="rate_limit_wait" if code == "TUSHARE_RATE_LIMITED" else "retry_wait")

        try:
            stage.mkdir(parents=True, exist_ok=False)
            provider = provider or TushareProvider(self.source.token(), check=check, on_retry=retry)
            baseline_version = request["baseline_version"]
            assets_document, asset_hash = self.assets(baseline_version)
            assets = assets_document["assets"]
            if request["start_date"] < assets_document["effective_from"]:
                raise MarketError("ASSET_DEFINITION_INVALID", "同步日期早于资产定义生效日期")
            codes = {c["ts_code"] for asset in assets.values() for c in asset["constituents"]}
            history = self.history(baseline_version)
            history = history[history["date"] < request["start_date"]].copy()
            if set(history.ts_code.unique()) != set(assets):
                raise MarketError("DATA_BRIDGE_MISSING", "桥接历史必须包含 10 个行业指数")
            bridge_date = history["date"].max()
            if any(group["date"].max() != bridge_date for _, group in history.groupby("ts_code")):
                raise MarketError("DATA_BRIDGE_MISSING", "各指数的桥接日期不一致")
            start, end = request["start_date"], request["end_date"]
            calendar_start = str(history["date"].min())
            warnings = ["资金流未接入，新增日期写入空值"]

            def record(api, params, rows):
                # Persist successful data only. No token, exception or supplier error text.
                filtered = [row for row in rows if api == "trade_cal" or row.get("ts_code") in codes]
                blob = json.dumps(filtered, ensure_ascii=False, sort_keys=True).encode()
                with gzip.open(stage / (api + ".jsonl.gz"), "ab") as handle:
                    handle.write(blob + b"\n")
                with (stage / "request_manifest.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"provider": "tushare", "api_name": api, "params": params,
                        "parameters_sha256": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest(),
                        "fields": FIELDS[api], "response_rows": len(rows), "saved_rows": len(filtered),
                        "response_sha256": hashlib.sha256(blob).hexdigest()}) + "\n")

            progress("calendar", .05)
            calendar_rows = provider.fetch("trade_cal", {"exchange": "SSE", "start_date": calendar_start.replace("-", ""),
                                           "end_date": end.replace("-", "")}, record=record)
            calendar = pd.DataFrame(calendar_rows)
            if calendar.empty:
                raise MarketError("DATA_COVERAGE_INCOMPLETE", "交易日历为空")
            calendar["cal_date"] = calendar["cal_date"].map(supplier_date)
            calendar["pretrade_date"] = calendar["pretrade_date"].map(supplier_date)
            if not calendar["is_open"].astype(str).isin(["0", "1"]).all():
                raise MarketError("SNAPSHOT_VALIDATION_FAILED", "交易日历开市标记无效")
            calendar["is_open"] = calendar["is_open"].astype(str).map({"0": "close", "1": "open"})
            calendar = calendar.sort_values("cal_date")
            expected = pd.date_range(calendar_start, end).strftime("%Y-%m-%d").tolist()
            if calendar["cal_date"].tolist() != expected:
                raise MarketError("DATA_COVERAGE_INCOMPLETE", "交易日历存在缺口、重复或越界日期")
            opened = calendar[calendar.is_open == "open"]["cal_date"].tolist()
            prefix = calendar[(calendar.cal_date < start) & (calendar.is_open == "open")]["cal_date"].tolist()
            expected_keys = {(code, day) for code in assets for day in prefix}
            if set(zip(history.ts_code, history.date)) != expected_keys or history.duplicated(["ts_code", "date"]).any():
                raise MarketError("DATA_COVERAGE_INCOMPLETE", "桥接历史缺少交易日行情或含重复数据")
            if len(opened) < 30:
                raise MarketError("DATA_COVERAGE_INCOMPLETE", "历史少于 30 个交易日，无法支持 MA30")
            trading_dates = [day for day in opened if day >= start]
            if not trading_dates:
                raise MarketError("DATA_COVERAGE_INCOMPLETE", "同步范围内没有交易日")
            daily, basics = {}, {}
            optional_available = "daily_basic" in request["datasets"]
            for index, day in enumerate(trading_dates):
                progress("fetching", .1 + .55 * index / len(trading_dates))
                params = {"trade_date": day.replace("-", "")}
                rows = provider.fetch("daily", params, record=record)
                daily[day] = self._rows_by_code(rows, codes, day)
                # Individual suspensions are normal; a broadly missing cross-section is not.
                # Fail here rather than after fetching the whole range.
                if len(daily[day]) < .8 * len(codes):
                    raise MarketError("DATA_COVERAGE_INCOMPLETE",
                                      f"{day} 仅返回 {len(daily[day])}/{len(codes)} 只成分股行情，截面数据不完整")
                if optional_available:
                    try:
                        rows = provider.fetch("daily_basic", params, record=record)
                        basics[day] = self._rows_by_code(rows, codes, day)
                    except MarketError as error:
                        if error.code != "TUSHARE_PERMISSION_DENIED":
                            raise
                        optional_available = False
                        warnings.append("每日估值无权限，新增估值字段保留空值")
            progress("aggregating", .7)
            output, aggregation_warnings, coverage = build_indices(assets, history, daily, basics, trading_dates)
            warnings.extend(aggregation_warnings)
            progress("validating", .8)
            new_rows = output[output.date >= start]
            if len(new_rows) != len(trading_dates) * 10 or output.duplicated(["ts_code", "date"]).any():
                raise MarketError("SNAPSHOT_VALIDATION_FAILED", "指数主键或交易日覆盖无效")
            if not (output["close"].gt(0) & output["pre_close"].gt(0)).all():
                raise MarketError("SNAPSHOT_VALIDATION_FAILED", "指数历史存在无效价格")
            constituents = normalize_constituents([row for group in daily.values() for row in group.values()])
            with gzip.open(stage / "constituents.jsonl.gz", "wt", encoding="utf-8") as handle:
                for row in constituents:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            calendar["exchange"] = "SSE"
            columns = self.warehouse.setting(INDEX_COLUMNS_KEY) or []
            columns = columns + [c for c in output.columns if c not in columns]
            summary = {"source": "tushare", "source_range": {"start_date": start, "end_date": end},
                "effective_through": opened[-1], "calendar_through": end, "transform_version": TRANSFORM_VERSION,
                "asset_definition_sha256": asset_hash,
                "datasets": {"daily": "available", "trade_cal": "available",
                             "daily_basic": "available" if optional_available and basics else "missing",
                             "moneyflow": "missing"},
                "warnings": warnings, "constituent_coverage_min": min(item["weight"] for item in coverage),
                "valuation_missing_values": int(new_rows[["pe_ttm", "pb", "ps_ttm", "dv_ttm"]].isna().sum().sum())}
            progress("publishing", .95)

            def publish_check():
                if self.registry.job(job_id)["cancel_requested"]:
                    raise Cancelled()

            version, changes = self.warehouse.write(job_id=job_id, kind="market_daily", summary=summary,
                rows={"index_daily": new_rows.astype(object).where(new_rows.notna(), None).to_dict("records"),
                      "calendar": calendar.astype(object).where(calendar.notna(), None).to_dict("records")},
                settings={INDEX_COLUMNS_KEY: columns}, before_commit=publish_check)
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, archive)  # Raw responses are kept for audit, next to the version they produced.
            return self.registry.update_job(job_id, status="completed", stage="completed" if changes else "unchanged",
                                            progress=1, data_version=version, pid=None)
        except Cancelled:
            return self.registry.update_job(job_id, status="cancelled", stage="cancelled", pid=None)
        except Exception as error:
            # Do not serialize exception objects from HTTP / pandas / subprocess layers.
            code = error.code if isinstance(error, MarketError) else "SYNC_FAILED"
            message = str(error) if isinstance(error, MarketError) else "同步失败，已有数据版本保持不变；可重新同步"
            return self.registry.update_job(job_id, status="failed", stage="failed", error_code=code, error=message, pid=None)
        finally:
            # Failed or cancelled jobs must not leave partial staging data behind.
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _rows_by_code(rows, codes, day):
        selected = {}
        for row in rows:
            code = row.get("ts_code")
            if code not in codes:
                continue
            if supplier_date(row.get("trade_date")) != day or code in selected:
                raise MarketError("SNAPSHOT_VALIDATION_FAILED", "响应含越界或重复成分股数据")
            selected[code] = row
        return selected
