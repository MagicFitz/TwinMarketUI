"""Read-only Agent snapshot queries compatible with the Studio resource views."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from server.settings import Settings

from .read_only import ReadOnlyStore

BEHAVIORS = (
    ("处置效应", "bh_disposition_effect_category"),
    ("彩票偏好", "bh_lottery_preference_category"),
    ("投资集中度", "bh_underdiversification_category"),
    ("换手率", "bh_annual_turnover_category"),
    ("交易频率", "trade_count_category"),
    ("历史收益", "bh_total_return_category"),
)
LEVELS = ("低", "中", "高")
EXPOSURE_BINS = 5


class AgentService:
    """Query template and run-specific investor profiles without database writes."""

    def __init__(self, settings: Settings, store: ReadOnlyStore | None = None):
        self.settings = settings
        self.store = store or ReadOnlyStore(settings)
        self.data_dir = Path(settings.data_dir).resolve()

    def get_db(self, run_id: str, output_dir: Path | str | None = None) -> Path | None:
        """Return the template DB or the first user DB in a safely resolved run folder."""
        if run_id == "template":
            return self.store.local(self.data_dir / "sys_1000.db")
        folder = self.store.resolve_run_folder(run_id, output_dir)
        return self.store.database(folder, "user*.db")

    def members(
        self,
        run_id: str,
        q: str = "",
        page: int = 1,
        output_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        db = self.get_db(run_id, output_dir)
        search = q[:100]
        current_page = min(1_000_000, max(1, int(page)))
        where = (
            "created_at = (SELECT max(created_at) FROM Profiles) "
            "AND (user_id LIKE ? OR user_type LIKE ? OR location LIKE ?)"
        )
        params = (f"%{search}%",) * 3
        total = self.store.scalar(db, f"SELECT count(*) FROM Profiles WHERE {where}", params)
        items = self.store.rows(
            db,
            f"""SELECT user_id, user_type, location,
            bh_disposition_effect_category disposition, bh_lottery_preference_category lottery,
            current_cash, total_value, return_rate, created_at
            FROM Profiles WHERE {where} ORDER BY user_id LIMIT 20 OFFSET ?""",
            (*params, (current_page - 1) * 20),
        )
        return {"items": items, "total": total, "page": current_page}

    def member_detail(
        self,
        run_id: str,
        user_id: str,
        output_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        db = self.get_db(run_id, output_dir)
        result = self.store.rows(
            db,
            """SELECT user_id, user_type, location, gender,
            bh_disposition_effect_category disposition, bh_lottery_preference_category lottery,
            bh_underdiversification_category diversification,
            current_cash, total_value, return_rate, cur_positions, created_at
            FROM Profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        )
        if not result:
            raise KeyError("Unknown member")
        item = result[0]
        try:
            item["positions"] = json.loads(item.pop("cur_positions") or "{}")
        except ValueError:
            item["positions"] = {}
        strategies = self.store.rows(
            db,
            "SELECT strategy FROM Strategy WHERE CAST(user_id AS TEXT) = ? LIMIT 1",
            (user_id,),
        )
        item["strategy"] = strategies[0]["strategy"] if strategies else None
        # 提示词取最早快照：它是初始化时写入的画像，后续快照不保证保留这些字段。
        prompts = self.store.rows(
            db,
            """SELECT sys_prompt, prompt, self_description FROM Profiles
            WHERE user_id = ? ORDER BY created_at LIMIT 1""",
            (user_id,),
        )
        item.update(prompts[0])
        return item

    def distribution(self, run_id: str, output_dir: Path | str | None = None) -> dict[str, Any]:
        """Summarize a population from each Agent's earliest snapshot, its initial state."""
        db = self.get_db(run_id, output_dir)
        behavior_columns = ", ".join(column for _, column in BEHAVIORS)
        profiles = self.store.rows(
            db,
            f"""SELECT p.user_id, p.location, p.user_type, p.gender, p.fol_ind, p.ini_cash,
            p.current_cash, p.cur_positions, p.total_value, {behavior_columns}
            FROM Profiles p JOIN (
                SELECT user_id, min(created_at) first_at FROM Profiles GROUP BY user_id
            ) f ON p.user_id = f.user_id AND p.created_at = f.first_at""",
        )
        strategies = {
            str(row["user_id"]): row["strategy"]
            for row in self.store.rows(db, "SELECT user_id, strategy FROM Strategy")
        }
        stock_names = {
            row["stock_id"]: row["name"]
            for row in self.store.rows(db, "SELECT stock_id, name FROM StockProfile")
        }

        cash = Counter(p["ini_cash"] for p in profiles if p["ini_cash"] is not None)
        exposure = [0] * EXPOSURE_BINS
        holdings: Counter[str] = Counter()
        industries: Counter[str] = Counter()
        for profile in profiles:
            total, available = profile["total_value"], profile["current_cash"]
            if total and total > 0 and available is not None:
                share = min(1.0, max(0.0, 1 - available / total))
                exposure[min(EXPOSURE_BINS - 1, int(share * EXPOSURE_BINS))] += 1
            positions = _decode(profile["cur_positions"], dict)
            holdings.update(
                stock_names.get(code) or code
                for code, position in positions.items()
                if isinstance(position, dict) and position.get("shares")
            )
            industries.update(_decode(profile["fol_ind"], list))

        step = 100 // EXPOSURE_BINS
        return {
            "total": len(profiles),
            "location": _ranked(p["location"] for p in profiles),
            "user_type": _ranked(p["user_type"] for p in profiles),
            "gender": _ranked(p["gender"] for p in profiles),
            "strategy": _ranked(strategies.get(p["user_id"]) for p in profiles),
            "initial_cash": [{"name": amount, "value": cash[amount]} for amount in sorted(cash)],
            "exposure": [
                {"name": f"{index * step}–{(index + 1) * step}%", "value": value}
                for index, value in enumerate(exposure)
            ],
            "holdings": _ranked(holdings.elements()),
            "industries": _ranked(industries.elements()),
            "behaviors": [
                {"name": label, **{level: sum(p[column] == level for p in profiles) for level in LEVELS}}
                for label, column in BEHAVIORS
            ],
        }


def _decode(value: Any, expected: type) -> Any:
    """Decode a JSON column, treating malformed or unexpected values as empty."""
    try:
        decoded = json.loads(value or "null")
    except (TypeError, ValueError):
        return expected()
    return decoded if isinstance(decoded, expected) else expected()


def _ranked(values: Iterable[Any]) -> list[dict[str, Any]]:
    counts = Counter(value or "未知" for value in values)
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    return [{"name": name, "value": value} for name, value in ordered]


def get_db(settings: Settings, run_id: str, output_dir: Path | str | None = None) -> Path | None:
    return AgentService(settings).get_db(run_id, output_dir)


def members(
    settings: Settings,
    run_id: str,
    q: str = "",
    page: int = 1,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return the legacy ``GET /api/runs/{run_id}/agents`` response DTO."""
    return AgentService(settings).members(run_id, q, page, output_dir)


def member_detail(
    settings: Settings,
    run_id: str,
    user_id: str,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return the legacy Agent detail DTO, including decoded positions and strategy."""
    return AgentService(settings).member_detail(run_id, user_id, output_dir)


list_members = members
agent_detail = member_detail
