"""Read the per-Agent LLM context records written by runtime.llm_context."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ATTEMPT_FILE = re.compile(r"^(?P<agent>[A-Za-z0-9_-]+?)(?:_a(?P<attempt>\d+))?\.json$")


def _attempt_files(folder: Path, agent_id: str) -> list[tuple[int, Path]]:
    found = []
    for path in folder.glob(f"{agent_id}*.json"):
        match = ATTEMPT_FILE.match(path.name)
        if match and match.group("agent") == agent_id:
            found.append((int(match.group("attempt") or 1), path))
    return sorted(found)


def agent_llm_context(output_dir: Path, agent_id: str, date: str | None = None) -> dict[str, Any]:
    """Return every recorded attempt for one Agent on one simulation date (latest date by default)."""
    if not AGENT_ID.match(agent_id) or (date is not None and not DATE.match(date)):
        raise ValueError("agent_id 或日期格式无效")
    root = output_dir / "llm_context"
    dates = sorted(
        (child.name for child in root.iterdir() if child.is_dir() and DATE.match(child.name)
         and _attempt_files(child, agent_id)),
        reverse=True,
    ) if root.is_dir() else []
    chosen = date or (dates[0] if dates else None)
    attempts = []
    if chosen:
        for _, path in _attempt_files(root / chosen, agent_id):
            try:
                attempts.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue  # 正在写入或损坏；下次轮询再读
    return {"agent_id": agent_id, "date": chosen, "dates": dates, "attempts": attempts}
