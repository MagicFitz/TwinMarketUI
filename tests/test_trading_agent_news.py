"""News retrieval inside the trading agent when the model asks for nothing."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

# Use the empty retrieval index: no legacy FAISS file or embedding client in unit tests.
os.environ.setdefault("TWINMARKET_MARKET_BUNDLE_ID", "test-bundle")

from trader.trading_agent import PersonalizedStockTrader  # noqa: E402

DATE = pd.Timestamp("2025-01-13")


class DesireAgentTests(unittest.TestCase):
    def trader(self, responses):
        agent = Mock()
        agent.get_response.side_effect = [{"response": text} for text in responses]
        return SimpleNamespace(base_agent=agent, stock_codes=[], potential_stock_list=[],
                               _get_stock_details=lambda *args, **kwargs: "", user_profile={"user_type": "散户"},
                               conversation_history=[], InformationDataBase=Mock(),
                               system_context={"content": "人设"})

    def test_no_queries_returns_an_empty_result_and_query_list(self):
        trader = self.trader(["<output>暂无需要</output>", "queries: []"])
        self.assertEqual(PersonalizedStockTrader._desire_agent(trader, DATE), ("", []))
        trader.InformationDataBase.search_news_batch.assert_not_called()

    def test_environment_info_survives_a_model_that_asks_for_nothing(self):
        trader = self.trader(["初步看法"])
        trader._desire_agent = lambda current_date: ("", [])
        trader.conversation_history = [{"role": "assistant", "content": "回答"}]
        message, used = PersonalizedStockTrader._get_environment_info(trader, DATE)
        self.assertEqual((message, used), ("", True))
        self.assertIn("初步看法", trader.conversation_history[-1]["content"])


if __name__ == "__main__":
    unittest.main()
