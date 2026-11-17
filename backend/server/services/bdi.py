"""Abstract one Agent's run artifacts into a day-by-day Belief, Desire, Intention view.

Read-only: nothing here changes how a simulation runs. Beliefs are free text in the
artifacts, so ``belief_stance`` infers a stance with keyword rules. Against the labeled
attitudes in data/belief/belief_1000_0129.csv it agrees on 77% of beliefs and reads the
opposite stance on 7%, so callers must present the result as an inference.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from server.settings import Settings

from .llm_context import AGENT_ID, DATE, _attempt_files
from .read_only import ReadOnlyStore

TREND = re.compile(r"未来|一个月|1个月|大方向|市场趋势|后市|走势|整体趋势|接下来")
BULLISH = {
    "看涨": 3, "看多": 3, "乐观": 3, "上行": 3, "偏强": 3, "慢牛": 3, "看好": 2, "向上": 2,
    "偏暖": 2, "回暖": 2, "牛市": 2, "冲高": 2, "走强": 2, "上攻": 2, "反弹": 1,
}
BEARISH = {
    "看空": 3, "看跌": 3, "悲观": 3, "下行": 3, "偏弱": 3, "向下": 2, "回调": 2, "熊市": 2, "走弱": 2,
    "见顶": 2, "跳水": 2, "下探": 2, "谨慎态度": 2, "保持谨慎": 2, "持谨慎": 2, "调整": 1,
}
NEGATED = re.compile(r"(?:并不|不太|难以|未必|无法|缺乏|不)(看好|乐观|看涨|看多|上行|悲观|看空|看跌|下行)")
POSITIVE = {"看好", "乐观", "看涨", "看多", "上行"}
SENTENCE = re.compile(r"[。！？!?\n]")
ATTITUDES = {"乐观的": "bullish", "悲观的": "bearish", "对市场态度中性的": "neutral"}
# 持仓比例的变化小于这个百分点数时，视为维持。
SHIFT_THRESHOLD = 0.5
INACTIVE_ERROR = "用户没有被激活"


def belief_stance(text: str | None) -> dict[str, Any]:
    """Infer bullish, neutral or bearish from the first sentence about the market's direction."""
    sentences = [part.strip() for part in SENTENCE.split(text or "") if part.strip()]
    if not sentences:
        return {"stance": "unknown", "evidence": None}
    evidence = next((part for part in sentences if TREND.search(part)), sentences[0])
    reading = NEGATED.sub(lambda m: "悲观" if m.group(1) in POSITIVE else "乐观", evidence)
    reading = reading.replace("谨慎乐观", "乐观").replace("谨慎悲观", "悲观")
    bullish = sum(weight * reading.count(word) for word, weight in BULLISH.items())
    bearish = sum(weight * reading.count(word) for word, weight in BEARISH.items())
    stance = "bullish" if bullish > bearish else "bearish" if bearish > bullish else "neutral"
    return {"stance": stance, "evidence": evidence}


def _trading_days(settings: Settings) -> set[str] | None:
    try:
        with (Path(settings.data_dir) / "trading_days.csv").open(encoding="utf-8", newline="") as file:
            return {row["cal_date"] for row in csv.DictReader(file) if row.get("is_open") == "open"}
    except (OSError, KeyError):
        return None


def _initial_belief(folder: Path, agent_id: str) -> dict[str, Any] | None:
    csv.field_size_limit(10_000_000)
    for path in sorted(folder.glob("belief_*.csv")):
        try:
            with path.open(encoding="utf-8", newline="") as file:
                for row in csv.DictReader(file):
                    if str(row.get("user_id")) == agent_id:
                        inferred = belief_stance(row.get("belief"))
                        stance = ATTITUDES.get(row.get("attitude") or "", inferred["stance"])
                        return {"stance": stance, "text": row.get("belief"), "evidence": inferred["evidence"]}
        except OSError:
            continue
    return None


