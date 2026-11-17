import json
import math
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import networkx as nx
import numpy as np
from fastapi.testclient import TestClient

from server.analysis import analysis_network, analysis_propagation, analysis_stylized_facts, analysis_trading
from server.app.main import create_app
from server.process_manager import ProcessManager
from server.registry import RunRegistry
from server.settings import Settings
from simulation.stores.UserDB import save_graph_json

START = "2023-06-15"


def run_for(folder: Path, end: str = "2024-12-31") -> dict:
    return {"output_dir": str(folder), "config": {"user_count": 2, "start_date": START, "end_date": end}}


def write_forum(folder: Path, posts, references=(), reactions=()) -> None:
    with sqlite3.connect(folder / "forum_2.db") as db:
        db.executescript(
            """
            CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id TEXT, content TEXT, type TEXT, created_at TEXT);
            CREATE TABLE reactions (id INTEGER PRIMARY KEY, user_id TEXT, post_id INTEGER, type TEXT, created_at TEXT);
            CREATE TABLE post_references (id INTEGER PRIMARY KEY, reference_id INTEGER, repost_id INTEGER);
            """
        )
        db.executemany("INSERT INTO posts VALUES (?,?,?,?,?)", posts)
        db.executemany("INSERT INTO post_references(reference_id,repost_id) VALUES (?,?)", references)
        db.executemany("INSERT INTO reactions(user_id,post_id,type,created_at) VALUES (?,?,?,?)", reactions)


def write_market(folder: Path, rows) -> None:
    with sqlite3.connect(folder / "user_2.db") as db:
        db.executescript(
            """
            CREATE TABLE StockProfile (stock_id TEXT, weight REAL);
            CREATE TABLE StockData (stock_id TEXT, date TEXT, close_price REAL, vol INTEGER);
            """
        )
        db.executemany("INSERT INTO StockProfile VALUES (?,?)", [("A", 1.0), ("B", 3.0)])
        db.executemany("INSERT INTO StockData VALUES (?,?,?,?)", rows)


def trading_days(count: int, first_ordinal: int) -> list[str]:
    first = date(2023, 1, 1) + timedelta(days=first_ordinal)
    return [(first + timedelta(days=day)).isoformat() for day in range(count)]


class PropagationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def write_cascade(self):
        write_forum(
            self.folder,
            [
                (1, "root", "原帖", "type1", "2023-06-15 00:00:00"),
                (2, "a", "转发", "repost", "2023-06-16 00:00:00"),
                (3, "b", "再转发", "repost", "2023-06-16 00:00:00"),
                (4, "c", "转发", "repost", "2023-06-16 00:00:00"),
                (5, "lonely", "没人转发", "type2", "2023-06-16 00:00:00"),
            ],
            references=[(1, 2), (2, 3), (1, 4)],
            reactions=[("a", 1, "like", "2023-06-16"), ("b", 1, "unlike", "2023-06-16")],
        )

    def test_missing_forum_and_empty_forum_explain_themselves(self):
        self.assertFalse(analysis_propagation(run_for(self.folder))["available"])
        write_forum(self.folder, [])
        result = analysis_propagation(run_for(self.folder))
        self.assertFalse(result["available"])
        self.assertIn("没有产生论坛帖子", result["reason"])

    def test_cascades_follow_reposts_of_reposts(self):
        self.write_cascade()
        result = analysis_propagation(run_for(self.folder))
        self.assertEqual(result["sample"]["reposts"], 3)
        self.assertEqual(result["sample"]["max_depth"], 2)
        self.assertEqual(result["cascades"][0]["post_id"], 1)
        self.assertEqual(result["cascades"][0]["size"], 3)
        self.assertEqual(result["depth_distribution"], [{"depth": 2, "count": 1}])
        leader = result["leaders"][0]
        self.assertEqual(leader["agent_id"], "root")
        self.assertEqual((leader["reposted"], leader["reach"]), (2, 3))
        self.assertEqual((leader["likes"], leader["unlikes"]), (1, 1))

    def test_reposts_do_not_loop_forever_on_a_cycle(self):
        write_forum(
            self.folder,
            [
                (1, "root", "原帖", "type1", "2023-06-15"),
                (2, "a", "转发", "repost", "2023-06-16"),
                (3, "b", "转发", "repost", "2023-06-16"),
            ],
            references=[(1, 2), (2, 3), (3, 2)],
        )
        self.assertEqual(analysis_propagation(run_for(self.folder))["cascades"][0]["size"], 2)

    def test_pickle_is_never_loaded_and_the_conversion_is_explained(self):
        self.write_cascade()
        graphs = self.folder / "graphs"
        graphs.mkdir()
        # Not a valid pickle: reading it would raise, so success proves it is untouched.
        (graphs / f"user_graph_2_{START}.pkl").write_bytes(b"not a pickle")
        result = analysis_propagation(run_for(self.folder))
        self.assertIsNone(result["network"])
        self.assertIn("export_graphs_json.py", result["network_reason"])

    def test_network_reads_engine_json_and_annotates_leaders(self):
        self.write_cascade()
        graph = nx.Graph()
        graph.add_edge("root", "a", weight=np.float64(0.5))
        graph.add_edge("root", "b", weight=np.float64(0.25))
        graph.add_node("isolated")
        self.assertTrue(save_graph_json(graph, f"user_graph_2_{START}", str(self.folder / "graphs")))

        result = analysis_propagation(run_for(self.folder))
        network = result["network"]
        self.assertEqual((network["date"], network["node_count"], network["edge_count"]), (START, 4, 2))
        self.assertEqual(network["nodes"][0]["id"], "root")
        self.assertEqual(result["leaders"][0]["degree"], 2)
        self.assertEqual(result["leaders"][0]["weighted_degree"], 0.75)
        json.dumps(result, allow_nan=False)
        with self.assertRaises(ValueError):
            analysis_propagation(run_for(self.folder), "2023-01-01")


class StylizedFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_history_never_counts_as_simulated_output(self):
        history = [
            (stock, day, 10 + index * 0.01, None)
            for stock in ("A", "B")
            for index, day in enumerate(trading_days(25, 0))
        ]
        simulated = [
            (stock, day, price, volume)
            for stock in ("A", "B")
            for day, price, volume in (("2023-06-15", 11, 100), ("2023-06-16", 11.1, 200))
        ]
        write_market(self.folder, history + simulated)
        result = analysis_stylized_facts(run_for(self.folder))
        self.assertEqual(result["simulated"]["days"], 2)
        self.assertEqual(result["history"]["days"], 24)
        self.assertFalse(result["simulated"]["distribution"]["available"])
        self.assertIn("只有 2 个", result["simulated"]["distribution"]["reason"])
        self.assertTrue(result["history"]["distribution"]["available"])
        self.assertIn("没有成交量", result["history"]["volume_return"]["reason"])

    def test_returns_after_end_date_are_ignored(self):
        write_market(self.folder, [("A", day, 10.0, 1) for day in ("2023-06-14", START, "2023-06-16")])
        result = analysis_stylized_facts(run_for(self.folder, end=START))
        self.assertEqual(result["simulated"]["days"], 1)

    def test_zero_volume_days_are_skipped_rather_than_infinite(self):
        rows = [("A", "2023-06-14", 10.0, None)]
        for index, day in enumerate(trading_days(80, 170)):
            volume = 0 if index % 5 == 0 else 100 + index
            rows.append(("A", day, 10.0 + (index % 3) * 0.1, volume))
        write_market(self.folder, rows)
        result = analysis_stylized_facts(run_for(self.folder))
        volume_return = result["simulated"]["volume_return"]
        self.assertGreater(volume_return["zero_volume_skipped"], 0)
        self.assertTrue(volume_return["available"])
        json.dumps(result, allow_nan=False)

    def test_constant_prices_yield_no_undefined_statistics(self):
        write_market(self.folder, [("A", day, 10.0, 100) for day in trading_days(120, 170)])
        result = analysis_stylized_facts(run_for(self.folder))
        simulated = result["simulated"]
        self.assertFalse(simulated["distribution"]["available"])
        self.assertFalse(simulated["clustering"]["available"])
        json.dumps(result, allow_nan=False)

    def test_statistics_match_an_independent_calculation_and_recover_garch(self):
        rng = np.random.default_rng(7)
        count = 3000
        variance, price = 1e-4, 10.0
        alpha, beta, omega = 0.2, 0.7, 1e-5
        returns = []
        rows = [("A", "2023-06-15", price, 100)]
        for day in trading_days(count, 170):
            value = math.sqrt(variance) * rng.standard_normal()
            returns.append(value)
            price *= math.exp(value)
            variance = omega + alpha * value**2 + beta * variance
            rows.append(("A", day, price, 100))
        write_market(self.folder, rows)

        result = analysis_stylized_facts(run_for(self.folder, end="2040-01-01"))["simulated"]
        expected = np.array(returns)
        z = (expected - expected.mean()) / expected.std()
        self.assertAlmostEqual(result["distribution"]["excess_kurtosis"], np.mean(z**4) - 3, places=3)
        # GARCH(0.2, 0.7) has theoretical excess kurtosis of about 2.2.
        self.assertGreater(result["distribution"]["excess_kurtosis"], 1)

        clustering = result["clustering"]
        self.assertTrue(clustering["available"], clustering["reason"])
        self.assertAlmostEqual(clustering["alpha"], alpha, delta=0.05)
        self.assertAlmostEqual(clustering["beta"], beta, delta=0.07)
        self.assertEqual(len(clustering["series"]), count)
        self.assertGreater(clustering["acf_abs"][0]["value"], 0)
        # The curve integrates to about 1: a real density, not an unscaled histogram.
        curve = np.array(result["distribution"]["curve"])
        self.assertAlmostEqual(float(np.trapezoid(curve[:, 1], curve[:, 0])), 1.0, delta=0.02)
        self.assertTrue(all(x < 0 and y < 0 for x, y in result["leverage"]["points"]))


