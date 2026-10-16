"""Deterministic reducer from persisted events to the live UI snapshot."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .event import RuntimeEvent
from .schemas import AGENT_STAGES, DAY_STAGES, EventType


EVENT_STATUS = {
    EventType.STARTED: "running", EventType.COMPLETED: "completed", EventType.SKIPPED: "skipped",
    EventType.FAILED: "failed", EventType.RETRYING: "retrying",
}


class RuntimeSnapshotReducer:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.last_event_id = 0
        self.run: dict[str, Any] = {"status": "pending", "health": "healthy"}
        self.day: dict[str, Any] = {"date": None, "stages": {stage: {"status": "pending"} for stage in DAY_STAGES}}
        self.agents: dict[str, dict[str, Any]] = {}
        self.workers: dict[str, dict[str, Any]] = {}
        self.diagnostics: list[dict[str, Any]] = []
        self.today_summary = Counter({"buy": 0, "sell": 0, "hold": 0, "orders": 0, "posts": 0, "interactions": 0, "failures": 0})

    def apply(self, event: RuntimeEvent) -> None:
        if event.event_id <= self.last_event_id:
            return
        self.last_event_id = event.event_id
        status = EVENT_STATUS[event.event]
        base = {"status": status, "substage": event.substage, "duration_ms": event.duration_ms,
                "attempt": event.attempt, "message": event.message, "input_summary": event.input_summary,
                "output_summary": event.output_summary, "started_at": event.started_at,
                "updated_at": event.ended_at or event.started_at,
                "error_code": event.error_code, "error_summary": event.error_summary}
        if event.scope.value == "run":
            self.run.update(base)
            if status == "failed": self.run["health"] = "degraded"
        elif event.scope.value == "day":
            if event.stage == "prepare" and event.event == EventType.STARTED and event.simulation_date != self.day.get("date"):
                self.today_summary = Counter({"buy": 0, "sell": 0, "hold": 0, "orders": 0, "posts": 0, "interactions": 0, "failures": 0})
            if event.simulation_date: self.day["date"] = event.simulation_date
            self.day["stages"].setdefault(event.stage, {}).update(base)
        elif event.scope.value == "agent" and event.agent_id:
            agent = self.agents.get(event.agent_id)
            if agent is None or (event.simulation_date and agent.get("simulation_date") != event.simulation_date):
                # 每个模拟日重新开始一条 Agent 流水线，避免显示前一天的阶段状态。
                agent = self.agents[event.agent_id] = {
                    "agent_id": event.agent_id, "simulation_date": event.simulation_date, "status": "pending",
                    "current_stage": None, "stage_started_at": None, "attempt": 1, "error": None,
                    "stages": {stage: {"status": "pending"} for stage in AGENT_STAGES}}
            if event.attempt > agent["attempt"]:
                # 超时重试从头再跑一遍流水线；保留上一次的错误直到重试成功。
                agent["attempt"] = event.attempt
                agent["stages"] = {stage: {"status": "pending"} for stage in AGENT_STAGES}
            if event.attempt == agent["attempt"]:
                agent["stages"].setdefault(event.stage, {}).update(base)
                agent.update({"worker_id": event.worker_id, "last_stage": event.stage, "updated_at": base["updated_at"]})
                self._update_agent_status(agent, event, status)
            if event.worker_id:
                self.workers[event.worker_id] = {"worker_id": event.worker_id, "agent_id": event.agent_id, "stage": event.stage, **base}
            if event.stage == "action" and event.event == EventType.COMPLETED:
                for key, name in (("buy", "buy"), ("sell", "sell"), ("hold", "hold"), ("orders", "orders")):
                    self.today_summary[name] += int(event.output_summary.get(key, 0) or 0)
            if event.event == EventType.FAILED:
                self.today_summary["failures"] += 1
        elif event.scope.value == "social" and event.event == EventType.COMPLETED:
            self.today_summary["posts"] += int(event.output_summary.get("posts", 0) or 0)
            self.today_summary["interactions"] += int(event.output_summary.get("interactions", 0) or 0)
        if event.event in (EventType.FAILED, EventType.RETRYING) or (event.duration_ms or 0) > 30_000:
            self.diagnostics.append(event.as_dict())
            self.diagnostics = self.diagnostics[-100:]

    @staticmethod
    def _update_agent_status(agent: dict[str, Any], event: RuntimeEvent, status: str) -> None:
        if event.event == EventType.STARTED:
            agent.update(status="running", current_stage=event.stage, stage_started_at=event.started_at)
        elif event.event == EventType.RETRYING:
            agent.update(status="retrying", current_stage=event.stage)
        elif event.event == EventType.FAILED:
            agent.update(status="failed", current_stage=event.stage, error={
                "stage": event.stage, "substage": event.substage, "attempt": event.attempt, "code": event.error_code,
                "summary": event.error_summary, "message": event.message, "at": event.ended_at})
        elif event.event == EventType.COMPLETED and event.stage == AGENT_STAGES[-1]:
            agent.update(status="completed", current_stage=None, stage_started_at=None, error=None)

    def apply_all(self, events: Iterable[RuntimeEvent]) -> None:
        for event in events: self.apply(event)

    def as_dict(self) -> dict[str, Any]:
        counts = Counter(agent.get("status", "pending") for agent in self.agents.values())
        return {"run_id": self.run_id, "last_event_id": self.last_event_id, "run": self.run, "day": self.day,
                "agents": list(self.agents.values()), "workers": list(self.workers.values()),
                "agent_counts": {key: counts.get(key, 0) for key in ("completed", "running", "pending", "retrying", "failed", "skipped")},
                "today_summary": dict(self.today_summary), "diagnostics": self.diagnostics}
