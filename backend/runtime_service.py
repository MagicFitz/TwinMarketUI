"""Framework-neutral queries over persisted RuntimeEvent streams."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime import EventStore, RuntimeSnapshotReducer
from runtime.outcome import first_all_failed_day


def store_for(run: dict[str, Any]) -> EventStore:
    return EventStore(Path(run["output_dir"]) / "events.jsonl")


def reclassify_completed_runs(registry) -> list[str]:
    """Mark degraded completed runs as failed when a whole day had no working Agent.

    Runs recorded before the simulation stopped on such days were reported as completed.
    Healthy runs had no Agent failure at all and are skipped without reading events.
    """
    candidates = []
    for archived in (False, True):
        offset = 0
        while True:
            runs, total = registry.list(status="completed", archived=archived, offset=offset, limit=200)
            candidates += [run for run in runs if run.get("health") == "degraded"]
            offset += len(runs)
            if not runs or offset >= total:
                break
    # Correct only after paging, so updates cannot shift the pages being read.
    corrected = []
    for run in candidates:
        try:
            reason = first_all_failed_day(store_for(run).after(0, 1_000_000))
        except OSError:
            continue
        if reason and registry.mark_completed_as_failed(run["run_id"], reason):
            corrected.append(run["run_id"])
    return corrected


def runtime_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    store = store_for(run)
    reducer = RuntimeSnapshotReducer(run["run_id"])
    reducer.apply_all(store.after(0, 1_000_000))
    result = reducer.as_dict()
    result["lifecycle"] = run["status"]
    result["health"] = run["health"]
    return result