class NetworkTimelineTests(unittest.TestCase):
    def test_positions_stay_fixed_across_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            graphs = str(folder / "graphs")
            first, second = nx.Graph(), nx.Graph()
            first.add_edge("a", "b", weight=1.0)
            second.add_edge("b", "c", weight=1.0)
            save_graph_json(first, f"user_graph_2_{START}", graphs)
            save_graph_json(second, "user_graph_2_2023-06-16", graphs)

            early = analysis_network(run_for(folder), START)
            late = analysis_network(run_for(folder), "2023-06-16")
            self.assertEqual(early["edge_count"], 1)
            self.assertEqual(early["dates"], [START, "2023-06-16"])
            positions = {node["id"]: node["position"] for node in early["nodes"]}
            self.assertEqual({node["id"]: node["position"] for node in late["nodes"]}["b"], positions["b"])
            with self.assertRaises(ValueError):
                analysis_network(run_for(folder), "2020-01-01")


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def write_user_db(self, trades, profiles):
        with sqlite3.connect(self.folder / "user_2.db") as db:
            db.executescript(
                """
                CREATE TABLE StockProfile (stock_id TEXT, weight REAL, name TEXT);
                CREATE TABLE StockData (stock_id TEXT, date TEXT, close_price REAL, vol INTEGER);
                CREATE TABLE TradingDetails (user_id TEXT, date_time TEXT, stock_id TEXT, trading_direction TEXT,
                    price REAL, volume INTEGER, valid INTEGER);
                CREATE TABLE Profiles (user_id TEXT, created_at TEXT, stock_returns TEXT, total_value REAL,
                    bh_disposition_effect_category TEXT);
                """
            )
            db.executemany("INSERT INTO StockProfile VALUES (?,?,?)", [("A", 1.0, "甲"), ("B", 1.0, "乙")])
            db.executemany("INSERT INTO StockData VALUES (?,?,?,?)", [
                ("A", "2023-06-14", 10.0, 1), ("A", START, 11.0, 1), ("B", "2023-06-14", 10.0, 1), ("B", START, 9.0, 1),
            ])
            db.executemany("INSERT INTO TradingDetails VALUES (?,?,?,?,?,?,?)", trades)
            db.executemany("INSERT INTO Profiles VALUES (?,?,?,?,?)", profiles)

    def test_seed_trades_are_excluded_and_nothing_to_show_is_explained(self):
        self.write_user_db([("x", "2023-01-03", "A", "buy", 10.0, 100, 1)], [])
        result = analysis_trading(run_for(self.folder))
        self.assertFalse(result["available"])
        self.assertIn("没有成交", result["reason"])

    def test_trades_orders_and_disposition_are_computed_from_the_window(self):
        holdings = json.dumps({"A": {"profit": 10.0}, "B": {"profit": -10.0}})
        profiles = [
            (agent, f"{day} 00:00:00", holdings, value, "高")
            for agent in ("x", "y")
            for day, value in (("2023-06-14", 1000.0), (START, 1100.0))
        ]
        self.write_user_db(
            [
                ("x", "2023-01-03", "A", "buy", 10.0, 999, 1),  # seed history
                ("x", START, "A", "sell", 11.0, 100, 1),
                ("y", START, "A", "buy", 11.0, 100, 1),
                ("y", START, "B", "buy", 9.0, 50, 0),  # rejected order
            ],
            profiles,
        )
        records = self.folder / "trading_records"
        records.mkdir()
        (records / f"{START}_orders.json").write_text(json.dumps({
            "A": {"buy": [{"quantity": 300}], "sell": [{"quantity": 100}]},
        }))

        result = analysis_trading(run_for(self.folder))
        self.assertEqual(result["sample"]["trades"], 2)
        self.assertEqual(result["sample"]["agents"], 2)
        self.assertEqual(result["sample"]["buy"], 1100.0)
        self.assertEqual(result["sample"]["sell"], 1100.0)
        self.assertEqual(result["sample"]["fill_rate"], 0.5)
        self.assertEqual(result["daily"][0]["buy"], 1100.0)
        self.assertEqual(result["daily"][0]["traders"], 2)
        # Equal weights on +ln(1.1) and ln(0.9).
        self.assertAlmostEqual(result["daily"][0]["market_return"], (math.log(1.1) + math.log(0.9)) / 2, places=5)
        self.assertEqual(result["assets"][0]["name"], "甲")
        # Too few sell decisions and Agents: a reason, not a statistic.
        self.assertFalse(result["disposition"]["available"])
        self.assertFalse(result["imbalance"]["available"])
        self.assertFalse(result["turnover"]["available"])
        json.dumps(result, allow_nan=False)

    def test_disposition_matches_a_hand_calculation(self):
        days = trading_days(40, 170)
        holdings = json.dumps({"A": {"profit": 5.0}, "B": {"profit": -5.0}, "C": {"profit": 0}})
        profiles = [("x", "2023-06-14 00:00:00", holdings, 1000.0, "高")]
        profiles += [("x", f"{day} 00:00:00", holdings, 1000.0, "高") for day in days]
        # Sells the gain on 10 days and the loss on 5: each sell day observes one gain and one loss.
        trades = [("x", day, "A", "sell", 10.0, 100, 1) for day in days[:10]]
        trades += [("x", day, "B", "sell", 10.0, 100, 1) for day in days[10:15]]
        trades += [("x", day, "B", "sell", 10.0, 100, 1) for day in days[15:30]]
        self.write_user_db(trades, profiles)
        result = analysis_trading(run_for(self.folder))["disposition"]
        self.assertTrue(result["available"], result["reason"])
        self.assertEqual(result["decisions"], 60)
        self.assertEqual(result["pgr"], round(10 / 30, 4))
        self.assertEqual(result["plr"], round(20 / 30, 4))
        self.assertEqual(result["groups"][0]["category"], "高")


