"""One-time import of the repository's existing market files into an empty warehouse.

Preference order for prices: the newest published market snapshot (it already
bridges the legacy history and adds every synced day), else the legacy CSVs.
Legacy news always comes from ``sorted_impact_news.pkl`` in the seed directory;
snapshot news containers were deliberately empty and are ignored.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .assets import load_assets_bytes
from .errors import MarketError
from .warehouse import INDEX_COLUMNS_KEY, Warehouse

STATIC_FILES = ("market_assets.yaml", "stock_profile.csv")
LEGACY_NEWS = "sorted_impact_news.pkl"


def newest_snapshot(seed_dir: Path):
    candidates = []
    for metadata_file in seed_dir.glob("market_snapshots/*/snapshot.json"):
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if metadata.get("status") in {"ready", "ready_with_warnings"}:
            candidates.append((metadata["effective_through"], metadata["as_of"], metadata_file.parent, metadata))
    return max(candidates, key=lambda item: item[:2])[2:] if candidates else (None, None)


def article_id(source, content):
    return hashlib.sha256(f"{source}|{content}".encode()).hexdigest()[:24]


def legacy_articles(path: Path):
    """Day-precision articles; ``rank_hint`` keeps the original within-day order."""
    frame = pd.read_pickle(path)
    if not {"cal_date", "news"}.issubset(frame.columns):
        raise MarketError("SEED_INVALID", f"{path.name} 缺少 cal_date/news 列")
    articles, seen = [], set()
    for row in frame.itertuples(index=False):
        day = pd.Timestamp(row.cal_date).strftime("%Y-%m-%d")
        for rank, text in enumerate(row.news if isinstance(row.news, list) else []):
            if not isinstance(text, str) or not text.strip():
                continue
            content = text.strip()
            identifier = article_id("legacy", content)
            if identifier in seen:
                continue
            seen.add(identifier)
            articles.append({"article_id": identifier, "source": "legacy", "channel": path.name,
                             "published_at": f"{day}T00:00:00+08:00", "time_precision": "day",
                             "title": content[:80], "content": content,
                             "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                             "url": None, "ts_codes": [], "synthetic": False, "rank_hint": rank})
    return articles


def seed(settings, warehouse: Warehouse | None = None):
    """Import once; return the created version, or ``None`` when the warehouse is not empty."""
    warehouse = warehouse or Warehouse(settings)
    if warehouse.latest_version() is not None:
        return None
    folder, metadata = newest_snapshot(settings.seed_dir)
    source = folder or settings.seed_dir
    try:
        stock = pd.read_csv(source / "stock_data.csv")
        calendar = pd.read_csv(source / "trading_days.csv")
        static = {name: (source / name).read_bytes() for name in STATIC_FILES}
    except OSError:
        raise MarketError("SEED_MISSING", f"种子目录 {source} 缺少行情、交易日历或资产定义文件") from None
    load_assets_bytes(static["market_assets.yaml"])
    calendar["exchange"] = "SSE"
    news_path = settings.seed_dir / LEGACY_NEWS
    articles = legacy_articles(news_path) if news_path.is_file() else []
    warnings = ["旧版新闻只有日期精度，按当日 00:00 计入，可能含日内前视"] if articles else []
    summary = {"source": f"snapshot {metadata['snapshot_id']}" if metadata else "legacy files",
               "effective_through": str(stock["date"].max()), "warnings": warnings,
               "news_articles": len(articles)}
    version, _ = warehouse.write(
        job_id=None, kind="seed", summary=summary,
        rows={"index_daily": stock.astype(object).where(stock.notna(), None).to_dict("records"),
              "calendar": calendar.astype(object).where(calendar.notna(), None).to_dict("records"),
              "news_articles": articles},
        static=static, settings={INDEX_COLUMNS_KEY: list(stock.columns)})
    return version
