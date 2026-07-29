from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from daytrader.config import Config
from daytrader.paper import PaperBroker


class PaperBrokerTests(unittest.TestCase):
    def ticket(self) -> dict:
        return {
            "mode": "PAPER_ONLY",
            "signal_id": "BTCUSDT:1:LONG",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "opened_at": "2026-01-01T00:00:00+00:00",
            "entry": 100.0,
            "stop": 99.0,
            "tp1": 101.0,
            "tp2": 102.0,
            "tp3": 103.0,
            "time_exit": "2026-01-01T03:00:00+00:00",
            "quantity": 10.0,
            "notional_usd": 1000.0,
            "risk_budget_usd": 25.0,
            "effective_risk_usd": 12.0,
            "estimated_round_trip_cost_usd": 2.0,
            "friction_bp": 20.0,
            "score": 80,
            "disclaimer": "test",
        }

    def test_opens_deduplicates_and_closes_target(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = PaperBroker(Path(directory) / "state.json", Config())
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            opened, _ = broker.open_from_ticket(self.ticket(), now)
            self.assertTrue(opened)
            opened_again, reason = broker.open_from_ticket(self.ticket(), now)
            self.assertFalse(opened_again)
            self.assertEqual(reason, "signal already processed")
            events = broker.update_prices(
                {"BTCUSDT": 102.5},
                datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(events[0]["reason"], "TP2")
            state = broker.snapshot(datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(len(state["positions"]), 0)
            self.assertEqual(state["history"][0]["net_pnl_usd"], 18.0)

    def test_daily_trade_limit_blocks_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(max_trades_per_day=0)
            broker = PaperBroker(Path(directory) / "state.json", config)
            opened, reason = broker.open_from_ticket(
                self.ticket(), datetime(2026, 1, 1, tzinfo=timezone.utc)
            )
            self.assertFalse(opened)
            self.assertIn("daily trade limit", reason)


if __name__ == "__main__":
    unittest.main()
