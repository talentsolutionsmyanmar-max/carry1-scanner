from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from daytrader.config import Config
from daytrader.engine import (
    Signal,
    adx,
    atr,
    build_ticket,
    confirmed_swings,
    ema,
    evaluate_snapshot,
    find_fair_value_gaps,
    macd,
    rsi,
    stoch_rsi,
)
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

    def test_swing_is_not_visible_until_right_bars_close(self):
        rows = tuple(
            Candle(i, i, 1.0, high, 0.5, 1.0, 1.0, 1.0, 1)
            for i, high in enumerate((1.0, 2.0, 5.0, 2.0, 1.0))
        )
        self.assertEqual(confirmed_swings(rows[:4])["highs"], [])
        swings = confirmed_swings(rows)["highs"]
        self.assertEqual(len(swings), 1)
        self.assertEqual(swings[0]["index"], 2)
        self.assertEqual(swings[0]["confirmation_index"], 4)

    def test_fvg_reports_current_midpoint_retest_without_full_fill(self):
        rows = (
            Candle(0, 0, 99.5, 100.0, 99.0, 99.8, 1, 1, 1),
            Candle(1, 1, 99.8, 100.3, 99.7, 100.1, 1, 1, 1),
            Candle(2, 2, 101.1, 101.5, 101.0, 101.4, 1, 1, 1),
            Candle(3, 3, 101.4, 101.7, 100.7, 101.2, 1, 1, 1),
            Candle(4, 4, 101.2, 101.4, 100.4, 100.9, 1, 1, 1),
        )
        gap = next(item for item in find_fair_value_gaps(rows, "LONG", 2, 1.0) if item["index"] == 2)
        self.assertEqual(gap["low"], 100.0)
        self.assertEqual(gap["high"], 101.0)
        self.assertEqual(gap["retest_index"], 4)