class AnalysisApiTests(unittest.TestCase):
    def test_routes_read_registered_runs_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            runs = root / "runs"
            runs.mkdir()
            settings = Settings(
                project_root=root,
                data_dir=root / "data",
                runs_dir=runs,
                registry_path=root / "server" / "data" / "runs.sqlite3",
            )
            registry = RunRegistry(settings.registry_path)
            output = runs / "r1"
            output.mkdir()
            registry.create("r1", "r1", "", run_for(output)["config"], output)
            write_market(output, [("A", START, 10.0, 1)])
            manager = ProcessManager(registry, settings)
            client = TestClient(create_app(settings, registry=registry, manager=manager))

            self.assertEqual(client.get("/api/runs/missing/analysis/propagation").status_code, 404)
            self.assertFalse(client.get("/api/runs/r1/analysis/propagation").json()["available"])
            self.assertTrue(client.get("/api/runs/r1/analysis/stylized-facts").json()["available"])
            self.assertFalse(client.get("/api/runs/r1/analysis/polarization").json()["available"])
            self.assertEqual(client.get("/api/runs/r1/analysis/network", params={"date": START}).status_code, 422)
            self.assertEqual(client.get("/api/runs/missing/analysis/trading").status_code, 404)


if __name__ == "__main__":
    unittest.main()
