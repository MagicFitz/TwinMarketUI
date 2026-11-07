import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.services.bdi import agent_bdi, belief_stance
from server.settings import Settings

AGENT = "10001"


class BeliefStanceTests(unittest.TestCase):
    def test_reads_the_market_direction_sentence(self):
        cases = {
            "未来一个月我持谨慎乐观态度，短期或有调整压力": "bullish",
            "未来1个月市场大概率继续震荡下行": "bearish",
            "我对未来一个月的走势持谨慎悲观态度": "bearish",
            "未来一个月我并不看好大盘": "bearish",
            "我觉得未来一个月市场大概率维持震荡格局": "neutral",
        }
        for text, stance in cases.items():
            with self.subTest(text=text):
                self.assertEqual(belief_stance(text)["stance"], stance)

    def test_ignores_other_sentences_when_a_direction_sentence_exists(self):
        result = belief_stance("我过去一直很悲观。未来一个月市场震荡上行。")
        self.assertEqual(result["stance"], "bullish")
        self.assertEqual(result["evidence"], "未来一个月市场震荡上行")

    def test_empty_text_is_unknown(self):
        self.assertEqual(belief_stance(None)["stance"], "unknown")


class AgentBdiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.run_dir = root / "runs" / "run-1"
        (self.run_dir / "trading_records").mkdir(parents=True)
        (root / "data").mkdir()
        (root / "data" / "trading_days.csv").write_text(
            "cal_date,is_open,pretrade_date\n2023-06-15,open,\n2023-06-16,open,\n2023-06-19,open,\n",
            encoding="utf-8",
        )
        self.settings = Settings(
            project_root=root,
            data_dir=root / "data",
            runs_dir=root / "runs",
            registry_path=root / "registry.sqlite3",
            allowed_origins=("https://example.test",),
        )
        with sqlite3.connect(self.run_dir / "user_1.db") as db:
            db.executescript(
                """
                CREATE TABLE Profiles (user_id TEXT, current_cash REAL, total_value REAL, created_at TEXT);
                CREATE TABLE StockProfile (stock_id TEXT, name TEXT);
                INSERT INTO StockProfile VALUES ('TTEI', '科技与通信指数');
                """
            )
            db.executemany(
                "INSERT INTO Profiles VALUES (?, ?, ?, ?)",
                [
                    (AGENT, 60.0, 100.0, "2023-06-14 00:00:00"),
                    (AGENT, 50.0, 100.0, "2023-06-15 00:00:00"),
                    (AGENT, 50.0, 100.0, "2023-06-16 00:00:00"),
                    (AGENT, 70.0, 100.0, "2023-06-17 00:00:00"),
                    (AGENT, 70.0, 100.0, "2023-06-19 00:00:00"),
                ],
            )
        with sqlite3.connect(self.run_dir / "forum_1.db") as db:
            db.execute(
                "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id TEXT, content TEXT, belief TEXT, type TEXT, created_at TEXT)"
            )
            db.executemany(
                "INSERT INTO posts (user_id, content, belief, type, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (AGENT, "加仓科技", "未来一个月市场震荡上行。", "type2", "2023-06-15 00:00:00"),
                    (AGENT, "转发", "", "repost", "2023-06-16 00:00:00"),
                    (AGENT, "落袋", "未来一个月市场大概率继续下行。", "type3", "2023-06-17 00:00:00"),
                ],
            )
        (self.run_dir / "belief_1.csv").write_text(
            f"user_id,belief,attitude\n{AGENT},作为投资者我很谨慎,悲观的\n", encoding="utf-8"
        )
        decisions = {
            "2023-06-15": {"stock_decisions": {
                "TTEI": {"action": "buy", "target_position": 15.0, "cur_position": 5.0, "quantity": 100},
                "MEI": {"action": "hold", "target_position": 0, "cur_position": 0, "quantity": 0},
            }},
            "2023-06-16": {"stock_decisions": {
                "TTEI": {"action": "hold", "target_position": 0, "cur_position": 0, "quantity": 0},
            }},
            "2023-06-19": {"error": "用户没有被激活"},
        }
        for date, decision in decisions.items():
            (self.run_dir / "trading_records" / f"{date}.json").write_text(
                json.dumps({AGENT: decision}, ensure_ascii=False), encoding="utf-8"
            )
        (self.run_dir / "trading_records" / "2023-06-15_orders.json").write_text("{}", encoding="utf-8")
        context = self.run_dir / "llm_context" / "2023-06-16"
        context.mkdir(parents=True)
        (context / f"{AGENT}.json").write_text(
            json.dumps(
                {
                    "calls": [
                        {"step": "_desire_agent", "reply_id": "a"},
                        {"step": "_update_belief", "reply_id": "b"},
                    ],
                    "messages": {
                        "a": {"content": "```yaml\nqueries:\n- 科技板块有新政策吗？\n- 成交量放大了吗？\n```"},
                        "b": {"content": "未来一个月市场维持震荡格局。"},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.run_dir / "events.jsonl").write_text(
            "\n".join(
                json.dumps(event)
                for event in [
                    {"scope": "agent", "agent_id": AGENT, "simulation_date": "2023-06-15", "event": "stage_completed", "stage": "commit"},
                    {"scope": "agent", "agent_id": AGENT, "simulation_date": "2023-06-16", "event": "stage_failed", "stage": "interpretation", "error_code": "Timeout", "error_summary": "超时"},
                    {"scope": "agent", "agent_id": "other", "simulation_date": "2023-06-20", "event": "stage_started", "stage": "state"},
                ]
            ),
            encoding="utf-8",
        )
        self.days = {day["date"]: day for day in agent_bdi(self.settings, self.run_dir, AGENT)["days"]}

    def test_lists_only_this_agents_days_and_skips_order_files(self):
        self.assertEqual(sorted(self.days), ["2023-06-15", "2023-06-16", "2023-06-17", "2023-06-19"])

    def test_first_day_compares_with_the_initial_labeled_attitude(self):
        day = self.days["2023-06-15"]
        self.assertEqual(day["previous"], {"date": None, "stance": "bearish"})
        self.assertEqual(day["belief"]["stance"], "bullish")
        self.assertEqual(day["belief"]["source"], "post")
        self.assertTrue(day["belief"]["changed"])

    def test_desire_compares_target_position_with_the_held_position(self):
        desire = self.days["2023-06-15"]["desire"]
        self.assertEqual(desire["shift"], "increase")
        self.assertEqual(desire["held_position"], 40.0)
        self.assertEqual(desire["position_delta"], 10.0)
        self.assertEqual(desire["target_position"], 50.0)
        self.assertEqual(self.days["2023-06-16"]["desire"]["shift"], "maintain")
        self.assertEqual(self.days["2023-06-19"]["desire"]["shift"], "inactive")

    def test_desire_tracks_focus_changes_and_queries(self):
        desire = self.days["2023-06-16"]["desire"]
        self.assertEqual(desire["focus_removed"], ["MEI"])
        self.assertEqual(self.days["2023-06-17"]["desire"]["focus_removed"], [])
        self.assertEqual(self.days["2023-06-19"]["desire"]["focus_removed"], [])
        self.assertEqual(desire["queries"], ["科技板块有新政策吗？", "成交量放大了吗？"])

    def test_intention_reports_direction_orders_and_posting(self):
        first = self.days["2023-06-15"]["intention"]
        self.assertEqual(first["direction"], "buy")
        self.assertEqual([order["stock_id"] for order in first["orders"]], ["TTEI"])
        self.assertEqual(first["orders"][0]["name"], "科技与通信指数")
        self.assertTrue(first["posted"])
        second = self.days["2023-06-16"]["intention"]
        self.assertEqual(second["direction"], "hold")
        self.assertFalse(second["posted"])
        self.assertEqual(second["reposts"], 1)
        self.assertEqual(self.days["2023-06-19"]["intention"]["direction"], "none")

    def test_belief_falls_back_to_the_llm_reply_then_carries_forward(self):
        self.assertEqual(self.days["2023-06-16"]["belief"]["source"], "llm")
        self.assertEqual(self.days["2023-06-16"]["belief"]["stance"], "neutral")
        self.assertEqual(self.days["2023-06-19"]["belief"]["source"], "carried")
        self.assertEqual(self.days["2023-06-19"]["belief"]["stance"], "bearish")

    def test_non_trading_days_do_not_become_the_comparison_day(self):
        closed = self.days["2023-06-17"]
        self.assertFalse(closed["trading"])
        self.assertEqual(closed["desire"]["shift"], "unknown")
        self.assertEqual(closed["belief"]["stance"], "bearish")
        self.assertEqual(self.days["2023-06-19"]["previous"], {"date": "2023-06-16", "stance": "neutral"})
        self.assertEqual(self.days["2023-06-19"]["desire"]["held_position"], 30.0)

    def test_failed_day_exposes_the_error(self):
        self.assertEqual(self.days["2023-06-15"]["status"], "completed")
        day = self.days["2023-06-16"]
        self.assertEqual(day["status"], "failed")
        self.assertEqual(day["error"]["code"], "Timeout")

    def test_unknown_agent_and_invalid_id_are_rejected(self):
        with self.assertRaises(KeyError):
            agent_bdi(self.settings, self.run_dir, "99999")
        with self.assertRaises(ValueError):
            agent_bdi(self.settings, self.run_dir, "../other")


if __name__ == "__main__":
    unittest.main()