class SignalTests(unittest.TestCase):
    def snapshot(self, spread_bp: float = 2.0, **derivatives) -> MarketSnapshot:
        primary = candles(90, 5, direction=1.0, breakout=True)
        trend = candles(90, 15, direction=1.0)
        higher = candles(220, 60, direction=1.0)
        price = primary[-1].close
        half = spread_bp / 20_000.0
        context = {
            "funding_bp": 0.2,
            "open_interest_usd": None,
            "open_interest_change_15m_pct": None,
            "open_interest_change_1h_pct": None,
            "taker_buy_sell_ratio_15m": None,
            "long_short_account_ratio": None,
        }
        context.update(derivatives)
        return MarketSnapshot(
            symbol="TESTUSDT",
            primary=primary,
            trend=trend,
            higher=higher,
            bid=price * (1 - half),
            ask=price * (1 + half),
            mark=price,
            price_change_pct_24h=2.0,
            quote_volume_24h=1_000_000_000,
            captured_at="2026-01-01T00:00:00+00:00",
            **context,
        )

    def liquidity_snapshot(self) -> MarketSnapshot:
        primary = list(candles(80, 5, direction=1.0))
        base = primary[-1].close
        interval_ms = 5 * 60 * 1000

        def append(opening, high, low, close, volume=2_000.0):
            index = len(primary)
            primary.append(
                Candle(
                    open_time=index * interval_ms,
                    close_time=(index + 1) * interval_ms - 1,
                    open=opening,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    quote_volume=volume * close,
                    trades=200,
                )
            )

        append(base, base + 0.05, base - 0.40, base - 0.10)
        append(base - 0.10, base + 0.25, base - 0.12, base + 0.15)
        append(base + 0.15, base + 0.55, base + 0.10, base + 0.35)
        append(base + 0.35, base + 0.40, base + 0.05, base + 0.20)
        append(base + 0.20, base + 0.30, base, base + 0.10)
        append(base + 0.10, base + 0.15, base - 0.55, base - 0.22)
        append(base - 0.22, base + 0.75, base - 0.25, base + 0.70, 4_000.0)
        append(base + 0.70, base + 0.95, base + 0.30, base + 0.85, 3_000.0)
        append(base + 0.85, base + 0.90, base + 0.20, base + 0.65, 2_500.0)
        mark = primary[-1].close
        half = 2.0 / 20_000.0
        trend = tuple(
            Candle(
                bar.open_time,
                bar.close_time,
                bar.open + 10.0,
                bar.high + 10.0,
                bar.low + 10.0,
                bar.close + 10.0,
                bar.volume,
                bar.quote_volume,
                bar.trades,
            )
            for bar in candles(120, 15, direction=1.0)
        )
        return MarketSnapshot(
            symbol="LIQUSDT",
            primary=tuple(primary),
            trend=trend,
            higher=candles(220, 60, direction=1.0),
            bid=mark * (1 - half),
            ask=mark * (1 + half),
            mark=mark,
            funding_bp=0.0,
            price_change_pct_24h=1.0,
            quote_volume_24h=1_000_000_000,
            captured_at="2026-01-01T01:00:00+00:00",
        )

    def test_long_breakout_produces_risk_sized_ticket(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=100.0,
            min_volume_ratio=1.0, min_adx=10.0,
        )
        signal = evaluate_snapshot(
            self.snapshot(), config, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(signal.state, "LIVE")
        self.assertEqual(signal.side, "LONG")
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
        signal = evaluate_snapshot(
            self.snapshot(spread_bp=20.0), config,
            now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        self.assertNotEqual(signal.state, "LIVE")
        self.assertTrue(any("spread" in item for item in signal.blocked_by))

    def test_supportive_derivatives_context_is_reported_separately(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=100.0,
            min_volume_ratio=1.0, min_adx=10.0,
        )
        signal = evaluate_snapshot(
            self.snapshot(
                open_interest_usd=2_000_000_000,
                open_interest_change_15m_pct=0.8,
                open_interest_change_1h_pct=1.6,
                taker_buy_sell_ratio_15m=1.25,
                long_short_account_ratio=1.0,
            ),
            config,
            now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(signal.state, "LIVE")
        self.assertEqual(signal.derivatives_verdict, "SUPPORTS")
        self.assertTrue(signal.derivatives_available)
        self.assertEqual(signal.score, signal.ticket["score"])

    def test_two_factor_derivatives_conflict_vetoes_actionable_ticket(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=100.0,
            min_volume_ratio=1.0, min_adx=10.0,
        )
        signal = evaluate_snapshot(
            self.snapshot(
                funding_bp=5.0,
                open_interest_usd=2_000_000_000,
                open_interest_change_15m_pct=0.8,
                open_interest_change_1h_pct=1.6,
                taker_buy_sell_ratio_15m=0.70,
                long_short_account_ratio=2.0,
            ),
            config,
            now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(signal.derivatives_verdict, "CONFLICTS")
        self.assertEqual(signal.state, "WATCH")
        self.assertIsNone(signal.ticket)
        self.assertTrue(any("derivatives context conflicts" in item for item in signal.blocked_by))

    def test_ticket_risk_is_bounded(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=100.0,
            min_volume_ratio=1.0, min_adx=10.0,
        )
        signal = evaluate_snapshot(
            self.snapshot(), config, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        )
        ticket = build_ticket(signal, config, equity_usd=5_000)
        self.assertLessEqual(ticket["notional_usd"], 2_500.01)
        self.assertTrue(math.isfinite(ticket["quantity"]))

    def test_armed_ticket_is_visible_outside_kill_zone(self):
        config = Config(
            signal_score=70, min_reward_cost_multiple=1.0, long_rsi_max=100.0,
            min_volume_ratio=1.0, min_adx=10.0,
        )
        signal = evaluate_snapshot(
            self.snapshot(), config, now=datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
        )
        self.assertEqual(signal.state, "ARMED")
        self.assertIsNotNone(signal.ticket)
        self.assertIn("outside", " ".join(signal.blocked_by))

    def test_extended_indicators_are_finite(self):
        bars = candles(90, 5, direction=1.0)
        closes = [bar.close for bar in bars]
        self.assertTrue(all(math.isfinite(value) for value in macd(closes)))
        self.assertTrue(math.isfinite(stoch_rsi(closes)))
        self.assertTrue(math.isfinite(adx(bars)))

    def test_liquidity_sweep_mss_fvg_retest_produces_playbook_b_ticket(self):
        config = Config(
            min_reward_cost_multiple=1.0,
            max_friction_stop_pct=100.0,
            liquidity_signal_score=85,
        )
        signal = evaluate_snapshot(
            self.liquidity_snapshot(),
            config,
            now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(signal.state, "LIVE")
        self.assertEqual(signal.side, "LONG")
        self.assertEqual(signal.playbook, "LIQUIDITY_MSS_FVG")
        self.assertIsNotNone(signal.fvg_mid)
        self.assertIsNotNone(signal.mss_level)
        self.assertIsNotNone(signal.ticket)
        self.assertLess(signal.ticket["stop"], signal.ticket["entry"])
        self.assertGreater(signal.ticket["tp3"], signal.ticket["tp2"])

    def test_opposite_actionable_playbooks_are_vetoed(self):
        momentum = Signal(
            symbol="TESTUSDT", state="LIVE", side="LONG", score=91,
            action="enter", playbook="MOMENTUM_BREAKOUT",
        )
        liquidity = Signal(
            symbol="TESTUSDT", state="ARMED", side="SHORT", score=95,
            action="arm", playbook="LIQUIDITY_MSS_FVG",
            playbook_label="PLAYBOOK B · LIQUIDITY MSS/FVG",
        )
        with patch("daytrader.engine._evaluate_momentum_snapshot", return_value=momentum), patch(
            "daytrader.engine._evaluate_liquidity_snapshot", return_value=liquidity
        ):
            signal = evaluate_snapshot(self.snapshot(), Config())
        self.assertEqual(signal.state, "WATCH")
        self.assertEqual(signal.playbook_confirmation, "CONFLICT")
        self.assertIsNone(signal.ticket)
        self.assertTrue(any("arbiter vetoed" in item for item in signal.blocked_by))


if __name__ == "__main__":
    unittest.main()
