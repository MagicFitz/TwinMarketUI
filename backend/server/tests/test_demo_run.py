import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path

from scripts.generate_demo_run import generate
from server.analysis import analysis_propagation, analysis_stylized_facts
from server.registry import ConflictError, RunRegistry
from server.results import paged_json_records, results_summary
from server.runtime_service import runtime_snapshot
from server.settings import Settings

BACKEND = Path(__file__).resolve().parents[2]
START, END = "2023-06-15", "2023-08-15"
USERS = 12


class DemoRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name).resolve()
        cls.settings = Settings(
            project_root=BACKEND,
            data_dir=BACKEND / "data",
            runs_dir=root / "runs",
            registry_path=root / "runs.sqlite3",
        )
        cls.registry = RunRegistry(cls.settings.registry_path)
        cls.result = generate(cls.settings, cls.registry, run_id="demo", user_count=USERS, start=START, end=END)
        cls.demo = cls.registry.get("demo")
        cls.folder = Path(cls.demo["output_dir"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def user_rows(self, query, *args):
        with closing(sqlite3.connect(self.folder / f"user_{USERS}.db")) as conn:
            return conn.execute(query, args).fetchall()

    def test_run_is_labeled_synthetic_and_completed(self):
        self.assertEqual(self.demo["status"], "completed")
        self.assertIs(self.demo["config"]["synthetic_demo"], True)
        self.assertIn("合成", self.demo["name"])
        manifest = json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(manifest["synthetic"]["model_called"], False)

    def test_ledger_conserves_cash_and_shares(self):
        days = [row[0] for row in self.user_rows("SELECT DISTINCT created_at FROM Profiles ORDER BY created_at")]
        self.assertEqual(len(days), self.result["days"] + 1)
        totals = []
        for day in (days[0], days[-1]):
            rows = self.user_rows("SELECT current_cash,cur_positions FROM Profiles WHERE created_at=?", day)
            self.assertEqual(len(rows), USERS)
            shares = Counter()
            for _cash, positions in rows:
                for code, item in json.loads(positions).items():
                    self.assertGreater(item["shares"], 0)
                    shares[code] += item["shares"]
            totals.append((sum(row[0] for row in rows), shares))
        self.assertAlmostEqual(totals[0][0], totals[1][0], delta=1)
        self.assertEqual(totals[0][1], totals[1][1])
        self.assertTrue(all(row[0] >= 0 for row in self.user_rows("SELECT current_cash FROM Profiles")))

    def test_volume_matches_fills_on_both_sides(self):
        fills = self.user_rows(
            "SELECT stock_id,date_time,trading_direction,sum(volume) FROM TradingDetails "
            "WHERE date_time>=? GROUP BY 1,2,3", START,
        )
        by_side = {(stock, day, side): volume for stock, day, side, volume in fills}
        rows = self.user_rows("SELECT stock_id,date,vol FROM StockData WHERE date>=?", START)
        self.assertTrue(any(row[2] for row in rows))
        for stock, day, volume in rows:
            self.assertEqual(by_side.get((stock, day, "buy"), 0), volume)
            self.assertEqual(by_side.get((stock, day, "sell"), 0), volume)

    def test_every_view_reads_the_generated_files(self):
        summary = results_summary(self.demo)["items"]
        self.assertEqual(len(summary), self.result["days"] * 10)
        self.assertGreater(paged_json_records(self.folder, "trades", 1, 50)["total"], 0)
        self.assertGreater(paged_json_records(self.folder, "orders", 1, 50)["total"], 0)

        propagation = analysis_propagation(self.demo)
        self.assertTrue(propagation["available"])
        self.assertGreater(propagation["sample"]["reposts"], 0)
        self.assertEqual(propagation["network"]["node_count"], USERS)

        facts = analysis_stylized_facts(self.demo)["simulated"]
        self.assertEqual(facts["days"], self.result["days"])
        self.assertTrue(facts["distribution"]["available"], facts["distribution"]["reason"])
        self.assertTrue(facts["volume_return"]["available"], facts["volume_return"]["reason"])

        snapshot = runtime_snapshot(self.demo)
        self.assertEqual(len(snapshot["agents"]), USERS)
        self.assertEqual(snapshot["agent_counts"]["completed"], USERS)
        self.assertGreater(snapshot["today_summary"]["orders"], 0)
        self.assertTrue(snapshot["diagnostics"])

    def test_refuses_to_overwrite_without_replace_or_a_real_run(self):
        with self.assertRaises(ConflictError):
            generate(self.settings, self.registry, run_id="demo", user_count=USERS, start=START, end=END)
        output = self.settings.runs_dir / "real"
        output.mkdir()
        self.registry.create("real", "real", "", {"start_date": START, "end_date": END}, output)
        with self.assertRaises(ConflictError):
            generate(self.settings, self.registry, run_id="real", user_count=USERS, start=START, end=END, replace=True)
        self.assertTrue(output.is_dir())


if __name__ == "__main__":
    unittest.main()
