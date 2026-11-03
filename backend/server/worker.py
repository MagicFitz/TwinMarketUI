"""Detached simulation worker.  The HTTP server never runs simulation work inline."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from simulation.runtime import RuntimeObserver
from simulation.runtime.outcome import first_all_failed_day
from simulation.runtime.schemas import EventType, Scope

from .registry import RunRegistry
from .run_service import market_environment, validate_start
from .runtime_service import runtime_snapshot, store_for
from .settings import Settings, get_settings


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def _simulation_failure(run) -> str | None:
    """The simulation's own day- or run-level failure message, newest first."""
    for item in reversed(store_for(run).after(0, 1_000_000)):
        if item.event == EventType.FAILED and item.scope in {Scope.DAY, Scope.RUN} and item.error_summary:
            return item.error_summary
    return None


def run_worker(run_id: str, *, resume: bool = False, settings: Settings | None = None) -> int:
    """Execute a registered run and persist its lifecycle marker."""
    settings = settings if settings is not None else get_settings()
    registry = RunRegistry(settings.registry_path)
    run = registry.get(run_id)
    folder, config = Path(run["output_dir"]), run["config"]
    observer = RuntimeObserver(
        run_id,
        store_for(run),
        lambda reason: registry.update(run_id, health="degraded", failure_reason=reason),
    )
    registry.transition(
        run_id,
        "running",
        pid=os.getpid(),
        last_heartbeat=datetime.now(timezone.utc).isoformat(),
    )
    observer.started(Scope.RUN, "run", message="运行 Worker 已启动")
    try:
        preflight = validate_start(run, resume, settings)
        if preflight:
            raise ValueError("; ".join(preflight))
        environment, market_dir = market_environment(config, settings, folder)
        simulation_env = {**os.environ, **settings.as_environ(), "TWINMARKET_DATA_DIR": str(market_dir)}
        # The simulation needs no provider or service credentials, only its frozen inputs.
        for name in ("TUSHARE_TOKEN", "TWINMARKET_MDS_TOKEN", "TWINMARKET_MARKET_SNAPSHOT_ID",
                     "TWINMARKET_MARKET_BUNDLE_ID", "TWINMARKET_RULES"):
            simulation_env.pop(name, None)
        if environment and environment["mode"] == "template":
            simulation_env["TWINMARKET_MARKET_BUNDLE_ID"] = environment["id"]
            simulation_env["TWINMARKET_RULES"] = str(market_dir / "rules.json")
        elif environment:
            simulation_env["TWINMARKET_MARKET_SNAPSHOT_ID"] = environment["id"]
        if not resume:
            observer.started(Scope.RUN, "prepare", message="正在准备独立运行目录")
            prepared = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "simulation.prepare_run",
                    "--template-db",
                    config["template_db"],
                    "--belief-source",
                    config["belief_source"],
                    "--user-count",
                    str(config["user_count"]),
                    "--user-db",
                    str(folder / f"user_{config['user_count']}.db"),
                    "--belief-output",
                    str(folder / f"belief_{config['user_count']}.csv"),
                    *(["--market-snapshot", str(market_dir)] if environment else ["--legacy-history", str(market_dir)]),
                    "--start-date",
                    config["start_date"],
                ],
                cwd=settings.project_root,
                env=simulation_env,
                capture_output=True,
                text=True,
            )
            with (folder / "simulation.log").open("a", encoding="utf-8") as handle:
                handle.write(prepared.stdout + prepared.stderr)
            if prepared.returncode:
                # Surface the script's own message (e.g. too little history) instead of an exit code.
                lines = [line for line in prepared.stderr.splitlines() if line.strip()]
                raise RuntimeError(f"运行准备失败：{lines[-1] if lines else f'exit {prepared.returncode}'}")
            observer.completed(
                Scope.RUN,
                "prepare",
                message="运行输入已准备",
                output_summary={"users": config["user_count"]},
            )
        forum_db = folder / f"forum_{config['user_count']}.db"
        log = folder / "simulation.log"
        command = [
            sys.executable,
            "-m",
            "simulation",
            "--log_dir",
            str(folder),
            "--forum_db",
            str(forum_db),
            "--user_db",
            str(folder / f"user_{config['user_count']}.db"),
            "--user_graph_save_name",
            f"user_graph_{config['user_count']}",
            "--start_date",
            run["checkpoint"]["next_date"] if resume else config["start_date"],
            "--end_date",
            config["end_date"],
            "--max_workers",
            str(config["max_workers"]),
            "--agent_timeout",
            str(config.get("agent_timeout", 300)),  # 早期 Run 的配置中没有该字段
            "--node",
            str(config["user_count"]),
            "--config_path",
            config["config_path"],
            "--belief_init_path",
            str(folder / f"belief_{config['user_count']}.csv"),
            "--activate_prob",
            str(config["activate_prob"]),
            "--prob_of_technical",
            str(config["prob_of_technical"]),
            "--random_seed",
            str(config["random_seed"]),
            "--checkpoint_state_path",
            str(folder / "state.json"),
            "--runtime-events",
            str(folder / "events.jsonl"),
            "--run-id",
            run_id,
        ]
        if resume:
            command.append("--checkpoint")
        with log.open("a", encoding="utf-8") as handle:
            result = subprocess.run(
                command,
                cwd=settings.project_root,
                env=simulation_env,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        if result.returncode:
            raise RuntimeError(_simulation_failure(run) or f"simulation exited {result.returncode}")
        # Defense in depth: never report completion for a day on which no Agent worked.
        all_failed = first_all_failed_day(store_for(registry.get(run_id)).after(0, 1_000_000))
        if all_failed:
            raise RuntimeError(all_failed)
        # the simulation process has appended events using another EventStore instance;
        # reopen it so this terminal event continues the ordered sequence.
        final_run = registry.get(run_id)
        observer = RuntimeObserver(
            run_id,
            store_for(final_run),
            lambda reason: registry.update(run_id, health="degraded", failure_reason=reason),
        )
        observer.completed(Scope.RUN, "run", message="模拟运行完成")
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        checkpoint = state.get("checkpoint", final_run["checkpoint"])
        health = (
            "degraded"
            if any(
                item.get("event") == "stage_failed" and item.get("scope") == "agent"
                for item in runtime_snapshot(final_run)["diagnostics"]
            )
            else "healthy"
        )
        registry.transition(
            run_id,
            "completed",
            pid=None,
            exit_code=0,
            health=health,
            checkpoint_json=json.dumps(checkpoint),
            last_heartbeat=datetime.now(timezone.utc).isoformat(),
        )
        return 0
    except Exception as error:
        observer.failed(
            Scope.RUN,
            "run",
            message="模拟运行失败",
            error_code=type(error).__name__,
            error_summary=str(error),
        )
        # Days committed before the failure stay valid, so a retry can resume after them.
        try:
            checkpoint = json.loads((folder / "state.json").read_text(encoding="utf-8"))["checkpoint"]
        except (OSError, ValueError, KeyError):
            checkpoint = run["checkpoint"]
        registry.transition(
            run_id,
            "failed",
            pid=None,
            exit_code=1,
            health="degraded",
            checkpoint_json=json.dumps(checkpoint),
            failure_reason=str(error) if isinstance(error, RuntimeError) else f"{type(error).__name__}: {error}",
        )
        return 1


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = _arguments(argv)
    return run_worker(args.run_id, resume=args.resume, settings=settings)


if __name__ == "__main__":
    raise SystemExit(main())