def _agent_events(folder: Path, agent_id: str) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    try:
        with (folder / "events.jsonl").open(encoding="utf-8") as file:
            for line in file:
                if agent_id not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("scope") == "agent" and event.get("agent_id") == agent_id and event.get("simulation_date"):
                    by_date.setdefault(event["simulation_date"], []).append(event)
    except OSError:
        pass
    return by_date


def _day_status(events: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None]:
    if not events:
        return None, None
    last = events[-1]
    if last["event"] == "stage_failed":
        error = {"stage": last.get("stage"), "code": last.get("error_code"), "summary": last.get("error_summary")}
        return "failed", error
    if last["event"] == "retrying":
        return "retrying", None
    if last["event"] == "stage_completed" and last.get("stage") == "commit":
        return "completed", None
    return "running", None


def _decisions(folder: Path, date: str, agent_id: str) -> dict[str, Any] | None:
    try:
        records = json.loads((folder / "trading_records" / f"{date}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    record = records.get(agent_id) if isinstance(records, dict) else None
    return record if isinstance(record, dict) else None


def _context_replies(folder: Path, date: str, agent_id: str) -> dict[str, str]:
    """Return the last reply per LLM step from the Agent's latest attempt on a date."""
    day = folder / "llm_context" / date
    attempts = _attempt_files(day, agent_id) if day.is_dir() else []
    if not attempts:
        return {}
    try:
        record = json.loads(attempts[-1][1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    replies = {}
    for call in record.get("calls", []):
        reply = record.get("messages", {}).get(call.get("reply_id") or "", {}).get("content")
        if reply:
            replies[call.get("step")] = reply
    return replies


def _queries(reply: str | None) -> list[str]:
    if not reply or "queries:" not in reply:
        return []
    return [line.strip() for line in re.findall(r"^\s*-\s+(.+)$", reply.split("queries:", 1)[1], re.M)]


def _intention(decision: dict[str, Any] | None, names: dict[str, str], posts: list[dict[str, Any]]) -> dict[str, Any]:
    stocks = (decision or {}).get("stock_decisions") or {}
    orders = [
        {
            "stock_id": code,
            "name": names.get(code),
            "action": item.get("action"),
            "quantity": item.get("quantity"),
            "target_price": item.get("target_price"),
            "target_position": item.get("target_position"),
            "cur_position": item.get("cur_position"),
        }
        for code, item in stocks.items()
        if isinstance(item, dict) and item.get("action") in {"buy", "sell"}
    ]
    actions = {order["action"] for order in orders}
    if len(actions) == 2:
        direction = "mixed"
    elif actions:
        direction = actions.pop()
    elif stocks or (decision and decision.get("error") != INACTIVE_ERROR):
        direction = "hold"
    else:
        direction = "none"
    original = next((post for post in posts if post["type"] != "repost"), None)
    return {
        "direction": direction,
        "orders": orders,
        "posted": original is not None,
        "post": {"type": original["type"], "content": original["content"]} if original else None,
        "reposts": sum(post["type"] == "repost" for post in posts),
    }


def _desire(
    decision: dict[str, Any] | None,
    held: float | None,
    previous_focus: set[str] | None,
    queries: list[str],
) -> dict[str, Any]:
    stocks = (decision or {}).get("stock_decisions") or {}
    focus = set(stocks)
    # 没有决策的日子不算移出关注：Agent 只是当天没有表态。
    compared = previous_focus if stocks and previous_focus is not None else focus
    delta = None
    if decision is None:
        shift = "unknown"
    elif decision.get("error") == INACTIVE_ERROR:
        shift = "inactive"
    else:
        delta = round(
            sum(
                float(item.get("target_position") or 0) - float(item.get("cur_position") or 0)
                for item in stocks.values()
                if isinstance(item, dict) and item.get("action") in {"buy", "sell"}
            ),
            2,
        )
        shift = "increase" if delta > SHIFT_THRESHOLD else "decrease" if delta < -SHIFT_THRESHOLD else "maintain"
    return {
        "shift": shift,
        "position_delta": delta,
        "held_position": held,
        # 下单比例按当日估值计算，与快照仓位相加可能略超 100%，这里只取可达范围。
        "target_position": (
            round(min(100.0, max(0.0, held + delta)), 2) if held is not None and delta is not None else None
        ),
        "focus_added": sorted(focus - compared),
        "focus_removed": sorted(compared - focus),
        "focus": sorted(focus),
        "queries": queries,
    }


def agent_bdi(settings: Settings, output_dir: Path, agent_id: str) -> dict[str, Any]:
    """Return each simulated day's Belief, Desire and Intention, compared to the last trading day."""
    if not AGENT_ID.match(agent_id):
        raise ValueError("agent_id 格式无效")
    store = ReadOnlyStore(settings)
    folder = store.local(output_dir)
    user_db = store.database(folder, "user*.db")
    forum_db = store.database(folder, "forum*.db")

    snapshots = store.rows(
        user_db,
        """SELECT substr(created_at, 1, 10) day, current_cash, total_value FROM Profiles
        WHERE user_id = ? ORDER BY created_at""",
        (agent_id,),
    )
    if not snapshots:
        raise KeyError("Unknown member")
    position = {
        row["day"]: round(min(100.0, max(0.0, 100 * (1 - row["current_cash"] / row["total_value"]))), 2)
        for row in snapshots
        if row["total_value"] and row["current_cash"] is not None
    }
    names = {row["stock_id"]: row["name"] for row in store.rows(user_db, "SELECT stock_id, name FROM StockProfile")}

    posts_by_day: dict[str, list[dict[str, Any]]] = {}
    if forum_db:
        for post in store.rows(
            forum_db,
            """SELECT substr(created_at, 1, 10) day, type, content, belief FROM posts
            WHERE user_id = ? ORDER BY created_at, id""",
            (agent_id,),
        ):
            posts_by_day.setdefault(post["day"], []).append(post)

    events = _agent_events(folder, agent_id)
    record_days = {path.stem for path in folder.glob("trading_records/*.json") if DATE.match(path.stem)}
    context_days = {
        path.parent.name for path in folder.glob(f"llm_context/*/{agent_id}*.json") if DATE.match(path.parent.name)
    }
    baseline = snapshots[0]["day"]
    snapshot_days = {row["day"] for row in snapshots if row["day"] > baseline}
    dates = sorted(set(events) | set(posts_by_day) | record_days | context_days | snapshot_days)

    calendar = _trading_days(settings)
    initial = _initial_belief(folder, agent_id)
    current = {"stance": "unknown", "text": None, "evidence": None}
    if initial:
        current = {**initial}
    compared = {"date": None, "stance": current["stance"]}
    previous_focus: set[str] | None = None
    held_day = baseline
    days = []
    for date in dates:
        posts = posts_by_day.get(date, [])
        replies = _context_replies(folder, date, agent_id)
        written = next((post["belief"] for post in posts if post["belief"] and post["type"] != "repost"), None)
        text, source = (written, "post") if written else (replies.get("_update_belief"), "llm")
        if text:
            current = {"text": text, **belief_stance(text)}
        else:
            source = "carried"
        trading = calendar is None or date in calendar
        decision = _decisions(folder, date, agent_id) if trading else None
        status, error = _day_status(events.get(date, []))
        days.append(
            {
                "date": date,
                "trading": trading,
                "status": status,
                "error": error,
                "previous": dict(compared),
                "belief": {**current, "source": source, "changed": current["stance"] != compared["stance"]},
                "desire": _desire(
                    decision,
                    position.get(held_day),
                    previous_focus,
                    _queries(replies.get("_desire_agent")),
                ),
                "intention": _intention(decision, names, posts),
            }
        )
        if date in position:
            held_day = date
        if trading:
            compared = {"date": date, "stance": current["stance"]}
            stocks = (decision or {}).get("stock_decisions")
            previous_focus = set(stocks) if stocks else previous_focus
    return {"agent_id": agent_id, "initial": initial, "days": days}
