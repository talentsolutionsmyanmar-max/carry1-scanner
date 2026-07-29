from __future__ import annotations

import math
import unittest

from daytrader.config import Config
from daytrader.engine import atr, build_ticket, ema, evaluate_snapshot, rsi
from daytrader.market import Candle, MarketSnapshot


def candles(
    count: int,
    interval_minutes: int,
    direction: float = 1.0,
    breakout: bool = False,
) -> tuple[Candle, ...]:
    out = []
    close = 100.0
    interval_ms = interval_minutes * 60 * 1000
    for i in range(count):
        # Alternating pullbacks keep RSI away from a permanently saturated 100/0.
        step = direction * (0.16 if i % 3 else -0.08)
        new_close = close + step
        opening = close
        high = max(opening, new_close) + 0.07
        low = min(opening, new_close) - 0.07
        volume = 1_000.0
        out.append(
            Candle(
                open_time=i * interval_ms,
                close_time=(i + 1) * interval_ms - 1,
                open=opening,
                high=high,
                low=low,
                close=new_close,
                volume=volume,
                quote_volume=volume * new_close,
                trades=100,
            )
        )
        close = new_close
    if breakout:
        previous_high = max(item.high for item in out[-21:-1])
        last = out[-1]
        new_close = previous_high + 0.20 if direction > 0 else min(
            item.low for item in out[-21:-1]
        ) - 0.20
        out[-1] = Candle(
            open_time=last.open_time,
            close_time=last.close_time,
            open=last.open,
            high=max(last.high, new_close + 0.03),
            low=min(last.low, new_close - 0.03),
            close=new_close,
            volume=last.volume * 2.0,
            quote_volume=last.quote_volume * 2.0,
            trades=last.trades * 2,
        )
    return tuple(out)


class IndicatorTests(unittest.TestCase):
    def test_ema_tracks_constant_series(self):
        self.assertEqual(ema([5.0] * 20, 9), [5.0] * 20)

    def test_rsi_balanced_series_near_fifty(self):
        values = [100.0 + (1 if i % 2 else 0) for i in range(40)]
        value = rsi(values, 14)[-1]
        self.assertIsNotNone(value)
        self.assertTrue(45.0 <= value <= 55.0)

    def test_atr_is_positive(self):
        value = atr(candles(40, 5), 14)[-1]
        self.assertIsNotNone(value)
        self.assertGreater(value, 0)


class SignalTests(unittest.TestCase):
    def snapshot(self, spread_bp: float = 2.0) -> MarketSnapshot:
        primary = candles(90, 5, direction=1.0, breakout=True)
        trend = candles(90, 15, direction=1.0)
        price = primary[-1].close
        half = spread_bp / 20_000.0
        return MarketSnapshot(
            symbol="TESTUSDT",
            primary=primary,
            trend=trend,
            bid=price * (1 - half),
            ask=price * (1 + half),
            mark=price,
            funding_bp=0.2,
            price_change_pct_24h=2.0,
            quote_volume_24h=1_000_000_000,
            captured_at="2026-01-01T00:00:00+00:00",
        )

    def test_long_breakout_produces_risk_sized_ticket(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=90.0
        )
        signal = evaluate_snapshot(self.snapshot(), config)
        self.assertEqual(signal.state, "LONG")
        self.assertIsNotNone(signal.ticket)
        self.assertLess(signal.ticket["stop"], signal.ticket["entry"])
        self.assertGreater(signal.ticket["tp2"], signal.ticket["entry"])
        self.assertLessEqual(
            signal.ticket["notional_usd"],
            config.starting_equity_usd * config.max_notional_fraction + 0.01,
        )
        gross_at_tp2 = (
            (signal.ticket["tp2"] - signal.ticket["entry"])
            * signal.ticket["quantity"]
        )
        net_at_tp2 = (
            gross_at_tp2 - signal.ticket["estimated_round_trip_cost_usd"]
        )
        self.assertAlmostEqual(
            net_at_tp2,
            2.0 * signal.ticket["effective_risk_usd"],
            delta=0.30,
        )

    def test_wide_spread_fails_closed(self):
        config = Config(signal_score=70, min_reward_cost_multiple=1.0)
        signal = evaluate_snapshot(self.snapshot(spread_bp=20.0), config)
        self.assertNotEqual(signal.state, "LONG")
        self.assertTrue(any("spread" in item for item in signal.blocked_by))

    def test_ticket_risk_is_bounded(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=90.0
        )
        signal = evaluate_snapshot(self.snapshot(), config)
        ticket = build_ticket(signal, config, equity_usd=5_000)
        self.assertLessEqual(ticket["notional_usd"], 2_500.01)
        self.assertTrue(math.isfinite(ticket["quantity"]))


if __name__ == "__main__":
    unittest.main()
