"""Versioned market templates: a data manifest plus session and trading rules.

A published ``template_id@version`` never changes. The service validates the
structure and what it can produce; whether the simulation supports a rule is
decided by the simulation backend when a run is created.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import yaml

from .errors import MarketError

BUILTIN_DIR = Path(__file__).with_name("templates")
DEFAULT_TEMPLATE = "cn-a-industry10"
NEWS_SOURCES = {"legacy", "manual", "tushare.news", "tushare.major_news", "tushare.cctv_news", "tushare.anns"}
RANKINGS = {"legacy-v1"}


def invalid(message):
    return MarketError("TEMPLATE_INVALID", message)


def _section(body, *path):
    value = body
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, dict):
            raise invalid(f"缺少 {'.'.join(path)} 配置")
    return value


def validate(body):
    """Return a normalized copy of ``body`` or raise ``TEMPLATE_INVALID``."""
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        raise invalid("模板 schema_version 必须为 1")
    body = copy.deepcopy(body)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", str(body.get("template_id", ""))):
        raise invalid("template_id 只能包含小写字母、数字和连字符")
    if not isinstance(body.get("version"), int) or body["version"] < 1:
        raise invalid("version 必须为正整数")
    if not str(body.get("name", "")).strip():
        raise invalid("模板需要名称")
    body.setdefault("description", "")
    universe = _section(body, "universe")
    if universe.get("asset_definition") != "market_assets.yaml" or universe.get("instruments") != "industry_indices":
        raise invalid("目前只支持 market_assets.yaml 定义的 10 个行业指数")
    if _section(body, "data", "calendar").get("exchange") != "SSE":
        raise invalid("交易日历目前只支持 SSE")
    if _section(body, "data", "bars").get("dataset") != "index_daily":
        raise invalid("行情数据集目前只支持 index_daily")
    _section(body, "data", "valuation").setdefault("required", False)
    lookback = _section(body, "data", "history").get("lookback_trading_days")
    if not isinstance(lookback, int) or not 1 <= lookback <= 250:
        raise invalid("回看交易日数须在 1–250 之间")
    news = _section(body, "data", "news")
    if not isinstance(news.get("enabled"), bool):
        raise invalid("data.news.enabled 必须为布尔值")
    sources = news.setdefault("sources", [])
    if not isinstance(sources, list) or set(sources) - NEWS_SOURCES or (news["enabled"] and not sources):
        raise invalid(f"新闻来源须取自 {', '.join(sorted(NEWS_SOURCES))}，启用新闻时至少选择一个")
    news.setdefault("include_synthetic", False)
    broadcast = _section(body, "data", "news", "broadcast")
    if broadcast.get("ranking") not in RANKINGS:
        raise invalid(f"每日广播排序目前只支持 {', '.join(sorted(RANKINGS))}")
    top_k = broadcast.get("top_k")
    if top_k is not None and (not isinstance(top_k, int) or not 1 <= top_k <= 200):
        raise invalid("每日广播条数须在 1–200 之间，或留空表示全部")
    retrieval = _section(body, "data", "news", "retrieval")
    if retrieval.get("enabled"):
        raise invalid("新闻检索索引尚未提供，retrieval.enabled 须为 false")
    if not isinstance(retrieval.get("window_days"), int) or not 1 <= retrieval["window_days"] <= 60:
        raise invalid("检索窗口须在 1–60 天之间")
    if not isinstance(news.setdefault("min_daily_articles", 1), int) or news["min_daily_articles"] < 0:
        raise invalid("min_daily_articles 必须为非负整数")
    if news.setdefault("on_gap", "warn") not in {"warn", "block"}:
        raise invalid("on_gap 只能为 warn 或 block")
    session = _section(body, "session")
    if session.get("timezone") != "Asia/Shanghai" or session.get("trading_days") != "calendar":
        raise invalid("会话规则目前只支持 Asia/Shanghai 时区和交易日历开市")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(session.get("information_cutoff", ""))):
        raise invalid("信息截止时刻须为 HH:MM")
    if not isinstance(session.get("matching"), str) or not isinstance(session.get("non_trading_day"), dict):
        raise invalid("缺少撮合方式或非交易日规则")
    rules = _section(body, "rules")
    if not isinstance(rules.get("price_limit_pct"), (int, float)) or not 0 < rules["price_limit_pct"] <= 100:
        raise invalid("涨跌停幅度须在 0–100 之间")
    if not isinstance(rules.get("lot_size"), int) or rules["lot_size"] < 1:
        raise invalid("最小交易单位必须为正整数")
    if not isinstance(rules.get("fees", {}), dict):
        raise invalid("费率配置无效")
    if rules.get("missing_constituent") != "zero_return":
        raise invalid("成分股缺失处理目前只支持 zero_return（停牌收益计 0）")
    return body


def digest(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def builtin_templates():
    return [validate(yaml.safe_load(path.read_text(encoding="utf-8"))) for path in sorted(BUILTIN_DIR.glob("*.yaml"))]


def ensure_builtin(registry):
    for body in builtin_templates():
        registry.insert_template(body["template_id"], body["version"], digest(body), body, builtin=True)
    if registry.setting("default_template") is None:
        registry.set_setting("default_template", DEFAULT_TEMPLATE)


def parse_ref(ref):
    """``"id"`` or ``"id@3"`` -> ``(id, version or None)``."""
    matched = re.fullmatch(r"([a-z0-9][a-z0-9-]{1,60})(?:@(\d+))?", str(ref or ""))
    if not matched:
        raise MarketError("TEMPLATE_NOT_FOUND", "市场模板标识无效", 404)
    return matched[1], int(matched[2]) if matched[2] else None


def publish(registry, body):
    """Publish ``body`` as the next version of its template id."""
    body = dict(body or {})
    try:
        latest = registry.template(str(body.get("template_id", "")))["version"]
    except MarketError:
        latest = 0
    body["version"] = latest + 1
    body = validate(body)
    registry.insert_template(body["template_id"], body["version"], digest(body), body)
    return registry.template(body["template_id"], body["version"])


def summary(template, default_id=None):
    body = template["body"]
    news = body["data"]["news"]
    return {"template_id": template["template_id"], "version": template["version"],
            "ref": f"{template['template_id']}@{template['version']}", "sha256": template["sha256"],
            "name": body["name"], "description": body["description"], "builtin": template["builtin"],
            "default": template["template_id"] == default_id, "created_at": template["created_at"],
            "news": {"enabled": news["enabled"], "sources": news["sources"], "top_k": news["broadcast"]["top_k"]},
            "session": body["session"], "rules": body["rules"], "body": body}
