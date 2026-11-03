"""Per-Agent record of what went into each LLM call, with token estimates.

Written to ``<run>/llm_context/<date>/<agent_id>[_a<attempt>].json`` after every
call so the runtime page can show it live. Message texts are stored once in a
pool keyed by content hash; calls reference them by id, so the growing
conversation history is not duplicated per call.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算 token：ASCII 约 4 字符/token，中文约 1 token/字符，再加 10% 余量。"""
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    ascii_chars = sum(char.isascii() for char in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    estimate = (ascii_chars + 3) // 4 + non_ascii_chars + 8 * len(messages)
    return max(1, int(estimate * 1.1))


def context_file(directory: Path, agent_id: str, attempt: int) -> Path:
    return directory / (f"{agent_id}.json" if attempt == 1 else f"{agent_id}_a{attempt}.json")


class LLMContextRecorder:
    def __init__(self, path: Path, agent_id: str, simulation_date: str, attempt: int):
        self.path = path
        self.data: dict[str, Any] = {
            "agent_id": agent_id, "simulation_date": simulation_date, "attempt": attempt,
            "model": None, "context_window": None, "calls": [], "messages": {},
        }
        self._previous_ids: set[str] = set()

    def _intern(self, role: str, content: Any) -> str:
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        message_id = hashlib.sha1(f"{role}\0{text}".encode("utf-8")).hexdigest()[:12]
        if message_id not in self.data["messages"]:
            self.data["messages"][message_id] = {
                "role": role, "content": text, "chars": len(text),
                "est_tokens": estimate_tokens([{"role": role, "content": text}]),
            }
        return message_id

    def record(self, *, step: str, stage: str | None, messages: list[dict[str, Any]], model: str,
               context_window: int, max_tokens: int | None, started_at: datetime, latency_ms: float,
               response: Any = None, error: BaseException | None = None) -> None:
        ids = [self._intern(str(message.get("role")), message.get("content")) for message in messages]
        usage = getattr(response, "usage", None)
        choice = (getattr(response, "choices", None) or [None])[0]
        reply = getattr(getattr(choice, "message", None), "content", None)
        self.data.update(model=model, context_window=context_window)
        self.data["calls"].append({
            "seq": len(self.data["calls"]) + 1,
            "step": step,
            "stage": stage,
            "started_at": started_at.isoformat(),
            "latency_ms": round(latency_ms, 1),
            "status": "error" if error is not None else "ok",
            "error": None if error is None else f"{type(error).__name__}: {error}"[:500],
            "message_ids": ids,
            "new_ids": [message_id for message_id in ids if message_id not in self._previous_ids],
            "est_input_tokens": estimate_tokens(messages),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "max_tokens": max_tokens,
            "finish_reason": getattr(choice, "finish_reason", None),
            "reply_id": self._intern("assistant", reply) if reply is not None else None,
        })
        # 上一轮的回复常被拼回下一轮输入；它不是新加入的上下文，不计为“新增”。
        self._previous_ids = set(ids) | {self.data["calls"][-1]["reply_id"]}
        self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
