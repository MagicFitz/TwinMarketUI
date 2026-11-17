"""Read-only research analyses computed from a run's real output artifacts.

Every response states its sample size, and a view whose sample is too small
reports why instead of returning a statistic that would look meaningful.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import warnings
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

from server.results import _run_folder, _safe_directory, _safe_files

# Below these sizes the statistics are too unstable to read as evidence.
MIN_DISTRIBUTION_DAYS = 20
MIN_DISTRIBUTION_OBSERVATIONS = 30
MIN_SCATTER_DAYS = 10
MIN_SCATTER_OBSERVATIONS = 30
MIN_ACF_DAYS = 30
MIN_GARCH_DAYS = 100

LEADER_LIMIT = 20
CASCADE_LIMIT = 10
MAX_NETWORK_NODES = 150
MAX_NETWORK_EDGES = 1500
EXCERPT_LENGTH = 120
MIN_DISPOSITION_DECISIONS = 30
MIN_TURNOVER_AGENTS = 5
ACTIVE_AGENT_LIMIT = 20
KDE_GRID_POINTS = 120
ACF_MAX_LAG = 10

_GRAPH_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})\.(?:json|pkl)$")


def _number(value: Any, digits: int = 6) -> float | None:
    """JSON has no NaN or infinity, so undefined statistics become null."""
    if value is None:
        return None
    result = float(value)
    return round(result, digits) if math.isfinite(result) else None


def _correlation(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 2 or np.std(left) == 0 or np.std(right) == 0:
        return None
    return _number(np.corrcoef(left, right)[0, 1], 4)


def _database(run: dict[str, Any], prefix: str) -> Path | None:
    folder = _run_folder(run)
    path = (folder / f"{prefix}_{run['config']['user_count']}.db").resolve()
    return path if path.is_relative_to(folder) and path.is_file() else None


def _rows(path: Path, query: str) -> list[tuple]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.execute("PRAGMA query_only = ON")
        return conn.execute(query).fetchall()


def _graph_days(folder: Path) -> tuple[dict[str, Path], int]:
    """Map dates to JSON graphs, counting days that only have a pickle.

    Pickles are never loaded here: deserializing one can execute code, so this
    long-running service only reads JSON.  ``scripts/export_graphs_json.py``
    converts legacy pickles as an explicit local step.
    """
    graphs = _safe_directory(folder, "graphs")
    if graphs is None:
        return {}, 0
    days: dict[str, Path] = {}
    for path in _safe_files(graphs, "*.json"):
        match = _GRAPH_DATE.search(path.name)
        if match:
            days[match.group(1)] = path
    pickles = {
        match.group(1)
        for path in _safe_files(graphs, "*.pkl")
        if (match := _GRAPH_DATE.search(path.name))
    }
    return days, len(pickles - set(days))


def _load_graph(path: Path) -> nx.Graph | None:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        graph = nx.Graph()
        graph.add_nodes_from(str(node) for node in content["nodes"])
        graph.add_weighted_edges_from(
            (str(left), str(right), float(weight)) for left, right, weight in content["edges"]
        )
        return graph
    except (OSError, ValueError, KeyError, TypeError):
        return None


@lru_cache(maxsize=8)
def _cached_layout(paths: tuple[Path, ...], _signature: int) -> dict[str, tuple[float, float]]:
    union = nx.Graph()
    for path in paths:
        graph = _load_graph(path)
        if graph is None:
            continue
        union.add_nodes_from(graph.nodes)
        for left, right, data in graph.edges(data=True):
            weight = data.get("weight", 0.0) + (union[left][right]["weight"] if union.has_edge(left, right) else 0.0)
            union.add_edge(left, right, weight=weight)
    if not union.number_of_nodes():
        return {}
    layout = nx.spring_layout(union, weight="weight", seed=7, iterations=50)
    return {node: (round(float(x), 4), round(float(y), 4)) for node, (x, y) in layout.items()}


def _layout(graph_days: dict[str, Path]) -> dict[str, tuple[float, float]]:
    """One layout over every day's edges, so scrubbing through time moves edges rather than nodes."""
    paths = tuple(graph_days[day] for day in sorted(graph_days))
    signature = hash(tuple(path.stat().st_mtime_ns for path in paths))
    return _cached_layout(paths, signature)


