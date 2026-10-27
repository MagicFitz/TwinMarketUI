"""Run preparation, validation, and immutable input-manifest creation."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.schemas import redact

from .legacy_snapshots import resolve_pinned_snapshot
from .market_bundle import BUNDLE_DIR, MarketInputError, mds_client, unsupported_rules, verify_bundle
from .registry import ConflictError, RunRegistry
from .settings import Settings, get_settings


DEFAULTS = {
    "user_count": 10,
    "start_date": "2023-06-15",
    "end_date": "2023-06-16",
    "max_workers": 1,
    "agent_timeout": 300,
    "activate_prob": 0.2,
    "prob_of_technical": 0.3,
    "random_seed": 20260916,
    "template_db": "data/sys_1000.db",
    "belief_source": "util/belief/belief_1000_0129.csv",
    "config_path": "config/api.yaml",
    # legacy: the repository's built-in files. template: a frozen bundle from the market
    # data service. snapshot: read-only compatibility for runs created before templates.
    "market_data_mode": "legacy",
    "market_template": None,
    "data_version": None,
    "market_snapshot_id": None,
}


def _settings(settings: Settings | None) -> Settings:
    return settings if settings is not None else get_settings()


def time_run_id(now: datetime | None = None) -> str:
    """Time-ordered unique run id, e.g. ``20261018-153012-3f9a``.

    The short random suffix keeps ids unique when several runs are created within
    the same second.  The id is independent of the user-facing name, so names may
    repeat freely.
    """
    return f"{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:4]}"


def safe_run_id(value: str) -> str:
    value = "".join(char if char.isalnum() or char in "-_" else "-" for char in value.lower()).strip("-")
    return value[:64] or f"run-{uuid4().hex[:12]}"


def file_fingerprint(path: Path, settings: Settings | None = None) -> dict[str, Any]:
    """Describe a local input without reading it through a framework layer."""
    settings = _settings(settings)
    resolved = path.resolve()
    try:
        display_path = str(resolved.relative_to(settings.project_root))
    except ValueError:
        # A separately configured data directory is allowed.  The manifest still
        # needs a useful, reproducible identifier for that explicit input.
        display_path = str(resolved)
    return {
        "path": display_path,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "bytes": resolved.stat().st_size,
    }


def git_revision(settings: Settings | None = None) -> str | None:
    settings = _settings(settings)
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=settings.project_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def normalize_config(value: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Apply existing defaults and keep file inputs constrained to the project."""
    settings = _settings(settings)
    config = {**DEFAULTS, **{key: value[key] for key in DEFAULTS if key in value}}
    if "template_db" not in value and settings.data_dir != settings.project_root / "data":
        config["template_db"] = str(settings.data_path("sys_1000.db"))
    config["user_count"] = int(config["user_count"])
    config["max_workers"] = int(config["max_workers"])
    config["agent_timeout"] = int(config["agent_timeout"])
    config["random_seed"] = int(config["random_seed"])
    config["activate_prob"] = float(config["activate_prob"])
    config["prob_of_technical"] = float(config["prob_of_technical"])
    if (
        not 1 <= config["user_count"] <= 1000
        or config["max_workers"] < 1
        or not 30 <= config["agent_timeout"] <= 3600
        or not 0 <= config["activate_prob"] <= 1
        or not 0 <= config["prob_of_technical"] <= 1
    ):
        raise ValueError("用户数、并发数、Agent 超时（30–3600 秒）或概率超出允许范围")
    config["start_date"] = date.fromisoformat(str(config["start_date"])).isoformat()
    config["end_date"] = date.fromisoformat(str(config["end_date"])).isoformat()
    if config["market_snapshot_id"] or config["market_data_mode"] == "snapshot":
        raise ValueError("市场快照已由市场数据服务取代，请选择市场模板")
    if config["market_template"]:
        config["market_data_mode"] = "template"
    elif config["market_data_mode"] == "template":
        raise ValueError("模板模式必须选择市场模板")
    elif config["market_data_mode"] != "legacy":
        raise ValueError("market_data_mode 必须为 legacy 或 template")
    if config["market_data_mode"] == "legacy":
        config["market_template"] = config["data_version"] = None
    if config["start_date"] > config["end_date"]:
        raise ValueError("开始日期不能晚于结束日期")
    for key in ("template_db", "belief_source", "config_path"):
        path = settings.input_path(config[key]) if key == "template_db" else settings.project_path(config[key])
        try:
            config[key] = str(path.relative_to(settings.project_root))
        except ValueError:
            config[key] = str(path)
    if config["market_data_mode"] == "template":
        plan = mds_client(settings).plan(config["market_template"], config["start_date"], config["end_date"],
                                         config["data_version"])
        if not plan["ok"]:
            blocked = "；".join(item["message"] for item in plan["checks"] if item["status"] == "block")
            raise MarketInputError("PLAN_BLOCKED", f"市场数据不满足模板要求：{blocked}", 409)
        problems = unsupported_rules(plan["template"]["body"])
        if problems:
            raise MarketInputError("RULE_UNSUPPORTED", "当前仿真不支持模板中的规则：" + "，".join(problems))
        # Pin the exact template version and data version that the plan checked.
        config["market_template"], config["data_version"] = plan["template"]["ref"], plan["data_version"]
    return config


