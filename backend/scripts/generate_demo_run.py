#!/usr/bin/env python3
"""Generate a complete, clearly labeled SYNTHETIC experiment for demonstrating Studio.

No model is called. Agent personas and the pre-start market history are the
repository's real seed inputs; everything from ``start_date`` on is produced here:

* Returns come from a GJR-GARCH process with Student-t shocks, and volume from a
  log-AR(1) process loaded on absolute returns. Fat tails, the leverage effect and
  volatility clustering therefore exist BY CONSTRUCTION. They show what the analysis
  views look like and are never evidence that the simulator reproduces them.
* Orders, fills, cash and positions form a conserved ledger, and forum cascades come
  from a preferential-attachment rule, so every tab reads mutually consistent data.

The run is marked with ``synthetic_demo`` in its config and manifest, which Studio
shows as a banner on every tab.

    python scripts/generate_demo_run.py [--replace]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.registry import ConflictError, NotFoundError, RunRegistry  # noqa: E402
from server.run_service import normalize_config, write_manifest  # noqa: E402
from server.settings import Settings, get_settings  # noqa: E402
from simulation.prepare_run import prepare_database, write_beliefs  # noqa: E402
from simulation.runtime import RuntimeEvent  # noqa: E402
from simulation.runtime.schemas import EventType, Scope  # noqa: E402
from simulation.stores.UserDB import save_graph_json  # noqa: E402

DEFAULT_RUN_ID = "demo-synthetic-100a"
RUN_NAME = "【合成示例】100 Agent · 2023 下半年"
RUN_DESCRIPTION = (
    "由 scripts/generate_demo_run.py 生成的合成数据，未调用模型，只用于演示界面。"
    "人设和起始日前的行情来自内置种子；起始日后的行情、交易和帖子都由随机过程生成，"
    "典型事实由构造产生，不是研究结论。"
)
SYNTHETIC_MODEL = "synthetic-demo（未调用模型）"
LLM_CONTEXT_DAYS = 3
STOCK_COLUMNS = (
    "stock_id", "date", "close_price", "pre_close", "change", "pct_chg", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
    "vol", "vol_5", "vol_10", "vol_30", "ma_hfq_5", "ma_hfq_10", "ma_hfq_30", "elg_amount_net",
)
LARGE_ORDER_SHARES = 100_000
TURNOVER_WEIGHT = {"高": 1.5, "中": 1.0, "低": 0.6}

# (position in the run, factor shock, what the forum talks about that day)
MARKET_EVENTS = (
    (0.18, 0.032, "稳增长政策预期升温"),
    (0.42, -0.038, "流动性收紧的传闻"),
    (0.63, -0.030, "外资流出的担忧"),
    (0.82, 0.036, "市场传闻将有增量资金入场"),
)

OPENINGS = {
    "bull": ("今天盘面比我预期的强。", "越看越觉得这轮还没走完。", "资金明显在回流，我继续偏多。", "这个位置我不想下车。"),
    "bear": ("今天这走势让我很不舒服。", "我开始怀疑这轮反弹的成色。", "量价配合不好，先防守。", "这个位置我不敢追了。"),
    "flat": ("今天没什么明确信号。", "市场还在震荡，耐心等。", "多空都说得通，我先看。", "节奏不清楚，少动为妙。"),
}
ACTIONS = {
    "buy": ("加了点{name}，收盘 {price}，当日 {pct:+.2f}%。", "分批买入{name}，{price} 附近接了一笔。"),
    "sell": ("减了部分{name}，{price} 落袋，当日 {pct:+.2f}%。", "{name}先卖掉一些，收盘 {price}，留点现金。"),
    "hold": ("{name}今天 {pct:+.2f}%，收在 {price}，仓位没动。", "继续拿着{name}，今天 {pct:+.2f}%。"),
}
STRATEGY_NOTES = {
    "技术面": ("均线还没走坏，按纪律来。", "缩量回调不算破位。", "等放量确认再加。"),
    "基本面": ("估值还在能接受的范围。", "盈利预期没变，我不看短期波动。", "还得看下一季的业绩。"),
}
REPOST_COMMENTS = {
    "bull": ("说得对，我也是这么看的。", "这段逻辑值得记一下。", "同感，准备跟一点。"),
    "bear": ("提醒得对，别上头。", "风险说得很清楚，转给大家看看。", "和我担心的一样。"),
    "flat": ("先记下，再观察。", "有点意思，看后面怎么走。", "转一下，留个对照。"),
}
REACTION_REASONS = {
    "like": "和我的判断一致。",
    "unlike": "和我的判断相反，逻辑也不够扎实。",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def trading_dates(settings: Settings, start: str, end: str) -> list[str]:
    with settings.data_path("trading_days.csv").open(newline="", encoding="utf-8") as handle:
        return sorted(
            row["cal_date"] for row in csv.DictReader(handle)
            if row["is_open"] in {"open", "1"} and start <= row["cal_date"] <= end
        )


def garch_path(rng: np.random.Generator, days: int, daily_vol: float, shocks: dict[int, float] | None = None):
    """GJR-GARCH(1,1) returns with t(5) innovations; returns (returns, conditional vols)."""
    alpha, gamma, beta, freedom = 0.12, 0.16, 0.74, 5
    omega = daily_vol**2 * (1 - alpha - gamma / 2 - beta)
    scale = math.sqrt((freedom - 2) / freedom)
    variance = daily_vol**2
    returns, vols = np.zeros(days), np.zeros(days)
    for day in range(days):
        vols[day] = math.sqrt(variance)
        value = vols[day] * scale * rng.standard_t(freedom) + (shocks or {}).get(day, 0.0)
        returns[day] = value
        variance = omega + (alpha + gamma * (value < 0)) * value**2 + beta * variance
    return returns, vols


class Events:
    """Writes the RuntimeEvent stream the progress and diagnostics tabs read."""

    def __init__(self, path: Path, run_id: str):
        self.handle = path.open("w", encoding="utf-8")
        self.run_id = run_id
        self.count = 0

    def emit(self, event: EventType, scope: Scope, stage: str, message: str, **fields: Any) -> None:
        self.count += 1
        at = utc_now()
        record = RuntimeEvent(
            event_id=self.count, run_id=self.run_id, event=event, scope=scope, stage=stage, message=message,
            started_at=at, ended_at=None if event == EventType.STARTED else at, **fields,
        )
        self.handle.write(json.dumps(record.as_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    def stage(self, scope: Scope, stage: str, started: str, finished: str, *, input_summary=None,
              output_summary=None, duration_ms=None, **fields: Any) -> None:
        self.emit(EventType.STARTED, scope, stage, started, input_summary=input_summary or {}, **fields)
        self.emit(EventType.COMPLETED, scope, stage, finished, output_summary=output_summary or {},
                  duration_ms=duration_ms, **fields)


def message(role: str, content: str) -> dict[str, Any]:
    return {"role": role, "content": content, "chars": len(content), "est_tokens": max(1, len(content) // 2)}


def llm_record(agent: dict, day: str, summary: str, reply: str, rng: np.random.Generator) -> dict[str, Any]:
    """One short illustrative context, so the inspector opens. Latency and tokens are synthetic."""
    messages = {
        "system": message("system", agent["prompt"] or agent["sys_prompt"] or "你是一位 A 股投资者。"),
        "environment": message("user", f"【合成示例，未调用模型】今天是 {day}。行情摘要：{summary}"),
        "belief": message("assistant", agent["belief"][:300] or "保持观察。"),
        "ask": message("user", "请给出今天的交易决策和理由。"),
        "reply": message("assistant", reply),
    }
    steps = (
        ("_get_environment_info", ["system", "environment"], "belief"),
        ("_make_final_decision", ["system", "environment", "belief", "ask"], "reply"),
    )
    calls = []
    for seq, (step, ids, reply_id) in enumerate(steps, 1):
        estimated = sum(messages[item]["est_tokens"] for item in ids)
        calls.append({
            "seq": seq, "step": step, "stage": "interpretation", "started_at": utc_now(),
            "latency_ms": round(float(rng.uniform(1200, 6000)), 1), "status": "ok", "error": None,
            "message_ids": ids, "new_ids": ids if seq == 1 else ids[2:], "est_input_tokens": estimated,
            "prompt_tokens": estimated, "completion_tokens": messages[reply_id]["est_tokens"],
            "max_tokens": 4096, "finish_reason": "stop", "reply_id": reply_id,
        })
    return {"agent_id": agent["id"], "simulation_date": day, "attempt": 1, "model": SYNTHETIC_MODEL,
            "context_window": 16384, "calls": calls, "messages": messages}


def load_agents(database: Path, beliefs: dict[str, str]) -> tuple[list[dict], list[str], list[dict]]:
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        columns = [row[1] for row in conn.execute("PRAGMA table_info(Profiles)")]
        rows = [dict(row) for row in conn.execute("SELECT * FROM Profiles ORDER BY user_id")]
        strategies = {str(row[0]): row[1] for row in conn.execute("SELECT user_id,strategy FROM Strategy")}
        stocks = [dict(row) for row in conn.execute("SELECT stock_id,name,industry,weight FROM StockProfile")]
    agents = []
    for row in rows:
        positions = json.loads(row["cur_positions"] or "{}")
        returns = json.loads(row["stock_returns"] or "{}")
        agents.append({
            **row, "id": row["user_id"], "cash": float(row["current_cash"]), "positions": positions,
            "profits": returns, "strategy_name": strategies.get(row["user_id"], "技术面"),
            "belief": beliefs.get(row["user_id"], ""), "follows": set(json.loads(row["fol_ind"] or "[]")),
            "turnover": TURNOVER_WEIGHT.get(row["bh_annual_turnover_category"], 1.0),
            "disposition": row["bh_disposition_effect_category"] == "高",
        })
    return agents, columns, stocks


def base_graph(rng: np.random.Generator, agents: list[dict], fitness: np.ndarray) -> dict[tuple[int, int], float]:
    """Agents connect through shared interests, with a few high-fitness hubs."""
    edges: dict[tuple[int, int], float] = {}
    target = 7 / max(len(agents) - 1, 1)
    mean_fitness = fitness.mean()
    for left in range(len(agents)):
        for right in range(left + 1, len(agents)):
            union = agents[left]["follows"] | agents[right]["follows"]
            overlap = len(agents[left]["follows"] & agents[right]["follows"]) / len(union) if union else 0.0
            chance = target * (0.3 + 2 * overlap) * math.sqrt(fitness[left] * fitness[right]) / mean_fitness
            if rng.random() < chance:
                edges[(left, right)] = round(0.3 + 0.7 * overlap, 3)
    return edges


def rolling(values: list[float], window: int) -> float:
    tail = values[-window:]
    return round(sum(tail) / len(tail), 4) if tail else 0.0


def generate(settings: Settings, registry: RunRegistry, *, run_id: str = DEFAULT_RUN_ID, user_count: int = 100,
             start: str = "2023-06-15", end: str = "2023-12-29", seed: int = 20261107,
             replace: bool = False) -> dict[str, Any]:
    output = settings.runs_dir / run_id
    try:
        existing = registry.get(run_id)
    except NotFoundError:
        existing = None
    if existing and not existing["config"].get("synthetic_demo"):
        raise ConflictError(f"{run_id} 是真实实验，拒绝覆盖")
    if (existing or output.exists()) and not replace:
        raise ConflictError(f"{run_id} 已存在；确认要重新生成时请加 --replace")
    if output.exists() and not existing:
        raise ConflictError(f"{output} 已存在但未注册，无法确认它是示例数据，请手动处理")
    if existing:
        with registry.lock, registry.connection() as conn:
            conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        shutil.rmtree(output, ignore_errors=True)

    config = normalize_config(
        {"user_count": user_count, "start_date": start, "end_date": end, "max_workers": 8, "activate_prob": 0.8,
         "prob_of_technical": 0.5, "random_seed": seed}, settings,
    )
    config["synthetic_demo"] = True
    dates = trading_dates(settings, start, end)
    if not dates:
        raise ValueError("所选区间没有交易日")

    output.mkdir(parents=True, mode=0o700)
    try:
        return _write_run(settings, registry, output, run_id, config, dates, seed)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _write_run(settings: Settings, registry: RunRegistry, output: Path, run_id: str, config: dict, dates: list[str],
               seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    user_count = config["user_count"]
    database = output / f"user_{user_count}.db"
    belief_path = output / f"belief_{user_count}.csv"
    ids = prepare_database(settings.input_path(config["template_db"]), database, user_count)
    write_beliefs(settings.project_path(config["belief_source"]), belief_path, ids)
    with belief_path.open(newline="", encoding="utf-8") as handle:
        beliefs = {row["user_id"]: row["belief"] for row in csv.DictReader(handle)}

    manifest = write_manifest(output, config, settings)
    manifest["synthetic"] = {"generator": "scripts/generate_demo_run.py", "seed": seed, "model_called": False,
                             "note": "起始日后的行情、交易和帖子都是合成数据；典型事实由构造产生，不是研究结论。"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    agents, profile_columns, stocks = load_agents(database, beliefs)
    days = len(dates)
    events_by_day = {int(days * position): (shock, text) for position, shock, text in MARKET_EVENTS}
    factor, factor_vol = garch_path(rng, days, 0.0105, {day: shock for day, (shock, _) in events_by_day.items()})

    # Market state, continuing from the last real close before the run.
    with closing(sqlite3.connect(database)) as conn:
        last_rows = conn.execute(
            f"SELECT {','.join(STOCK_COLUMNS)} FROM StockData WHERE date=(SELECT max(date) FROM StockData)"
        ).fetchall()
        history = {
            stock["stock_id"]: [
                row[0] for row in conn.execute(
                    "SELECT close_price FROM StockData WHERE stock_id=? ORDER BY date DESC LIMIT 30",
                    (stock["stock_id"],),
                )
            ][::-1]
            for stock in stocks
        }
    last = {row[0]: dict(zip(STOCK_COLUMNS, row)) for row in last_rows}
    prices = {code: float(row["close_price"]) for code, row in last.items()}
    first_price = dict(prices)

    # Seed cost bases from the template's reported profit where it has one.
    for agent in agents:
        agent["costs"] = {}
        for code in agent["positions"]:
            profit = float((agent["profits"].get(code) or {}).get("profit") or 0)
            agent["costs"][code] = prices[code] / max(1 + profit / 100, 0.1)
        agent["shares"] = {code: int(item["shares"]) for code, item in agent["positions"].items()}
        agent["total"] = agent["cash"] + sum(agent["shares"][code] * prices[code] for code in agent["shares"])

    paths = {}
    for stock in stocks:
        beta = float(rng.uniform(0.75, 1.25))
        idio, idio_vol = garch_path(rng, days, float(rng.uniform(0.004, 0.007)))
        paths[stock["stock_id"]] = {"returns": beta * factor + idio, "vol": np.sqrt((beta * factor_vol) ** 2 + idio_vol**2)}
    float_shares = {stock["stock_id"]: sum(agent["shares"].get(stock["stock_id"], 0) for agent in agents)
                    for stock in stocks}
    log_volume = {stock["stock_id"]: 0.0 for stock in stocks}
    volumes: dict[str, list[float]] = {stock["stock_id"]: [] for stock in stocks}

    fitness = rng.lognormal(0, 0.9, len(agents))
    agent_bias = rng.normal(0, 0.35, len(agents))
    edges = base_graph(rng, agents, fitness)
    interaction: dict[tuple[int, int], float] = {}
    neighbors: dict[int, set[int]] = {index: set() for index in range(len(agents))}
    for left, right in edges:
        neighbors[left].add(right)
        neighbors[right].add(left)

    forum_path = output / f"forum_{user_count}.db"
    forum = sqlite3.connect(forum_path)
    forum.executescript(
        """
        CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, content TEXT NOT NULL,
            score INTEGER DEFAULT 0, belief TEXT, type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE reactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, post_id INTEGER NOT NULL,
            type TEXT CHECK(type IN ('repost', 'like', 'unlike')) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (post_id) REFERENCES posts(id));
        CREATE TABLE post_references (id INTEGER PRIMARY KEY AUTOINCREMENT, reference_id INTEGER NOT NULL,
            repost_id INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reference_id) REFERENCES posts(id), FOREIGN KEY (repost_id) REFERENCES posts(id),
            UNIQUE(reference_id, repost_id));
        """
    )

    for name in ("trading_records", "post_records", "reaction_records", "simulation_results", "graphs", "llm_context"):
        (output / name).mkdir()
    events = Events(output / "events.jsonl", run_id)
    events.stage(Scope.RUN, "prepare", "正在准备独立运行目录", "运行输入已准备", output_summary={"users": user_count},
                 duration_ms=60)
    events.emit(EventType.STARTED, Scope.RUN, "run", "合成示例数据：以下事件由生成脚本写入，未运行模型")
    events.emit(EventType.STARTED, Scope.RUN, "simulation", "模拟主流程开始")
    log_lines = ["合成示例数据：scripts/generate_demo_run.py，未调用模型"]

    posts: list[dict[str, Any]] = []  # author index, tone, day index and popularity of every post
    sentiment = 0.0
    previous_day = None
    user_db = sqlite3.connect(database)
    retry_days = {days // 3: 0, 2 * days // 3: min(1, len(agents) - 1)}

    for day_index, day in enumerate(dates):
        stamp = f"{day} 00:00:00"
        active = rng.random(len(agents)) < config["activate_prob"]
        decisions: dict[str, dict] = {agents[i]["id"]: {"stock_decisions": {}} for i in np.flatnonzero(active)}
        order_book: dict[str, dict[str, list]] = {}
        summary_rows = []
        trade_rows = []
        traded: dict[int, tuple[str, str]] = {}
        factor_z = factor[day_index] / max(factor_vol[day_index], 1e-6)
        sentiment = 0.8 * sentiment + 0.2 * float(np.tanh(factor_z))

        for stock in stocks:
            code = stock["stock_id"]
            value = float(paths[code]["returns"][day_index])
            # A-share style daily limit, then a tick-rounded close like the matching engine reports.
            close = round(prices[code] * math.exp(float(np.clip(value, -0.095, 0.095))), 2)
            sigma = float(paths[code]["vol"][day_index])
            z = value / max(sigma, 1e-6)
            log_volume[code] = 0.6 * log_volume[code] + 0.35 * abs(z) + float(rng.normal(0, 0.28))
            target = int(0.006 * float_shares[code] * math.exp(log_volume[code]))
            tilt = float(np.clip(z * 0.25, -0.5, 0.5))

            buys, sells = [], []
            for index in rng.permutation(np.flatnonzero(active)):
                agent = agents[index]
                held = agent["shares"].get(code, 0)
                follow = 1.6 if stock["industry"] in agent["follows"] else 0.6
                base = 0.10 * agent["turnover"] * (1 + 0.5 * abs(z)) * follow
                sell_chance = base * (1 - tilt) * (1.5 if agent["disposition"] and close > agent["costs"].get(code, close) else 1)
                draw = rng.random()
                if draw < base * (1 + tilt):
                    quantity = int(agent["cash"] * rng.uniform(0.03, 0.15) / close / 100) * 100
                    if quantity:
                        buys.append([int(index), quantity])
                elif draw < base * (1 + tilt) + sell_chance and held >= 100:
                    quantity = max(100, int(held * rng.uniform(0.1, 0.6) / 100) * 100)
                    sells.append([int(index), quantity])

            matched = int(min(target, sum(q for _, q in buys), sum(q for _, q in sells)) / 100) * 100
            book = {"buy": [], "sell": []}
            fills: dict[str, dict[int, int]] = {"buy": {}, "sell": {}}
            for side, orders, sign in (("buy", buys, 1), ("sell", sells, -1)):
                remaining = matched
                for index, quantity in orders:
                    fill = min(remaining, quantity)
                    remaining -= fill
                    if fill:
                        fills[side][index] = fill
                    # Filled orders crossed the close; an unfilled one was resting away from it.
                    away = rng.uniform(0, 0.015) * (1 if fill else -1)
                    limit = close if 0 < fill < quantity else round(close * (1 + sign * away), 2)
                    second = int(rng.integers(0, 19800))
                    book[side].append({
                        "price": limit, "quantity": quantity, "user_id": agents[index]["id"],
                        "timestamp": f"{day} {9 + second // 3600:02d}:{second % 3600 // 60:02d}:{second % 60:02d}",
                    })
                    agent = agents[index]
                    before = agent["shares"].get(code, 0)
                    after = before + sign * quantity
                    decisions[agent["id"]]["stock_decisions"][code] = {
                        "action": side, "target_price": limit,
                        "target_position": round(after * close / agent["total"] * 100, 2),
                        "cur_position": round(before * close / agent["total"] * 100, 2),
                        "quantity": quantity, "sub_orders": [{"quantity": quantity, "price": limit}],
                    }
            order_book[code] = book

            large = 0
            for side, sign in (("buy", 1), ("sell", -1)):
                for index, fill in fills[side].items():
                    agent = agents[index]
                    held = agent["shares"].get(code, 0)
                    if side == "buy":
                        agent["costs"][code] = (agent["costs"].get(code, close) * held + close * fill) / (held + fill)
                    agent["shares"][code] = held + sign * fill
                    agent["cash"] += -sign * fill * close
                    if agent["shares"][code] == 0:
                        del agent["shares"][code]
                        agent["costs"].pop(code, None)
                    if fill >= LARGE_ORDER_SHARES:
                        large += sign * fill
                    trade_rows.append((agent["id"], day, stock["industry"], code, close, stock["name"], side, fill, 1))
                    traded[index] = (side, code)

            previous = prices[code]
            prices[code] = close
            volumes[code].append(matched)
            history[code].append(close)
            ratio = close / float(last[code]["close_price"])
            row = {
                "stock_id": code, "date": day, "close_price": close, "pre_close": previous,
                "change": round(close - previous, 2), "pct_chg": round((close / previous - 1) * 100, 2),
                **{key: round(float(last[code][key] or 0) * ratio, 4) for key in ("pe_ttm", "pb", "ps_ttm")},
                "dv_ttm": round(float(last[code]["dv_ttm"] or 0) / ratio, 4), "vol": matched,
                "vol_5": rolling(volumes[code], 5), "vol_10": rolling(volumes[code], 10),
                "vol_30": rolling(volumes[code], 30),
                "ma_hfq_5": rolling(history[code], 5), "ma_hfq_10": rolling(history[code], 10),
                "ma_hfq_30": rolling(history[code], 30), "elg_amount_net": round(large * close / 10000, 2),
            }
            user_db.execute(
                f"INSERT INTO StockData ({','.join(STOCK_COLUMNS)}) VALUES ({','.join('?' for _ in STOCK_COLUMNS)})",
                [row[key] for key in STOCK_COLUMNS],
            )
            summary_rows.append({
                "date": day, "stock_code": code, "closing_price": close, "volume": matched,
                "transaction_count": max(len(fills["buy"]), len(fills["sell"])),
                "large_order_net_inflow": row["elg_amount_net"],
            })

        user_db.executemany(
            "INSERT INTO TradingDetails (user_id,date_time,industry,stock_id,price,stock_name,trading_direction,volume,valid) "
            "VALUES (?,?,?,?,?,?,?,?,?)", trade_rows,
        )

        # Accounts: only the latest day keeps the long persona text, which keeps the database small.
        final_day = day_index == days - 1
        for index, agent in enumerate(agents):
            total = agent["cash"] + sum(shares * prices[code] for code, shares in agent["shares"].items())
            positions = {
                code: {"shares": shares, "ratio": round(shares * prices[code] / total * 100, 1)}
                for code, shares in agent["shares"].items()
            }
            stock_returns = {
                code: {"profit": round((prices[code] / agent["costs"].get(code, prices[code]) - 1) * 100, 1),
                       "market_value": round(shares * prices[code], 2)}
                for code, shares in agent["shares"].items()
            }
            agent["total"] = total
            initial = float(agent["ini_cash"])
            row = {column: agent[column] for column in profile_columns if column in agent}
            row.update(
                current_cash=round(agent["cash"], 2), cur_positions=json.dumps(positions, ensure_ascii=False),
                total_value=round(total, 2), total_return=round(total - initial, 2),
                return_rate=round((total - initial) / initial * 100, 2) if initial else 0,
                stock_returns=json.dumps(stock_returns, ensure_ascii=False), created_at=stamp,
                yest_returns=json.dumps(
                    {code: round(float(paths[code]["returns"][day_index]) * 100, 2) for code in agent["shares"]},
                    ensure_ascii=False,
                ),
            )
            if not final_day:
                row.update(sys_prompt="", prompt="", self_description="")
            user_db.execute(
                f"INSERT INTO Profiles ({','.join(profile_columns)}) VALUES ({','.join('?' for _ in profile_columns)})",
                [row.get(column) for column in profile_columns],
            )
            if active[index] and agent["id"] in decisions:
                for code, shares in agent["shares"].items():
                    decisions[agent["id"]]["stock_decisions"].setdefault(code, {
                        "action": "hold", "target_position": positions[code]["ratio"],
                        "cur_position": positions[code]["ratio"], "target_price": prices[code],
                        "quantity": 0, "sub_orders": [],
                    })
        user_db.commit()

        # Forum: tone follows the market and each Agent's own bias.
        event_text = events_by_day.get(day_index, (0, None))[1]
        tone_of = {}
        for index in np.flatnonzero(active):
            stance = agent_bias[index] + 1.1 * sentiment + float(rng.normal(0, 0.3))
            tone_of[int(index)] = "bull" if stance > 0.25 else "bear" if stance < -0.25 else "flat"
        post_records: dict[str, dict] = {}
        reaction_records: dict[str, list] = {}
        new_posts: list[dict[str, Any]] = []
        activity = 1 + 0.35 * abs(factor_z) + (0.8 if event_text else 0)
        for index, tone in tone_of.items():
            if rng.random() > min(0.9, 0.28 * activity * (0.6 + 0.4 * min(fitness[index], 3))):
                continue
            agent = agents[index]
            side, code = traded.get(index, ("hold", None))
            if code is None:
                held = list(agent["shares"]) or [stocks[int(rng.integers(len(stocks)))]["stock_id"]]
                code = held[int(rng.integers(len(held)))]
            name = next(stock["name"] for stock in stocks if stock["stock_id"] == code)
            pct = float(paths[code]["returns"][day_index]) * 100
            pick = lambda options: options[int(rng.integers(len(options)))]  # noqa: E731
            text = pick(OPENINGS[tone])
            if event_text:
                text += f"今天论坛都在聊{event_text}，真假难辨。"
            text += pick(ACTIONS[side]).format(name=name, price=prices[code], pct=pct)
            text += pick(STRATEGY_NOTES.get(agent["strategy_name"], STRATEGY_NOTES["技术面"]))
            post_type = "type1" if rng.random() < 0.4 else "type2"
            cursor = forum.execute(
                "INSERT INTO posts (user_id,content,score,belief,type,created_at) VALUES (?,?,0,?,?,?)",
                (agent["id"], text, agent["belief"], post_type, stamp),
            )
            new_posts.append({"id": cursor.lastrowid, "author": index, "tone": tone, "day": day_index, "pop": 0.0,
                              "text": text})
            post_records[agent["id"]] = {"post": text, "type": post_type, "belief": agent["belief"]}

        # Reposts and reactions read the previous two days, so cascades grow across days.
        recent = [post for post in posts[-1500:] if day_index - post["day"] <= 2]
        interactions_today = 0
        if recent:
            author_fitness = np.array([fitness[post["author"]] for post in recent])
            recency = np.array([0.5 ** (day_index - post["day"] - 1) for post in recent])
            authors = np.array([post["author"] for post in recent])
            tones = np.array([post["tone"] for post in recent])
            for index, tone in tone_of.items():
                reader = agents[index]
                reader_neighbors = neighbors[index]
                weights = author_fitness * recency * (1 + 4 * np.isin(authors, list(reader_neighbors))) * (
                    1 + 1.5 * (tones == tone)
                ) * (authors != index)
                total = weights.sum()
                if total <= 0:
                    continue
                reader_reactions = []
                if rng.random() < min(0.6, 0.2 * activity):
                    popularity = weights * (1 + np.array([post["pop"] for post in recent]))
                    post = recent[int(rng.choice(len(recent), p=popularity / popularity.sum()))]
                    comment = REPOST_COMMENTS[tone][int(rng.integers(3))]
                    author_id = agents[post["author"]]["id"]
                    text = f"{comment} //@{author_id}：{post['text'][:60]}"
                    cursor = forum.execute(
                        "INSERT INTO posts (user_id,content,score,belief,type,created_at) VALUES (?,?,0,NULL,'repost',?)",
                        (reader["id"], text, stamp),
                    )
                    forum.execute("INSERT INTO post_references (reference_id,repost_id,created_at) VALUES (?,?,?)",
                                  (post["id"], cursor.lastrowid, stamp))
                    forum.execute("INSERT INTO reactions (user_id,post_id,type,created_at) VALUES (?,?,'repost',?)",
                                  (reader["id"], post["id"], stamp))
                    post["pop"] += 1
                    new_posts.append({"id": cursor.lastrowid, "author": index, "tone": tone, "day": day_index,
                                      "pop": 0.0, "text": post["text"]})
                    reader_reactions.append({"post_id": post["id"], "reason": comment, "action": "repost"})
                    pair = (min(index, post["author"]), max(index, post["author"]))
                    interaction[pair] = interaction.get(pair, 0.0) + 0.3
                    interactions_today += 1
                for choice in rng.choice(len(recent), size=min(3, len(recent)), replace=False, p=weights / total):
                    post = recent[int(choice)]
                    agrees = post["tone"] == tone
                    draw = rng.random()
                    kind = "like" if agrees and draw < 0.35 else "unlike" if not agrees and draw < 0.15 else None
                    if kind is None:
                        continue
                    forum.execute("INSERT INTO reactions (user_id,post_id,type,created_at) VALUES (?,?,?,?)",
                                  (reader["id"], post["id"], kind, stamp))
                    forum.execute("UPDATE posts SET score=score+? WHERE id=?", (1 if kind == "like" else -1, post["id"]))
                    reader_reactions.append({"post_id": post["id"], "reason": REACTION_REASONS[kind], "action": kind})
                    pair = (min(index, post["author"]), max(index, post["author"]))
                    interaction[pair] = interaction.get(pair, 0.0) + 0.05
                    interactions_today += 1
                if reader_reactions:
                    reaction_records[reader["id"]] = reader_reactions
        posts.extend(new_posts)
        forum.commit()

        # The daily graph: stable interest ties plus recent interactions that fade.
        interaction = {pair: weight * 0.9 for pair, weight in interaction.items() if weight * 0.9 >= 0.05}
        graph = nx.Graph()
        graph.add_nodes_from(agent["id"] for agent in agents)
        for pair in set(edges) | set(interaction):
            weight = edges.get(pair, 0.0) + interaction.get(pair, 0.0)
            graph.add_edge(agents[pair[0]]["id"], agents[pair[1]]["id"], weight=round(weight, 3))
        save_graph_json(graph, f"user_graph_{user_count}_{day}", str(output / "graphs"))

        # Per-day artifacts.
        (output / "trading_records" / f"{day}.json").write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
        (output / "trading_records" / f"{day}_orders.json").write_text(json.dumps(order_book, ensure_ascii=False), encoding="utf-8")
        (output / "post_records" / f"{day}.json").write_text(json.dumps(post_records, ensure_ascii=False), encoding="utf-8")
        (output / "reaction_records" / f"{day}.json").write_text(json.dumps(reaction_records, ensure_ascii=False), encoding="utf-8")
        results = output / "simulation_results" / day
        results.mkdir()
        with (results / f"daily_summary_{day}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
            writer.writeheader()
            writer.writerows(summary_rows)

        if day_index >= days - LLM_CONTEXT_DAYS:
            folder = output / "llm_context" / day
            folder.mkdir()
            market_line = "；".join(f"{row['stock_code']} 收盘 {row['closing_price']}" for row in summary_rows[:5])
            for index in np.flatnonzero(active):
                agent = agents[index]
                record = llm_record(agent, day, market_line, json.dumps(decisions[agent["id"]], ensure_ascii=False)[:600], rng)
                (folder / f"{agent['id']}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        # Run events. Agent-level events are written for the final day only: the snapshot keeps
        # just the latest day per Agent, and all 13 000 Agent-days would add nothing to any view.
        active_count = int(active.sum())
        events.stage(Scope.DAY, "prepare", "正在准备当日市场环境", "当日环境已准备", simulation_date=day,
                     output_summary={"agents": user_count, "trading_day": True, "graph_nodes": user_count},
                     duration_ms=int(rng.integers(20, 90)))
        events.emit(EventType.STARTED, Scope.DAY, "agents", "正在处理 Agent", simulation_date=day,
                    input_summary={"agents": active_count, "max_workers": config["max_workers"]})
        if day_index in retry_days:
            agent_id = agents[retry_days[day_index]]["id"]
            events.emit(EventType.RETRYING, Scope.AGENT, "interpretation", "LLM 请求超时，正在重试（合成示例）",
                        simulation_date=day, agent_id=agent_id, error_code="LLM_TIMEOUT",
                        error_summary="合成示例：演示诊断面板如何显示重试事件", duration_ms=45_000)
        if final_day:
            _final_day_agent_events(events, agents, active, decisions, tone_of, day, rng)
        events.emit(EventType.COMPLETED, Scope.DAY, "agents", "Agent 处理阶段完成", simulation_date=day,
                    output_summary={"processed": active_count, "failed": 0},
                    duration_ms=int(active_count * rng.uniform(1500, 3000) / config["max_workers"]))
        matched_total = sum(row["transaction_count"] for row in summary_rows)
        events.stage(Scope.DAY, "market", "正在执行市场撮合", "市场撮合完成", simulation_date=day,
                     output_summary={"decisions": active_count, "matches": matched_total},
                     duration_ms=int(rng.integers(30, 120)))
        events.emit(EventType.COMPLETED, Scope.MARKET, "matching", "市场撮合产物已写入", simulation_date=day,
                    output_summary={"decisions": active_count, "matches": matched_total})
        events.stage(Scope.DAY, "social", "正在更新社交环境", "社交环境更新完成", simulation_date=day,
                     output_summary={"posts": len(new_posts), "forum_actions": interactions_today},
                     duration_ms=int(rng.integers(5, 40)))
        events.emit(EventType.COMPLETED, Scope.SOCIAL, "update", "论坛互动已提交", simulation_date=day,
                    output_summary={"posts": len(new_posts), "interactions": interactions_today})
        events.stage(Scope.DAY, "commit", "正在提交当日 checkpoint", "当日提交完成", simulation_date=day, duration_ms=2)

        log_lines += [
            f"=== 当前日期: {day} ===", "交易日: True",
            f"用户关系图属性: {graph.number_of_nodes()} 个节点, {graph.number_of_edges()} 条边",
            f"开始处理 {active_count} 个用户，使用 {config['max_workers']} 个工作线程",
            f"成功处理 {active_count} 个用户",
        ]
        previous_day = day

    events.emit(EventType.COMPLETED, Scope.RUN, "simulation", "模拟主流程完成")
    events.emit(EventType.COMPLETED, Scope.RUN, "run", "合成示例数据生成完成")
    events.handle.close()
    log_lines.append("=== 模拟系统运行完成 ===")
    (output / "simulation.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    user_db.close()
    forum.close()

    next_date = (datetime.fromisoformat(previous_day) + timedelta(days=1)).date().isoformat()
    checkpoint = {"last_completed_date": previous_day, "next_date": next_date}
    (output / "state.json").write_text(
        json.dumps({"checkpoint": checkpoint, "status": "completed", "updated_at": utc_now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    registry.create(run_id, RUN_NAME, RUN_DESCRIPTION, config, output)
    at = utc_now()
    registry.update(run_id, status="completed", health="healthy", started_at=at, ended_at=at, last_heartbeat=at,
                    exit_code=0, checkpoint_json=json.dumps(checkpoint), last_event_id=events.count)
    return {"run_id": run_id, "days": days, "posts": len(posts), "events": events.count,
            "index_change": {code: round((prices[code] / first_price[code] - 1) * 100, 1) for code in prices}}


def _final_day_agent_events(events: Events, agents, active, decisions, tone_of, day, rng) -> None:
    for index, agent in enumerate(agents):
        common = {"simulation_date": day, "agent_id": agent["id"],
                  "worker_id": f"worker-{index % 8 + 1}"}
        events.stage(Scope.AGENT, "state", "正在读取 Agent 状态", "Agent 状态已就绪",
                     output_summary={"strategy": agent["strategy_name"], "active": bool(active[index]),
                                     "cash": round(agent["cash"]), "positions": len(agent["shares"])},
                     duration_ms=1, **common)
        if not active[index]:
            for stage in ("perception", "interpretation", "decision", "action"):
                events.emit(EventType.SKIPPED, Scope.AGENT, stage, "今日未激活，跳过", **common)
            events.stage(Scope.AGENT, "commit", "正在提交 Agent 产物", "Agent 产物已提交", duration_ms=0, **common)
            continue
        choices = decisions[agent["id"]]["stock_decisions"].values()
        counts = {name: sum(item["action"] == name for item in choices) for name in ("buy", "sell", "hold")}
        counts["orders"] = counts["buy"] + counts["sell"]
        events.stage(Scope.AGENT, "perception", "正在收集市场与社交信息", "感知信息已准备",
                     output_summary={"news_available": False, "candidate_assets": len(choices)},
                     duration_ms=int(rng.integers(20, 60)), **common)
        events.stage(Scope.AGENT, "interpretation", "正在形成市场判断", "市场判断已完成",
                     output_summary={"tone": tone_of.get(index), "llm_called": False},
                     duration_ms=int(rng.integers(1500, 9000)), **common)
        events.stage(Scope.AGENT, "decision", "正在生成交易意图", "交易意图已生成",
                     output_summary={"has_decision": True, **counts}, duration_ms=1, **common)
        events.stage(Scope.AGENT, "action", "正在生成可执行动作", "可执行动作已生成",
                     output_summary=counts, duration_ms=1, **common)
        events.stage(Scope.AGENT, "commit", "正在提交 Agent 产物", "Agent 产物已提交", duration_ms=0, **common)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--agents", type=int, default=100)
    parser.add_argument("--start", default="2023-06-15")
    parser.add_argument("--end", default="2023-12-29")
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--replace", action="store_true", help="重新生成已有的合成示例；不会覆盖真实实验")
    args = parser.parse_args()
    settings = get_settings()
    result = generate(settings, RunRegistry(settings.registry_path), run_id=args.run_id, user_count=args.agents,
                      start=args.start, end=args.end, seed=args.seed, replace=args.replace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
