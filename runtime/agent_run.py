"""One Agent's attempt within a simulation day: current stage, deadline and abandonment.

Python threads cannot be killed, so a timed-out Agent is *abandoned*: the main
loop stops waiting for it, records the failure at the stage it was stuck in, and
every later event or artifact write from that thread is suppressed.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import Iterator

from .llm_context import LLMContextRecorder, context_file
from .observer import RuntimeObserver
from .schemas import Scope

_local = threading.local()


def current_agent_run() -> "AgentRun | None":
    """The AgentRun bound to the calling thread, if any (used by the LLM client)."""
    return getattr(_local, "run", None)


class AgentRun:
    def __init__(self, observer: RuntimeObserver | None, agent_id: str, simulation_date: str, attempt: int = 1,
                 context_dir: Path | None = None):
        self.observer, self.agent_id, self.simulation_date, self.attempt = observer, str(agent_id), simulation_date, attempt
        self.context_dir = context_dir
        self.llm_context = (LLMContextRecorder(context_file(context_dir, self.agent_id, attempt), self.agent_id, simulation_date, attempt)
                            if context_dir is not None else None)
        self.stage: str | None = None
        self.worker_id: str | None = None
        self.last_llm_error: str | None = None
        self._began_at: float | None = None
        self._abandoned = threading.Event()

    def next_attempt(self) -> "AgentRun":
        return AgentRun(self.observer, self.agent_id, self.simulation_date, self.attempt + 1, self.context_dir)

    # ---- lifecycle -----------------------------------------------------
    def begin(self) -> None:
        """Called from the worker thread when the task actually starts running."""
        self.worker_id = f"thread-{threading.get_ident()}"
        self._began_at = monotonic()

    def elapsed(self) -> float | None:
        """Seconds since the task started running; None while still queued."""
        return None if self._began_at is None else monotonic() - self._began_at

    @property
    def abandoned(self) -> bool:
        return self._abandoned.is_set()

    def abandon(self, error_code: str, error_summary: str) -> None:
        """Mark this attempt failed at its current stage and silence the thread."""
        self._abandoned.set()  # 先屏蔽线程，再写失败事件，避免其事件插在中间
        self._emit("failed", self.stage or "state", force=True, message="Agent 处理超时，已放弃等待",
                   error_code=error_code, error_summary=error_summary)

    @contextmanager
    def bound(self) -> Iterator["AgentRun"]:
        previous = getattr(_local, "run", None)
        _local.run = self
        try:
            yield self
        finally:
            _local.run = previous

    # ---- events --------------------------------------------------------
    def start(self, stage: str, **fields) -> None:
        self.stage = stage
        self._emit("started", stage, **fields)

    def complete(self, stage: str, **fields) -> None:
        self._emit("completed", stage, **fields)

    def record_llm_call(self, **call) -> None:
        """记录一次 LLM 调用放入了哪些消息；被放弃的线程不再写入。"""
        if self.llm_context is None or self.abandoned:
            return
        try:
            self.llm_context.record(stage=self.stage, **call)
        except Exception:  # 观测失败不能影响模拟
            pass

    def note_llm_error(self, code: str, summary: str) -> None:
        """记录最终失败的 LLM 请求。调用方常把它吞掉，之后才以无关的异常（如 TypeError）崩溃。"""
        self.last_llm_error = f"{code}: {summary}"

    def fail(self, error: BaseException, message: str = "Agent 处理失败，运行将继续") -> None:
        summary = str(error)
        if self.last_llm_error:
            summary = f"{summary}（此前 LLM 请求失败：{self.last_llm_error}）"
        self._emit("failed", self.stage or "state", message=message,
                   error_code=type(error).__name__, error_summary=summary)

    def retrying(self, substage: str, **fields) -> None:
        self._emit("retrying", self.stage or "state", substage=substage, **fields)

    def _emit(self, kind: str, stage: str, force: bool = False, **fields) -> None:
        if self.observer is None or (self.abandoned and not force):
            return
        getattr(self.observer, kind)(Scope.AGENT, stage, simulation_date=self.simulation_date,
                                     agent_id=self.agent_id, worker_id=self.worker_id,
                                     attempt=self.attempt, **fields)
