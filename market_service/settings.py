"""Environment-backed configuration for the standalone market data service.

The service deliberately shares no settings object with the simulation backend:
the two processes only meet over HTTP (plans, bundles and file downloads).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PROJECT_ROOT_ENV = "TWINMARKET_PROJECT_ROOT"
HOME_ENV = "TWINMARKET_MDS_HOME"
SEED_DIR_ENV = "TWINMARKET_MDS_SEED_DIR"
ALLOWED_ORIGINS_ENV = "TWINMARKET_MDS_ALLOWED_ORIGINS"
TOKEN_ENV = "TWINMARKET_MDS_TOKEN"

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWED_ORIGINS = ("http://127.0.0.1:5173",)


def _resolve(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


@dataclass(frozen=True, slots=True)
class MdsSettings:
    """``home`` holds every file the service writes; ``seed_dir`` is read once on first start."""

    project_root: Path
    home: Path
    seed_dir: Path
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS
    token: str = ""

    def __post_init__(self) -> None:
        for name in ("project_root", "home", "seed_dir"):
            object.__setattr__(self, name, Path(getattr(self, name)).expanduser().resolve())
        object.__setattr__(self, "allowed_origins", tuple(self.allowed_origins))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "MdsSettings":
        source = os.environ if environ is None else environ
        root = _resolve(source[PROJECT_ROOT_ENV], Path.cwd()) if source.get(PROJECT_ROOT_ENV) else DEFAULT_PROJECT_ROOT
        origins = source.get(ALLOWED_ORIGINS_ENV)
        return cls(
            project_root=root,
            home=_resolve(source.get(HOME_ENV) or "mds_data", root),
            seed_dir=_resolve(source.get(SEED_DIR_ENV) or "data", root),
            allowed_origins=tuple(item.strip() for item in origins.replace("\n", ",").split(",") if item.strip())
            if origins is not None else DEFAULT_ALLOWED_ORIGINS,
            token=source.get(TOKEN_ENV, "").strip(),
        )

    def path(self, *parts: str | Path) -> Path:
        """Return a path below ``home`` without traversal."""
        path = self.home.joinpath(*parts).resolve()
        if not path.is_relative_to(self.home):
            raise ValueError("数据路径必须位于市场数据服务目录")
        return path

    def project_path(self, value: str | Path) -> Path:
        path = _resolve(value, self.project_root)
        if not path.is_relative_to(self.project_root):
            raise ValueError("配置路径必须位于项目目录")
        return path

    def as_environ(self) -> dict[str, str]:
        """Serialize for a detached job worker; the access token is not needed there."""
        return {PROJECT_ROOT_ENV: str(self.project_root), HOME_ENV: str(self.home), SEED_DIR_ENV: str(self.seed_dir),
                ALLOWED_ORIGINS_ENV: ",".join(self.allowed_origins)}


def get_settings() -> MdsSettings:
    return MdsSettings.from_env()
