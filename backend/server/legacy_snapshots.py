"""Read-only access to market snapshots pinned by runs created before the market data service.

New runs use market templates and bundles. Existing snapshot runs keep working:
their manifest recorded every snapshot file fingerprint, which is all that is
needed to verify the published directory before a start or resume.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .market_bundle import MarketInputError


def fingerprint(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def resolve_pinned_snapshot(settings, snapshot_id, folder: Path):
    """Verify a pinned snapshot against the run manifest; return ``(environment, directory)``."""
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"market-[a-z0-9-]{1,80}", snapshot_id):
        raise MarketInputError("SNAPSHOT_NOT_FOUND", "市场快照标识无效", 404)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MarketInputError("SNAPSHOT_INTEGRITY_FAILED", "运行清单缺失，无法校验市场快照") from None
    environment = manifest.get("market_environment", {})
    if environment.get("snapshot_id") != snapshot_id or not environment.get("files"):
        raise MarketInputError("SNAPSHOT_INTEGRITY_FAILED", "运行清单与市场快照不一致")
    path = settings.data_path("market_snapshots", snapshot_id)
    try:
        for name, expected in environment["files"].items():
            target = (path / name).resolve()
            if not target.is_relative_to(path) or fingerprint(target) != expected:
                raise MarketInputError("SNAPSHOT_INTEGRITY_FAILED", "快照文件指纹不匹配")
    except OSError:
        raise MarketInputError("SNAPSHOT_INTEGRITY_FAILED", "快照文件缺失或损坏") from None
    return {"mode": "snapshot", "id": snapshot_id, "manifest": environment,
            "warnings": manifest.get("research_warnings", [])}, path
