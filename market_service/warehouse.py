"""Versioned local warehouse: every successful write creates one ``data_version``.

Rows are never updated in place. A supplier revision closes the current row
(``valid_to``) and inserts a new one (``valid_from``) inside the same
transaction that records the version, so any historical version can be read
back exactly. Bundles only need to remember the version they were built from.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager

from .registry import now

# table -> (key columns, extra indexed columns copied out of the row)
TABLES = {
    "calendar": (("exchange", "cal_date"), ()),
    "index_daily": (("ts_code", "date"), ()),
    "news_articles": (("article_id",), ("published_at", "source")),
}
# Compatibility file column order, recorded so bundles reproduce the seed layout.
INDEX_COLUMNS_KEY = "index_columns"


def clean(value):
    """JSON-safe scalar: NaN/inf become null, numpy scalars become Python values."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def payload(row):
    return json.dumps({key: clean(value) for key, value in row.items()}, ensure_ascii=False, sort_keys=True,
                      allow_nan=False)


class Warehouse:
    def __init__(self, settings):
        self.path = settings.path("warehouse.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS data_versions (
                data_version INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                job_id TEXT, kind TEXT NOT NULL, summary TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS static_files (
                name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
                valid_from INTEGER NOT NULL, valid_to INTEGER)""")
            db.execute("CREATE INDEX IF NOT EXISTS static_files_open ON static_files(name, valid_to)")
            db.execute("CREATE TABLE IF NOT EXISTS warehouse_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for table, (keys, indexed) in TABLES.items():
                columns = ", ".join(f"{name} TEXT NOT NULL" for name in (*keys, *indexed))
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns}, row TEXT NOT NULL, "
                           "valid_from INTEGER NOT NULL, valid_to INTEGER)")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_open ON {table}({', '.join(keys)}, valid_to)")
                for name in indexed:
                    db.execute(f"CREATE INDEX IF NOT EXISTS {table}_{name} ON {table}({name})")

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

    # ---------------------------------------------------------------- versions

    def latest_version(self):
        with self.connection() as db:
            return db.execute("SELECT max(data_version) FROM data_versions").fetchone()[0]

    def versions(self, limit=30):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM data_versions ORDER BY data_version DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "summary": json.loads(row["summary"])} for row in rows]

    def version(self, data_version):
        with self.connection() as db:
            row = db.execute("SELECT * FROM data_versions WHERE data_version=?", (data_version,)).fetchone()
        return {**dict(row), "summary": json.loads(row["summary"])} if row else None

    def setting(self, key, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM warehouse_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def write(self, *, job_id, kind, summary, rows=None, static=None, settings=None, before_commit=None):
        """Apply all changes as one new version; return ``(version, changes)``.

        ``rows`` maps a table to dict rows. Unchanged rows are skipped, and a write
        that changes nothing does not create a version (the latest is returned).
        ``before_commit`` runs inside the write lock, e.g. a final cancellation check.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("INSERT INTO data_versions(created_at,job_id,kind,summary) VALUES (?,?,?,?)",
                                 (now(), job_id, kind, "{}")).lastrowid
            changes = {}
            for table, items in (rows or {}).items():
                changes[table] = self._apply(db, table, items, version)
            for name, content in (static or {}).items():
                digest = hashlib.sha256(content).hexdigest()
                current = db.execute("SELECT sha256 FROM static_files WHERE name=? AND valid_to IS NULL", (name,)).fetchone()
                if current and current[0] == digest:
                    continue
                db.execute("UPDATE static_files SET valid_to=? WHERE name=? AND valid_to IS NULL", (version, name))
                db.execute("INSERT INTO static_files VALUES (?,?,?,?,NULL)", (name, digest, content, version))
                changes.setdefault("static_files", {"inserted": 0, "revised": 0})["inserted"] += 1
            for key, value in (settings or {}).items():
                db.execute("INSERT OR REPLACE INTO warehouse_settings VALUES (?,?)", (key, json.dumps(value)))
            if before_commit:
                before_commit()
            if not any(item["inserted"] or item["revised"] for item in changes.values()):
                db.rollback()
                return self.latest_version(), {}
            db.execute("UPDATE data_versions SET summary=? WHERE data_version=?",
                       (json.dumps({**summary, "changes": changes}, ensure_ascii=False), version))
        return version, changes

    @staticmethod
    def _apply(db, table, items, version):
        keys, indexed = TABLES[table]
        current = {tuple(row[:len(keys)]): row[-1] for row in db.execute(
            f"SELECT {', '.join(keys)}, row FROM {table} WHERE valid_to IS NULL")}
        inserted = revised = 0
        for item in items:
            key = tuple(str(item[name]) for name in keys)
            body = payload(item)
            previous = current.get(key)
            if previous == body:
                continue
            if previous is not None:
                db.execute(f"UPDATE {table} SET valid_to=? WHERE {' AND '.join(f'{k}=?' for k in keys)} AND valid_to IS NULL",
                           (version, *key))
                revised += 1
            else:
                inserted += 1
            db.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * (len(keys) + len(indexed) + 3))})",
                       (*key, *(str(item[name]) for name in indexed), body, version, None))
            current[key] = body
        return {"inserted": inserted, "revised": revised}

    # ------------------------------------------------------------------ reads

    def rows(self, table, version, where="", params=()):
        """Rows visible at ``version``, ordered by key."""
        keys, _ = TABLES[table]
        sql = (f"SELECT row FROM {table} WHERE valid_from<=? AND (valid_to IS NULL OR valid_to>?)"
               + (f" AND ({where})" if where else "") + f" ORDER BY {', '.join(keys)}")
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(sql, (version, version, *params))]

    def static(self, name, version):
        with self.connection() as db:
            row = db.execute("SELECT content FROM static_files WHERE name=? AND valid_from<=? "
                             "AND (valid_to IS NULL OR valid_to>?)", (name, version, version)).fetchone()
        return bytes(row[0]) if row else None

    def bounds(self, table, column, version, where="", params=()):
        sql = (f"SELECT min({column}), max({column}), count(*) FROM {table} WHERE valid_from<=? "
               "AND (valid_to IS NULL OR valid_to>?)" + (f" AND ({where})" if where else ""))
        with self.connection() as db:
            low, high, count = db.execute(sql, (version, version, *params)).fetchone()
        return {"from": low, "through": high, "rows": count}

    def bar_counts(self, version):
        """``{date: number of index rows}`` visible at ``version``."""
        with self.connection() as db:
            return dict(db.execute("SELECT date, count(*) FROM index_daily WHERE valid_from<=? "
                                   "AND (valid_to IS NULL OR valid_to>?) GROUP BY date", (version, version)).fetchall())

    @staticmethod
    def news_filter(sources, include_synthetic):
        """``where`` clause and params for news in ``(low, high]`` from ``sources``."""
        where = f"published_at>? AND published_at<=? AND source IN ({','.join('?' * len(sources))})"
        return where + ("" if include_synthetic else " AND json_extract(row, '$.synthetic')=0")

    def news_times(self, version, low, high, sources, include_synthetic=False):
        """``(published_at, time_precision)`` of visible news; cheap enough for coverage checks."""
        if not sources:
            return []
        sql = ("SELECT published_at, json_extract(row, '$.time_precision') FROM news_articles WHERE valid_from<=? "
               "AND (valid_to IS NULL OR valid_to>?) AND " + self.news_filter(sources, include_synthetic))
        with self.connection() as db:
            return [tuple(row) for row in db.execute(sql, (version, version, low, high, *sources))]