def _network(
    graph: nx.Graph, date: str, dates: list[str], layout: dict[str, tuple[float, float]]
) -> tuple[dict[str, Any], dict[str, dict]]:
    pagerank = nx.pagerank(graph, weight="weight") if graph.number_of_edges() else {}
    metrics = {
        node: {
            "degree": graph.degree(node),
            "weighted_degree": _number(graph.degree(node, weight="weight"), 4),
            "pagerank": _number(pagerank.get(node), 5),
        }
        for node in graph.nodes
    }
    kept = sorted(graph.nodes, key=lambda node: metrics[node]["weighted_degree"] or 0, reverse=True)[
        :MAX_NETWORK_NODES
    ]
    kept_set = set(kept)
    edges = sorted(
        (
            (left, right, data.get("weight", 0.0))
            for left, right, data in graph.edges(data=True)
            if left in kept_set and right in kept_set
        ),
        key=lambda edge: edge[2],
        reverse=True,
    )[:MAX_NETWORK_EDGES]
    return (
        {
            "date": date,
            "dates": dates,
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "density": _number(nx.density(graph), 4),
            "truncated": len(kept) < graph.number_of_nodes() or len(edges) < graph.number_of_edges(),
            "nodes": [{"id": node, "position": layout.get(node), **metrics[node]} for node in kept],
            "edges": [[left, right, _number(weight, 4)] for left, right, weight in edges],
        },
        metrics,
    )