def materialize_market(output: Path, config, settings) -> dict[str, Any] | None:
    """Pull the run's frozen bundle into ``output/market`` (template mode only)."""
    if config.get("market_data_mode") != "template":
        target = output / BUNDLE_DIR
        if target.exists():
            shutil.rmtree(target)
        return None
    client = mds_client(settings)
    bundle = client.create_bundle(config["market_template"], config["start_date"], config["end_date"],
                                  config["data_version"])
    target = output / BUNDLE_DIR
    try:
        current = verify_bundle(target) if target.is_dir() else None
    except MarketInputError:
        current = None
    metadata = current if current and current["bundle_id"] == bundle["bundle_id"] else client.download(bundle["bundle_id"], target)
    client.add_ref(bundle["bundle_id"], output.name)
    return metadata


def market_environment(config, settings, folder: Path):
    """``(environment, market directory)``; environment is ``None`` in legacy mode."""
    mode = config.get("market_data_mode", "legacy")
    if mode == "template":
        metadata = verify_bundle(folder / BUNDLE_DIR)
        template = metadata["template"]
        return {"mode": "template", "id": metadata["bundle_id"],
                "manifest": {"mode": "template", "bundle_id": metadata["bundle_id"], "template": template["ref"],
                             "template_sha256": template["sha256"], "data_version": metadata["data_version"],
                             "news": metadata["news"], "files": metadata["files"]},
                "warnings": metadata["warnings"]}, folder / BUNDLE_DIR
    if config.get("market_snapshot_id"):
        return resolve_pinned_snapshot(settings, config["market_snapshot_id"], folder)
    return None, settings.data_dir


def write_manifest(output, config, settings):
    environment, market_dir = market_environment(config, settings, output)
    input_paths = {
        "agent_group_template": settings.input_path(config["template_db"]),
        "belief_source": settings.project_path(config["belief_source"]),
        "model_config": settings.project_path(config["config_path"]),
        "market_stock_data": market_dir / "stock_data.csv",
        "market_trading_days": market_dir / "trading_days.csv",
        "market_news": market_dir / "sorted_impact_news.pkl",
    }
    manifest = {
        "schema_version": 3, "run_id": output.name,
        "created_at": datetime.now(timezone.utc).isoformat(), "code_version": git_revision(settings),
        "parameters": redact(config),
        "inputs": {key: file_fingerprint(path, settings) for key, path in input_paths.items() if path.is_file()},
        "market_environment": environment["manifest"] if environment else {"mode": "legacy"},
        "research_warnings": environment["warnings"] if environment else [],
        "command_summary": "backend.worker -> simulation.py (arguments stored in Registry config)",
        "checkpoint_rule": {"resume_date": "必须等于 state.json checkpoint.next_date",
                            "cleanup": "续跑日及之后的数据库记录和按日期产物将被重新生成"},
    }
    temporary = output / ".manifest.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output / "manifest.json")
    return manifest


def create_run(
    registry: RunRegistry,
    request: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Allocate a private run directory and persist its registry record."""
    settings = _settings(settings)
    config = normalize_config(request.get("config") or request, settings)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    explicit = request.get("run_id")
    # 显式 run_id 冲突时拒绝；自动生成的时间 ID 冲突时换一个后缀重试。
    for _ in range(1 if explicit else 5):
        run_id = safe_run_id(explicit) if explicit else time_run_id()
        output = settings.runs_dir / run_id
        try:
            output.mkdir(mode=0o700)
            break
        except FileExistsError as error:
            if explicit:
                raise ConflictError("run_id 已存在，拒绝覆盖运行目录") from error
    else:
        raise ConflictError("无法分配唯一的 run_id，请重试")
    try:
        materialize_market(output, config, settings)
        write_manifest(output, config, settings)
        (output / "state.json").write_text(
            json.dumps(
                {
                    "status": "draft",
                    "checkpoint": {"last_completed_date": None, "next_date": config["start_date"]},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return registry.create(
            run_id,
            str(request.get("name") or run_id),
            str(request.get("description") or ""),
            config,
            output,
        )
    except Exception:
        # Only remove the directory allocated above; never act on a request path.
        shutil.rmtree(output, ignore_errors=True)
        raise


def validate_start(
    run: dict[str, Any],
    resume: bool = False,
    settings: Settings | None = None,
) -> list[str]:
    """Return user-facing preflight errors without launching the simulation."""
    settings = _settings(settings)
    config, folder = run["config"], Path(run["output_dir"])
    errors: list[str] = []
    for key in ("template_db", "belief_source", "config_path"):
        try:
            resolver = settings.input_path if key == "template_db" else settings.project_path
            valid = resolver(config[key]).is_file()
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            errors.append(f"缺少必要输入: {key}")
    try:
        environment, market_dir = market_environment(config, settings, folder)
        if environment and environment["mode"] == "template":
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("market_environment", {}).get("bundle_id") != environment["id"]:
                errors.append("运行清单与市场数据包不一致")
        if any(not (market_dir / name).is_file() for name in ("stock_data.csv", "trading_days.csv", "sorted_impact_news.pkl")):
            errors.append("缺少市场数据或交易日历")
    except (ValueError, OSError) as error:
        errors.append(str(error))
    if resume:
        next_date = run["checkpoint"].get("next_date")
        if not next_date or next_date > config["end_date"]:
            errors.append("没有可用 checkpoint 可续跑")
        for path in (
            folder / f"user_{config['user_count']}.db",
            folder / f"forum_{config['user_count']}.db",
            folder / f"belief_{config['user_count']}.csv",
        ):
            if not path.is_file():
                errors.append(f"续跑产物缺失: {path.name}")
    return errors
