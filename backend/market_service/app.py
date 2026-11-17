"""HTTP API of the market data service (``/v1``), used by Studio and the simulation backend."""
from __future__ import annotations

import hmac
import io
import json
import sqlite3
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .bundles import BundleService
from .errors import MarketError
from .registry import MarketRegistry
from .seed import seed
from .settings import MdsSettings, get_settings
from .sync import MarketSyncService
from .templates import ensure_builtin, parse_ref, publish, summary


class DataSourceUpdate(BaseModel):
    token: str | None = Field(default=None, max_length=2000)
    enabled: bool = True


class SyncCreate(BaseModel):
    source_id: str = "tushare"
    mode: str = "incremental"
    start_date: str | None = None
    end_date: str | None = None
    datasets: list[str] = Field(default_factory=lambda: ["trade_cal", "daily", "daily_basic"])


class PlanRequest(BaseModel):
    template: str = Field(min_length=1, max_length=80)
    start_date: str
    end_date: str
    data_version: int | None = None


class DefaultTemplate(BaseModel):
    template_id: str = Field(min_length=1, max_length=80)


def create_app(settings: MdsSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    sync = MarketSyncService(settings)
    registry, warehouse = sync.registry, sync.warehouse
    bundles = BundleService(settings, registry, warehouse)
    ensure_builtin(registry)
    seed(settings, warehouse)

    def authorized(authorization: str | None = Header(default=None)):
        # Local deployments may leave the token unset; when set, every /v1 call needs it.
        if settings.token and not hmac.compare_digest(authorization or "", f"Bearer {settings.token}"):
            raise MarketError("MDS_UNAUTHORIZED", "市场数据服务拒绝了未授权的请求", 401)

    app = FastAPI(title="TwinMarket Market Data Service", version="1.0")
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["*"])

    @app.exception_handler(MarketError)
    async def market_error(_, error):
        return JSONResponse(status_code=error.status, content={"code": error.code, "error": str(error)})

    @app.exception_handler(sqlite3.Error)
    async def unavailable(_, __):
        return JSONResponse(status_code=503, content={"code": "MDS_STORAGE_UNAVAILABLE", "error": "数据仓库暂不可用，请稍后重试"})

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    # Mounted sub-application so one dependency guards every /v1 route.
    v1 = FastAPI(dependencies=[Depends(authorized)])

    def datasets(version):
        if version is None:
            return {}
        news = {}
        with warehouse.connection() as db:
            for source, low, high, count in db.execute(
                    "SELECT source, min(published_at), max(published_at), count(*) FROM news_articles "
                    "WHERE valid_from<=? AND (valid_to IS NULL OR valid_to>?) GROUP BY source", (version, version)):
                news[source] = {"from": low[:10], "through": high[:10], "rows": count}
        return {"calendar": warehouse.bounds("calendar", "cal_date", version),
                "index_daily": warehouse.bounds("index_daily", "date", version), "news": news}

    def template_items():
        default = registry.setting("default_template")
        latest = {}
        for item in registry.templates():
            latest[item["template_id"]] = item
        return [summary(item, default) for item in latest.values()]

    @v1.get("/health")
    def health():
        version = warehouse.latest_version()
        return {"status": "ok", "data_version": version, "datasets": datasets(version),
                "running_jobs": [job for job in sync.jobs() if job["status"] in {"queued", "running"}]}

    @v1.get("/overview")
    def overview():
        result = sync.overview()
        return {**result, "datasets": datasets(result["data_version"]), "templates": template_items()}

    @v1.get("/sources/tushare")
    def source():
        return sync.source.get()

    @v1.put("/sources/tushare")
    def update_source(body: DataSourceUpdate):
        return sync.source.update(body.model_dump(exclude_unset=True))

    @v1.post("/sources/tushare/test")
    def test_source():
        return sync.source.test()

    @v1.get("/jobs")
    def jobs():
        return {"items": sync.jobs()}

    @v1.post("/jobs", status_code=202)
    def create_job(body: SyncCreate):
        return sync.create(body.model_dump())

    @v1.get("/jobs/{job_id}")
    def job(job_id: str):
        sync.jobs()
        return registry.job(job_id)

    @v1.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return registry.cancel(job_id)

    @v1.get("/versions")
    def versions(limit: int = Query(30, ge=1, le=200)):
        return {"items": warehouse.versions(limit)}

    @v1.get("/market/series")
    def series(asset: str | None = None, data_version: int | None = None):
        version = data_version or warehouse.latest_version()
        if version is None or warehouse.version(version) is None:
            raise MarketError("DATA_VERSION_NOT_FOUND", "数据版本不存在", 404)
        profile = pd.read_csv(io.BytesIO(warehouse.static("stock_profile.csv", version))).sort_values("weight", ascending=False)
        assets = profile[["stock_id", "name", "industry", "weight", "description"]].to_dict("records")
        ids = [item["stock_id"] for item in assets]
        selected = asset if asset in ids else (ids[0] if ids else "")
        rows = warehouse.rows("index_daily", version, "ts_code=?", (selected,))[-180:]
        prices = [{"date": row["date"], "close_price": row.get("close"), "pct_chg": row.get("pct_chg"),
                   "vol": row.get("vol")} for row in rows]
        return {"assets": assets, "selected": selected, "prices": prices, "data_version": version,
                "source": f"市场数据服务 · 数据版本 {version}"}

    @v1.get("/news")
    def news(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
             source: str | None = None, q: str | None = Query(None, max_length=100),
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        version = warehouse.latest_version()
        clauses, params = ["valid_from<=?", "(valid_to IS NULL OR valid_to>?)"], [version, version]
        if start:
            clauses.append("published_at>=?"), params.append(start)
        if end:
            clauses.append("published_at<?"), params.append(end + "T99")  # Inclusive end day.
        if source:
            clauses.append("source=?"), params.append(source)
        if q:
            clauses.append("json_extract(row, '$.content') LIKE ?"), params.append(f"%{q}%")
        where = " AND ".join(clauses)
        with warehouse.connection() as db:
            total = db.execute(f"SELECT count(*) FROM news_articles WHERE {where}", params).fetchone()[0]
            rows = db.execute(f"SELECT row FROM news_articles WHERE {where} ORDER BY published_at DESC, article_id "
                              "LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "total": total, "data_version": version}

    @v1.get("/templates")
    def templates():
        return {"items": template_items(), "default": registry.setting("default_template")}

    @v1.post("/templates", status_code=201)
    def publish_template(body: dict[str, Any]):
        return summary(publish(registry, body), registry.setting("default_template"))

    @v1.put("/templates/default")
    def set_default(body: DefaultTemplate):
        registry.template(body.template_id)
        registry.set_setting("default_template", body.template_id)
        return {"default": body.template_id}

    @v1.get("/templates/{ref}")
    def template(ref: str):
        template_id, version = parse_ref(ref)
        default = registry.setting("default_template")
        item = summary(registry.template(template_id, version), default)
        item["versions"] = [entry["version"] for entry in registry.templates() if entry["template_id"] == template_id]
        return item

    @v1.post("/plans")
    def plan(body: PlanRequest):
        return bundles.plan(body.template, body.start_date, body.end_date, body.data_version)

    @v1.get("/bundles")
    def bundle_list(limit: int = Query(50, ge=1, le=200)):
        return {"items": registry.bundles(limit)}

    @v1.post("/bundles", status_code=201)
    def bundle_create(body: PlanRequest):
        return bundles.create(body.template, body.start_date, body.end_date, body.data_version)

    @v1.get("/bundles/{bundle_id}")
    def bundle(bundle_id: str):
        return registry.bundle(bundle_id)

    @v1.get("/bundles/{bundle_id}/archive")
    def bundle_archive(bundle_id: str):
        return FileResponse(bundles.archive(bundle_id), media_type="application/gzip", filename=bundle_id + ".tar.gz")

    @v1.put("/bundles/{bundle_id}/refs/{run_id}")
    def add_ref(bundle_id: str, run_id: str):
        return registry.add_ref(bundle_id, run_id[:64])

    @v1.delete("/bundles/{bundle_id}/refs/{run_id}")
    def remove_ref(bundle_id: str, run_id: str):
        return registry.remove_ref(bundle_id, run_id)

    v1.add_exception_handler(MarketError, market_error)
    v1.add_exception_handler(sqlite3.Error, unavailable)
    app.mount("/v1", v1)
    return app
