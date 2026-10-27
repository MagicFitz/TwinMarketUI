"""Service metadata (jobs, templates, bundles); credentials are deliberately stored elsewhere."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .errors import MarketError


def now():
    return datetime.now(timezone.utc).isoformat()


class MarketRegistry:
    def __init__(self, settings):
        self.path = settings.path("mds.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sync_jobs (
                    job_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'market', request TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    error_code TEXT, error TEXT, data_version INTEGER, pid INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS market_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS templates (
                    template_id TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    body TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    PRIMARY KEY (template_id, version));
                CREATE TABLE IF NOT EXISTS bundles (
                    bundle_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bundle_refs (
                    bundle_id TEXT NOT NULL, run_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (bundle_id, run_id));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM market_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO market_settings VALUES (?,?)", (key, json.dumps(value)))

    # ------------------------------------------------------------------ jobs

    def create_job(self, request, kind="market"):
        stamp, job_id = now(), "sync-" + uuid4().hex[:16]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # One writer for the whole warehouse; also keeps Tushare calls from competing for rate limits.
            if db.execute("SELECT 1 FROM sync_jobs WHERE status IN ('queued','running')").fetchone():
                raise MarketError("SYNC_JOB_CONFLICT", "已有同步任务正在执行", 409)
            db.execute("INSERT INTO sync_jobs(job_id,kind,request,status,stage,created_at,updated_at) "
                       "VALUES (?,?,?,'queued','queued',?,?)", (job_id, kind, json.dumps(request), stamp, stamp))
        return self.job(job_id)

    @staticmethod
    def _job(row):
        item = dict(row)
        item["request"] = json.loads(item["request"])
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item

    def job(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM sync_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise MarketError("SYNC_JOB_NOT_FOUND", "同步任务不存在", 404)
        return self._job(row)

    def jobs(self):
        with self.connection() as db:
            return [self._job(row) for row in db.execute("SELECT * FROM sync_jobs ORDER BY created_at DESC LIMIT 30")]

    def update_job(self, job_id, **values):
        allowed = {"status", "stage", "progress", "error_code", "error", "pid", "data_version"}
        if not set(values).issubset(allowed):
            raise ValueError("unsupported job field")
        values["updated_at"] = now()
        with self.connection() as db:
            db.execute("UPDATE sync_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE job_id=?",
                       (*values.values(), job_id))
        return self.job(job_id)

    def claim(self, job_id, pid):
        with self.connection() as db:
            cursor = db.execute("UPDATE sync_jobs SET status='running', pid=?, updated_at=? "
                "WHERE job_id=? AND status='queued' AND cancel_requested=0", (pid, now(), job_id))
            return cursor.rowcount == 1

    def cancel(self, job_id):
        self.job(job_id)
        with self.connection() as db:
            db.execute("UPDATE sync_jobs SET cancel_requested=1, updated_at=?, "
                "status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END "
                "WHERE job_id=? AND status IN ('queued','running')", (now(), job_id))
        return self.job(job_id)

    # ------------------------------------------------------------- templates

    def templates(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM templates ORDER BY template_id, version").fetchall()
        return [self._template(row) for row in rows]

    def template(self, template_id, version=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM templates WHERE template_id=? ORDER BY version DESC LIMIT 1", (template_id,)
                             ).fetchone() if version is None else db.execute(
                "SELECT * FROM templates WHERE template_id=? AND version=?", (template_id, version)).fetchone()
        if row is None:
            raise MarketError("TEMPLATE_NOT_FOUND", "市场模板不存在", 404)
        return self._template(row)

    @staticmethod
    def _template(row):
        return {"template_id": row["template_id"], "version": row["version"], "sha256": row["sha256"],
                "builtin": bool(row["builtin"]), "created_at": row["created_at"], "body": json.loads(row["body"])}

    def insert_template(self, template_id, version, sha256, body, builtin=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT sha256 FROM templates WHERE template_id=? AND version=?",
                                  (template_id, version)).fetchone()
            if existing:
                if existing[0] != sha256:
                    raise MarketError("TEMPLATE_VERSION_CONFLICT", f"模板 {template_id}@{version} 已发布且内容不同，请提升版本号", 409)
                return False
            db.execute("INSERT INTO templates VALUES (?,?,?,?,?,?)",
                       (template_id, version, sha256, json.dumps(body, ensure_ascii=False), int(builtin), now()))
        return True

    # --------------------------------------------------------------- bundles

    def bundle(self, bundle_id):
        with self.connection() as db:
            row = db.execute("SELECT metadata FROM bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
            refs = [r[0] for r in db.execute("SELECT run_id FROM bundle_refs WHERE bundle_id=?", (bundle_id,))]
        if row is None:
            raise MarketError("BUNDLE_NOT_FOUND", "数据包不存在", 404)
        return {**json.loads(row[0]), "refs": refs}

    def bundles(self, limit=50):
        with self.connection() as db:
            rows = db.execute("SELECT b.metadata, count(r.run_id) FROM bundles b LEFT JOIN bundle_refs r USING(bundle_id) "
                              "GROUP BY b.bundle_id ORDER BY b.created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**json.loads(row[0]), "ref_count": row[1]} for row in rows]

    def add_ref(self, bundle_id, run_id):
        self.bundle(bundle_id)
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO bundle_refs VALUES (?,?,?)", (bundle_id, run_id, now()))
        return self.bundle(bundle_id)

    def remove_ref(self, bundle_id, run_id):
        with self.connection() as db:
            db.execute("DELETE FROM bundle_refs WHERE bundle_id=? AND run_id=?", (bundle_id, run_id))
        return self.bundle(bundle_id)