def _cascades(
    posts: dict[int, tuple], references: list[tuple[int, int]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Walk repost trees from their original posts; a repost can itself be reposted."""
    children: dict[int, list[int]] = defaultdict(list)
    reposts = set()
    for parent, child in references:
        children[parent].append(child)
        reposts.add(child)

    cascades = []
    reach: Counter[str] = Counter()
    for root in children:
        if root in reposts or root not in posts:
            continue
        seen = {root}
        depth = 0
        frontier = [root]
        while frontier:
            frontier = [child for post in frontier for child in children.get(post, []) if child not in seen]
            seen.update(frontier)
            if frontier:
                depth += 1
        author, content, _kind, day = posts[root]
        size = len(seen) - 1
        reach[author] += size
        cascades.append(
            {
                "post_id": root,
                "agent_id": author,
                "date": day,
                "excerpt": (content or "")[:EXCERPT_LENGTH],
                "size": size,
                "depth": depth,
            }
        )
    cascades.sort(key=lambda item: (item["size"], item["depth"]), reverse=True)
    return cascades, reach


def analysis_propagation(run: dict[str, Any], date: str | None = None) -> dict[str, Any]:
    source = "forum database posts/reactions/post_references, graphs/*.json"
    database = _database(run, "forum")
    if database is None:
        return {"available": False, "reason": "该实验还没有论坛数据库。", "source": source}

    posts = {
        row[0]: row[1:]
        for row in _rows(database, "SELECT id,user_id,content,type,date(created_at) FROM posts ORDER BY id")
    }
    if not posts:
        return {"available": False, "reason": "该实验没有产生论坛帖子，无法分析信息传播。", "source": source}
    references = _rows(database, "SELECT reference_id,repost_id FROM post_references")
    reactions = _rows(database, "SELECT post_id,type,date(created_at) FROM reactions")

    daily: dict[str, dict[str, Any]] = {}
    authors_by_day: dict[str, set[str]] = defaultdict(set)
    for author, _content, kind, day in posts.values():
        row = daily.setdefault(day, {"date": day, "original": 0, "reposts": 0, "likes": 0, "unlikes": 0})
        row["reposts" if kind == "repost" else "original"] += 1
        authors_by_day[day].add(author)
    received: dict[str, Counter[str]] = defaultdict(Counter)
    for post_id, kind, day in reactions:
        if kind in {"like", "unlike"}:
            if day in daily:
                daily[day][f"{kind}s"] += 1
            if post_id in posts:
                received[posts[post_id][0]][kind] += 1
    for day, row in daily.items():
        row["authors"] = len(authors_by_day[day])

    cascades, reach = _cascades(posts, references)
    reposted: Counter[str] = Counter()
    for parent, _child in references:
        if parent in posts:
            reposted[posts[parent][0]] += 1
    original_by_author = Counter(row[0] for row in posts.values() if row[2] != "repost")

    folder = _run_folder(run)
    graph_days, pickle_only = _graph_days(folder)
    dates = sorted(graph_days)
    if date is not None and date not in graph_days:
        raise ValueError("该日期没有关系网络")
    network = None
    metrics: dict[str, dict] = {}
    selected = date or (dates[-1] if dates else None)
    graph = _load_graph(graph_days[selected]) if selected else None
    if graph is not None:
        network, metrics = _network(graph, selected, dates, _layout(graph_days))
    network_reason = None
    if network is None:
        network_reason = (
            "该实验的关系网络只有旧版 pickle 文件。可在 backend 目录运行 "
            f"python scripts/export_graphs_json.py runs/{folder.name} 转换后查看。"
            if pickle_only
            else "该实验没有可读取的关系网络文件。"
        )

    authors = set(reposted) | set(original_by_author)
    leaders = [
        {
            "agent_id": author,
            "original_posts": original_by_author[author],
            "reposted": reposted[author],
            "reach": reach[author],
            "likes": received[author]["like"],
            "unlikes": received[author]["unlike"],
            **metrics.get(author, {"degree": None, "weighted_degree": None, "pagerank": None}),
        }
        for author in authors
    ]
    leaders.sort(key=lambda item: (item["reach"], item["reposted"], item["likes"]), reverse=True)

    depths = Counter(item["depth"] for item in cascades)
    repost_count = sum(1 for row in posts.values() if row[2] == "repost")
    return {
        "available": True,
        "reason": None,
        "source": source,
        "sample": {
            "posts": len(posts),
            "original_posts": len(posts) - repost_count,
            "reposts": repost_count,
            "reactions": len(reactions),
            "authors": len({row[0] for row in posts.values()}),
            "days": len(daily),
            "cascades": len(cascades),
            "max_depth": max(depths, default=0),
        },
        "daily": [daily[day] for day in sorted(daily)],
        "leaders": leaders[:LEADER_LIMIT],
        "cascades": cascades[:CASCADE_LIMIT],
        "depth_distribution": [{"depth": depth, "count": depths[depth]} for depth in sorted(depths)],
        "network": network,
        "network_reason": network_reason,
    }


def analysis_network(run: dict[str, Any], date: str) -> dict[str, Any]:
    """One day of the relationship network, without re-reading the forum on every timeline step."""
    graph_days, _ = _graph_days(_run_folder(run))
    if date not in graph_days:
        raise ValueError("该日期没有关系网络")
    graph = _load_graph(graph_days[date])
    if graph is None:
        raise ValueError("该日期的关系网络无法读取")
    return _network(graph, date, sorted(graph_days), _layout(graph_days))[0]


def _kde(values: np.ndarray) -> list[list[float]]:
    """Gaussian KDE with Silverman's bandwidth, compared against a fitted normal."""
    std = float(np.std(values))
    spread = min(std, float(np.subtract(*np.percentile(values, [75, 25]))) / 1.349) or std
    bandwidth = 0.9 * spread * len(values) ** (-1 / 5)
    grid = np.linspace(values.min() - 3 * bandwidth, values.max() + 3 * bandwidth, KDE_GRID_POINTS)
    density = np.exp(-0.5 * ((grid[:, None] - values) / bandwidth) ** 2).mean(axis=1) / (
        bandwidth * math.sqrt(2 * math.pi)
    )
    normal = np.exp(-0.5 * ((grid - values.mean()) / std) ** 2) / (std * math.sqrt(2 * math.pi))
    return [[_number(x), _number(y, 4), _number(z, 4)] for x, y, z in zip(grid, density, normal)]


def _shortage(days: int, observations: int, need_days: int, need_observations: int = 0) -> str | None:
    if days < need_days:
        return f"至少需要 {need_days} 个交易日，当前只有 {days} 个。"
    if observations < need_observations:
        return f"至少需要 {need_observations} 个观测，当前只有 {observations} 个。"
    return None


def _distribution(returns: np.ndarray, days: int) -> dict[str, Any]:
    reason = _shortage(days, len(returns), MIN_DISTRIBUTION_DAYS, MIN_DISTRIBUTION_OBSERVATIONS)
    if reason is None and np.std(returns) == 0:
        reason = "收益率没有变化，无法估计分布。"
    if reason:
        return {"available": False, "reason": reason}
    z = (returns - returns.mean()) / returns.std()
    return {
        "available": True,
        "reason": None,
        "std": _number(returns.std()),
        "skewness": _number(np.mean(z**3), 4),
        "excess_kurtosis": _number(np.mean(z**4) - 3, 4),
        "tail_share": _number(np.mean(np.abs(z) > 3), 4),
        "normal_tail_share": 0.0027,
        "curve": _kde(returns),
    }


def _leverage(pairs: list[tuple[float, float]], days: int) -> dict[str, Any]:
    reason = _shortage(days, len(pairs), MIN_SCATTER_DAYS, MIN_SCATTER_OBSERVATIONS)
    if reason:
        return {"available": False, "reason": reason}
    previous = np.array([pair[0] for pair in pairs])
    current = np.array([pair[1] for pair in pairs])
    both_negative = (previous < 0) & (current < 0)
    return {
        "available": True,
        "reason": None,
        "observations": len(pairs),
        "return_vs_next_abs_corr": _correlation(previous, np.abs(current)),
        "points": [
            [_number(x), _number(y)] for x, y in zip(current[both_negative], previous[both_negative])
        ],
    }


def _volume_return(pairs: list[tuple[float, float]], days: int, skipped: int) -> dict[str, Any]:
    reason = _shortage(days, len(pairs), MIN_SCATTER_DAYS, MIN_SCATTER_OBSERVATIONS)
    if not pairs and days:
        reason = "该窗口没有成交量记录。"
    if reason:
        return {"available": False, "reason": reason, "zero_volume_skipped": skipped}
    change = np.array([pair[0] for pair in pairs])
    returns = np.array([pair[1] for pair in pairs])
    return {
        "available": True,
        "reason": None,
        "observations": len(pairs),
        "zero_volume_skipped": skipped,
        "volume_return_corr": _correlation(change, returns),
        "volume_abs_return_corr": _correlation(change, np.abs(returns)),
        "points": [[_number(x), _number(y)] for x, y in pairs],
    }


def _autocorrelation(values: np.ndarray) -> list[dict[str, Any]]:
    return [
        {"lag": lag, "value": _correlation(values[:-lag], values[lag:])}
        for lag in range(1, min(ACF_MAX_LAG, len(values) - 1) + 1)
    ]


def _clustering(dates: list[str], index_returns: np.ndarray) -> dict[str, Any]:
    days = len(dates)
    result: dict[str, Any] = {"available": False, "reason": None, "days": days, "acf_abs": []}
    if days >= MIN_ACF_DAYS:
        result["acf_abs"] = _autocorrelation(np.abs(index_returns))
    result["reason"] = _shortage(days, days, MIN_GARCH_DAYS)
    if result["reason"]:
        return result
    if np.std(index_returns) == 0:
        result["reason"] = "指数收益率没有变化，无法拟合 GARCH。"
        return result

    # Imported here because arch is slow to load and only this view needs it.
    from arch import arch_model

    centered = (index_returns - index_returns.mean()) * 100
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = arch_model(centered, mean="Zero", vol="GARCH", p=1, q=1, rescale=False).fit(disp="off")
    except (ValueError, np.linalg.LinAlgError) as error:
        result["reason"] = f"GARCH 拟合失败：{error}"
        return result
    if fit.convergence_flag != 0:
        result["reason"] = "GARCH 拟合未收敛。"
        return result
    scale = float(np.mean(centered**2))
    result.update(
        available=True,
        alpha=_number(fit.params["alpha[1]"], 4),
        beta=_number(fit.params["beta[1]"], 4),
        series=[
            {"date": day, "actual": _number(actual / scale, 4), "expected": _number(expected / scale, 4)}
            for day, actual, expected in zip(dates, centered**2, np.asarray(fit.conditional_volatility) ** 2)
        ],
    )
    return result


def _window_analysis(records: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    dates = sorted({record["date"] for record in records})
    days = len(dates)
    returns = np.array([record["return"] for record in records], dtype=float)

    leverage_pairs = [(record["lag"], record["return"]) for record in records if record["lag"] is not None]
    volume_pairs = [
        (record["volume_change"], record["return"])
        for record in records
        if record["volume_change"] is not None
    ]

    # The market-level series is the mean return weighted by StockProfile.weight.
    totals: dict[str, float] = defaultdict(float)
    weight_sums: dict[str, float] = defaultdict(float)
    for record in records:
        weight = weights.get(record["stock"], 0.0)
        totals[record["date"]] += weight * record["return"]
        weight_sums[record["date"]] += weight
    index_dates = [day for day in dates if weight_sums[day] > 0]
    index_returns = np.array([totals[day] / weight_sums[day] for day in index_dates], dtype=float)

    return {
        "days": days,
        "observations": len(records),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "distribution": _distribution(returns, days)
        if days
        else {"available": False, "reason": "没有数据。"},
        "leverage": _leverage(leverage_pairs, days),
        "volume_return": _volume_return(
            volume_pairs, days, sum(record["zero_volume"] for record in records)
        ),
        "clustering": _clustering(index_dates, index_returns),
    }


def analysis_stylized_facts(run: dict[str, Any]) -> dict[str, Any]:
    source = "user database StockData, StockProfile"
    database = _database(run, "user")
    if database is None:
        return {"available": False, "reason": "该实验还没有用户数据库。", "source": source}
    start, end = run["config"].get("start_date"), run["config"].get("end_date")
    if not start or not end:
        return {"available": False, "reason": "实验配置缺少起止日期。", "source": source}

    weights = {
        stock: float(weight)
        for stock, weight in _rows(database, "SELECT stock_id,weight FROM StockProfile")
        if weight is not None and weight > 0
    }
    rows = _rows(database, "SELECT stock_id,date,close_price,vol FROM StockData ORDER BY stock_id,date")

    # History before start_date only seeds the run, so it must never count as
    # simulated output; it is reported separately as a reference.
    windows: dict[str, list[dict[str, Any]]] = {"simulated": [], "history": []}
    previous: dict[str, tuple] = {}
    for stock, day, close, volume in rows:
        day = str(day)[:10]
        last = previous.get(stock)
        previous[stock] = (day, close, volume, None, None)
        if close is None or close <= 0 or last is None or last[1] is None or last[1] <= 0:
            continue
        value = math.log(close / last[1])
        window = "history" if day < start else "simulated" if day <= end else None
        previous[stock] = (day, close, volume, value, window)
        if window is None:
            continue
        has_volumes = volume is not None and last[2] is not None
        both_positive = has_volumes and volume > 0 and last[2] > 0
        windows[window].append(
            {
                "stock": stock,
                "date": day,
                "return": value,
                # A lag from the seed history would mix the two windows.
                "lag": last[3] if last[4] == window else None,
                "volume_change": math.log(volume / last[2]) if both_positive else None,
                "zero_volume": has_volumes and not both_positive,
            }
        )

    if not rows:
        return {"available": False, "reason": "该实验没有行情数据。", "source": source}
    return {
        "available": True,
        "reason": None,
        "source": source,
        "thresholds": {
            "distribution_days": MIN_DISTRIBUTION_DAYS,
            "scatter_days": MIN_SCATTER_DAYS,
            "garch_days": MIN_GARCH_DAYS,
        },
        "simulated": _window_analysis(windows["simulated"], weights),
        "history": _window_analysis(windows["history"], weights),
    }


def _ratio(part: float, whole: float, digits: int = 4) -> float | None:
    return _number(part / whole, digits) if whole else None


def _order_flow(folder: Path, start: str, end: str) -> dict[tuple[str, str], dict[str, float]]:
    """Ordered quantity per (date, asset, side) from the matching engine's order books."""
    flow: dict[tuple[str, str], dict[str, float]] = {}
    records = _safe_directory(folder, "trading_records")
    for path in _safe_files(records, "*_orders.json") if records is not None else ():
        day = path.stem[:10]
        if not start <= day <= end:
            continue
        try:
            book = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for stock, sides in book.items():
            if not isinstance(sides, dict):
                continue
            flow[(day, stock)] = {
                side: float(sum(order.get("quantity") or 0 for order in sides.get(side) or []))
                for side in ("buy", "sell")
            }
    return flow


def _disposition(profiles: list[tuple], sells: dict[tuple[str, str], set[str]], start: str, end: str) -> dict[str, Any]:
    """Odean's PGR and PLR: on days an Agent sells, how often gains versus losses are realized."""
    previous: dict[str, str] = {}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for agent, day, returns, _value, category in profiles:
        sold = sells.get((agent, day))
        if sold and agent in previous and start <= day <= end:
            for stock, item in json.loads(previous[agent] or "{}").items():
                profit = (item or {}).get("profit") or 0
                if not profit:
                    continue
                kind = "gain" if profit > 0 else "loss"
                counts[category or "未知"][f"{kind}_{'realized' if stock in sold else 'paper'}"] += 1
        previous[agent] = returns

    def rates(counter: Counter[str]) -> dict[str, Any]:
        pgr = _ratio(counter["gain_realized"], counter["gain_realized"] + counter["gain_paper"])
        plr = _ratio(counter["loss_realized"], counter["loss_realized"] + counter["loss_paper"])
        return {"pgr": pgr, "plr": plr, "decisions": sum(counter.values()),
                "difference": _number(pgr - plr, 4) if pgr is not None and plr is not None else None}

    total = sum(counts.values(), Counter())
    decisions = sum(total.values())
    if decisions < MIN_DISPOSITION_DECISIONS:
        return {"available": False, "reason": f"至少需要 {MIN_DISPOSITION_DECISIONS} 个卖出决策观测，当前只有 {decisions} 个。"}
    order = {"高": 0, "中": 1, "低": 2}
    groups = [{"category": name, **rates(counts[name])} for name in sorted(counts, key=lambda name: order.get(name, 9))]
    return {"available": True, "reason": None, **rates(total), "groups": groups}


def analysis_trading(run: dict[str, Any]) -> dict[str, Any]:
    source = "user database TradingDetails, Profiles, StockData; trading_records/*_orders.json"
    database = _database(run, "user")
    if database is None:
        return {"available": False, "reason": "该实验还没有用户数据库。", "source": source}
    start, end = run["config"].get("start_date"), run["config"].get("end_date")
    if not start or not end:
        return {"available": False, "reason": "实验配置缺少起止日期。", "source": source}

    # TradingDetails also holds the seed accounts' pre-run trades, which are not simulated behavior.
    trades = [
        row for row in _rows(
            database,
            "SELECT user_id,substr(date_time,1,10),stock_id,trading_direction,price,volume FROM TradingDetails WHERE valid=1",
        )
        if start <= row[1] <= end
    ]
    if not trades:
        return {"available": False, "reason": "该实验没有成交记录。", "source": source}

    names = dict(_rows(database, "SELECT stock_id,name FROM StockProfile"))
    weights = {
        stock: float(weight) for stock, weight in _rows(database, "SELECT stock_id,weight FROM StockProfile")
        if weight is not None and weight > 0
    }
    closes = _rows(database, "SELECT stock_id,date,close_price FROM StockData ORDER BY stock_id,date")
    returns: dict[tuple[str, str], float] = {}
    last_close: dict[str, float] = {}
    for stock, day, close in closes:
        day = str(day)[:10]
        if close and close > 0:
            if stock in last_close and start <= day <= end:
                returns[(day, stock)] = math.log(close / last_close[stock])
            last_close[stock] = close

    daily: dict[str, dict[str, Any]] = {}
    traders_by_day: dict[str, set[str]] = defaultdict(set)
    assets: dict[str, Counter[str]] = defaultdict(Counter)
    agents: dict[str, Counter[str]] = defaultdict(Counter)
    sells: dict[tuple[str, str], set[str]] = defaultdict(set)
    filled = Counter()
    for agent, day, stock, side, price, volume in trades:
        value = float(price or 0) * float(volume or 0)
        row = daily.setdefault(day, {"date": day, "buy": 0.0, "sell": 0.0})
        if side not in {"buy", "sell"}:
            continue
        row[side] += value
        traders_by_day[day].add(agent)
        assets[stock][side] += value
        agents[agent][side] += value
        agents[agent]["trades"] += 1
        filled[side] += float(volume or 0)
        if side == "sell":
            sells[(agent, day)].add(stock)

    flow = _order_flow(_run_folder(run), start, end)
    ordered = Counter()
    points = []
    for (day, stock), sides in sorted(flow.items()):
        ordered.update(sides)
        total = sides["buy"] + sides["sell"]
        if total and (day, stock) in returns:
            points.append(((sides["buy"] - sides["sell"]) / total, returns[(day, stock)]))
    days = len(daily)
    reason = _shortage(days, len(points), MIN_SCATTER_DAYS, MIN_SCATTER_OBSERVATIONS)
    imbalance: dict[str, Any] = {"available": reason is None, "reason": reason}
    if reason is None:
        imbalance.update(
            correlation=_correlation(np.array([x for x, _ in points]), np.array([y for _, y in points])),
            points=[[_number(x, 4), _number(y)] for x, y in points],
        )

    market: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for (day, stock), value in returns.items():
        weight = weights.get(stock, 0.0)
        market[day][0] += weight * value
        market[day][1] += weight
    rows = []
    for day in sorted(daily):
        row = daily[day]
        total_weight = market[day][1] if day in market else 0
        rows.append({
            **row, "buy": _number(row["buy"], 2), "sell": _number(row["sell"], 2), "traders": len(traders_by_day[day]),
            "market_return": _number(market[day][0] / total_weight) if total_weight else None,
        })

    profiles = _rows(
        database,
        "SELECT user_id,substr(created_at,1,10),stock_returns,total_value,bh_disposition_effect_category "
        "FROM Profiles ORDER BY user_id,created_at",
    )
    disposition = _disposition(profiles, sells, start, end)

    values: dict[str, list[float]] = defaultdict(list)
    for agent, day, _returns, value, _category in profiles:
        if value is not None and day <= end:
            values[agent].append(float(value))
    summaries = []
    for agent, counter in agents.items():
        history = values.get(agent) or []
        mean_value = sum(history) / len(history) if history else 0
        turnover = (counter["buy"] + counter["sell"]) / 2 / mean_value if mean_value else None
        summaries.append({
            "agent_id": agent,
            "trades": counter["trades"],
            "buy": _number(counter["buy"], 2),
            "sell": _number(counter["sell"], 2),
            "turnover": _number(turnover, 4),
            "return": _ratio(history[-1] - history[0], history[0]) if len(history) > 1 else None,
        })
    summaries.sort(key=lambda item: (item["buy"] or 0) + (item["sell"] or 0), reverse=True)
    pairs = [item for item in summaries if item["turnover"] is not None and item["return"] is not None]
    turnover_view: dict[str, Any] = {"available": len(pairs) >= MIN_TURNOVER_AGENTS, "reason": None}
    if turnover_view["available"]:
        turnover_view.update(
            correlation=_correlation(
                np.array([item["turnover"] for item in pairs]), np.array([item["return"] for item in pairs])
            ),
            points=[{"agent_id": item["agent_id"], "turnover": item["turnover"], "return": item["return"]} for item in pairs],
        )
    else:
        turnover_view["reason"] = f"至少需要 {MIN_TURNOVER_AGENTS} 个有成交的 Agent，当前只有 {len(pairs)} 个。"

    asset_rows = [
        {
            "stock_id": stock,
            "name": names.get(stock, stock),
            "buy": _number(counter["buy"], 2),
            "sell": _number(counter["sell"], 2),
        }
        for stock, counter in sorted(assets.items(), key=lambda item: -(item[1]["buy"] + item[1]["sell"]))
    ]
    total_ordered = ordered["buy"] + ordered["sell"]
    return {
        "available": True,
        "reason": None,
        "source": source,
        "sample": {
            "days": days,
            "trades": len(trades),
            "agents": len(agents),
            # Reported per side: real runs' fills are not always balanced between buyers and sellers.
            "buy": _number(sum(row["buy"] for row in rows), 2),
            "sell": _number(sum(row["sell"] for row in rows), 2),
            "fill_rate": _ratio(filled["buy"] + filled["sell"], total_ordered) if total_ordered else None,
        },
        "daily": rows,
        "imbalance": imbalance,
        "disposition": disposition,
        "turnover": turnover_view,
        "assets": asset_rows,
        "active": summaries[:ACTIVE_AGENT_LIMIT],
    }


def analysis_polarization(run: dict[str, Any]) -> dict[str, Any]:
    _run_folder(run)
    return {
        "available": False,
        "reason": "帖子中的 belief 目前是自由文本，还没有看多、中性、看空的结构化立场，暂时无法计算信念极化。",
        "source": "unavailable",
    }
