"""When a run's output cannot count as a completed simulation.

Individual Agent failures leave a run degraded but usable. A simulated day on
which every processed Agent failed produced no decisions at all, so the run
must not be reported as completed.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .event import RuntimeEvent
from .schemas import EventType, Scope

ALL_AGENTS_FAILED = "AllAgentsFailed"


class AllAgentsFailed(RuntimeError):
    """Raised by the simulation to stop at the first day with no successful Agent."""


def all_failed_message(date: str, count: int, sample: str | None) -> str:
    return f"{date} 全部 {count} 个 Agent 处理失败" + (f"。示例错误：{sample}" if sample else "")


def first_all_failed_day(events: Iterable[RuntimeEvent]) -> str | None:
    """Return a failure message for the first day whose Agents all failed, else ``None``.

    Uses the latest attempt of each Agent: a timed-out Agent that succeeded on retry counts as succeeded.
    """
    outcome: dict[str, dict[str, tuple[bool, str | None]]] = defaultdict(dict)
    for item in events:
        if item.scope != Scope.AGENT or not item.simulation_date or not item.agent_id:
            continue
        if item.event == EventType.FAILED:
            outcome[item.simulation_date][item.agent_id] = (False, item.error_summary)
        elif item.event == EventType.COMPLETED and item.stage == "commit":
            outcome[item.simulation_date][item.agent_id] = (True, None)
    for day in sorted(outcome):
        agents = outcome[day]
        if agents and not any(ok for ok, _ in agents.values()):
            sample = next((error for _, error in agents.values() if error), None)
            return all_failed_message(day, len(agents), sample)
    return None
