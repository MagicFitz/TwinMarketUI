"""Client side of the market data service: plans, bundle download and verification.

The simulation backend never imports ``market_service``; the two processes meet
over HTTP only. Once a bundle is unpacked into ``runs/<run_id>/market/`` the run
is self-contained and the service may be offline for starts and resumes.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

BUNDLE_DIR = "market"
TOKEN_ENV = "TWINMARKET_MDS_TOKEN"

# Rules the current simulation code implements. Anything else is refused at run creation
# instead of being silently ignored.
SUPPORTED_RULES = {
    ("rules", "price_limit_pct"): (10,),
    ("rules", "lot_size"): (100,),
    ("session", "matching"): ("daily_call_auction",),
    ("session", "non_trading_day"): ({"forum": True, "trading": False},),
}


class MarketInputError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(message)


def unsupported_rules(body: dict[str, Any]) -> list[str]:
    problems = []
    for (section, key), allowed in SUPPORTED_RULES.items():
        value = body.get(section, {}).get(key)
        if value not in allowed:
            problems.append(f"{section}.{key}={json.dumps(value, ensure_ascii=False)}")
    fees = body.get("rules", {}).get("fees") or {}
    if any(value for value in fees.values()):
        problems.append("rules.fees 非零")
    return problems


def fingerprint(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def verify_bundle(folder: Path) -> dict[str, Any]:
    """Return ``bundle.json`` after checking every listed file fingerprint."""
    try:
        metadata = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
        for name, expected in metadata["files"].items():
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()) or fingerprint(target) != expected:
                raise MarketInputError("BUNDLE_INTEGRITY_FAILED", "运行数据包的文件指纹不匹配")
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, MarketInputError):
            raise
        raise MarketInputError("BUNDLE_INTEGRITY_FAILED", "运行数据包缺失或损坏") from None
    return metadata


Transport = Callable[[str, str, Any], tuple[int, bytes]]


def requests_transport(base_url: str, token: str, timeout: float = 120) -> Transport:
    import requests

    def send(method, path, body):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = requests.request(method, base_url.rstrip("/") + path, json=body, headers=headers,
                                        timeout=timeout, allow_redirects=False)
        except requests.RequestException:
            raise MarketInputError("MDS_UNAVAILABLE", f"无法连接市场数据服务 {base_url}，请确认已启动", 503) from None
        return response.status_code, response.content

    return send


class MdsClient:
    def __init__(self, transport: Transport):
        self.transport = transport

    def _json(self, method, path, body=None):
        status, content = self.transport(method, path, body)
        try:
            data = json.loads(content or b"null")
        except ValueError:
            data = None
        if status >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            code = data.get("code") if isinstance(data, dict) else None
            raise MarketInputError(code or "MDS_REQUEST_FAILED", message or f"市场数据服务返回 HTTP {status}",
                                   status if status in {404, 409, 422} else 502)
        return data

    def plan(self, template, start_date, end_date, data_version=None):
        return self._json("POST", "/v1/plans", {"template": template, "start_date": start_date,
                                                 "end_date": end_date, "data_version": data_version})

    def create_bundle(self, template, start_date, end_date, data_version):
        return self._json("POST", "/v1/bundles", {"template": template, "start_date": start_date,
                                                   "end_date": end_date, "data_version": data_version})

    def add_ref(self, bundle_id, run_id):
        return self._json("PUT", f"/v1/bundles/{quote(bundle_id)}/refs/{quote(run_id)}")

    def remove_ref(self, bundle_id, run_id):
        return self._json("DELETE", f"/v1/bundles/{quote(bundle_id)}/refs/{quote(run_id)}")

    def download(self, bundle_id: str, destination: Path) -> dict[str, Any]:
        """Unpack the bundle archive into ``destination`` atomically and verify it."""
        status, content = self.transport("GET", f"/v1/bundles/{quote(bundle_id)}/archive", None)
        if status != 200:
            raise MarketInputError("MDS_REQUEST_FAILED", f"下载数据包失败（HTTP {status}）", 502)
        staging = destination.with_name(f".{destination.name}-{uuid4().hex[:8]}")
        staging.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                for member in archive.getmembers():
                    # Only plain relative files; never links, devices or paths that escape.
                    target = (staging / member.name).resolve()
                    if not member.isfile() or not target.is_relative_to(staging.resolve()):
                        raise MarketInputError("BUNDLE_INTEGRITY_FAILED", "数据包含不安全的路径")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
            metadata = verify_bundle(staging)
            if metadata.get("bundle_id") != bundle_id:
                raise MarketInputError("BUNDLE_INTEGRITY_FAILED", "数据包标识不一致")
            for path in staging.rglob("*"):
                if path.is_file():
                    path.chmod(0o444)
            if destination.exists():
                shutil.rmtree(destination)
            os.rename(staging, destination)
            return metadata
        except tarfile.TarError:
            raise MarketInputError("BUNDLE_INTEGRITY_FAILED", "数据包格式无效") from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def mds_client(settings) -> MdsClient:
    """Factory used by the run service; tests replace it with an in-process transport."""
    return MdsClient(requests_transport(settings.mds_url, os.environ.get(TOKEN_ENV, "").strip()))
