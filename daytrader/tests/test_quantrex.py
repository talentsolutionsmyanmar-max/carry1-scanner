import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from daytrader.market import Candle, MarketSnapshot
from daytrader.quantrex.config import QuantrexConfig
from daytrader.quantrex.broker import PaperBroker
from daytrader.quantrex.contracts import Book, CostEstimate, Side
from daytrader.quantrex.data import DataContractError, validate_snapshot
from daytrader.quantrex.replay import ReplayLedger
from daytrader.quantrex.research import anchored_purged_windows, promotion_verdict, summarize_trade_rows
from daytrader.quantrex.forward import ForwardPaperRunner
from daytrader.quantrex.risk import RiskKernel, RiskState
from daytrader.quantrex.shadow import NoSubmitVenue, SubmissionDisabled
from daytrader.quantrex.service import evaluate_public_snapshot
from daytrader.quantrex.strategies import breakout_signal, qsr_signal, qsr_v0_signal


BAR_MS = 15 * 60 * 1000
HOUR_MS = 60 * 60 * 1000


def candle(open_time, open_, high, low, close):
    return Candle(
        open_time=open_time,
        close_time=open_time + BAR_MS - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        quote_volume=10_000.0,
        trades=100,
    )


def hourly_candle(open_time, close):
    return Candle(
        open_time=open_time,
        close_time=open_time + HOUR_MS - 1,
        open=close - 0.1,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=100.0,
        quote_volume=10_000.0,
        trades=100,
    )


def qsr_fixture():
    day0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = []
    for index in range(96):
        bars.append(candle(day0 + index * BAR_MS, 100.0, 100.5, 99.5, 100.1))
    day1 = day0 + 96 * BAR_MS
    for index in range(28):
        bars.append(candle(day1 + index * BAR_MS, 100.0, 100.5, 99.6, 100.1))
    bars.append(candle(day1 + 28 * BAR_MS, 99.6, 100.7, 99.0, 100.5))
    hourly = tuple(
        hourly_candle(day0 - (70 - index) * HOUR_MS, 90.0 + index * 0.2)
        for index in range(70)
    )
    return tuple(bars), hourly


class QuantrexDataTests(unittest.TestCase):
    def setUp(self):
        self.config = QuantrexConfig()
        self.bars, self.hourly = qsr_fixture()
        observed = self.bars[-1].close_time + 1_000
        self.snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            primary=self.bars,
            trend=self.bars,
            higher=self.hourly,
            bid=100.49,
            ask=100.51,
            mark=100.50,
            funding_bp=0.0,
            price_change_pct_24h=0.0,
            quote_volume_24h=1_000_000.0,
            captured_at=datetime.fromtimestamp(observed / 1000, timezone.utc).isoformat(),
        )
        self.observed = observed

    def test_closed_usdm_contract_is_hashed(self):
        first = validate_snapshot(self.snapshot, self.config, self.observed)
        second = validate_snapshot(self.snapshot, self.config, self.observed)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_forming_candle_fails_closed(self):
        future = candle(self.bars[-1].open_time + BAR_MS, 100.5, 101.0, 100.0, 100.7)
        bad = MarketSnapshot(
            **{**self.snapshot.__dict__, "trend": self.bars + (future,)}
        )
        with self.assertRaisesRegex(DataContractError, "forming/current"):
            validate_snapshot(bad, self.config, self.observed)

    def test_stale_quote_fails_closed(self):
        with self.assertRaisesRegex(DataContractError, "stale"):
            validate_snapshot(self.snapshot, self.config, self.observed + 2_001)

    def test_non_universe_symbol_fails_closed(self):
        bad = MarketSnapshot(**{**self.snapshot.__dict__, "symbol": "DOGEUSDT"})
        with self.assertRaisesRegex(DataContractError, "universe"):
            validate_snapshot(bad, self.config, self.observed)

    def test_public_shadow_service_exposes_levels_but_blocks_missing_depth(self):
        result = evaluate_public_snapshot(
            self.snapshot,
            self.config,
            observed_at=datetime.fromtimestamp(self.observed / 1000, timezone.utc),
            depth_available=False,
        )
        self.assertEqual(result["status"], "CANDIDATE")
        self.assertEqual(len(result["chart_bars"]), 96)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["state"], "BLOCKED")
        self.assertIn("required depth unavailable", candidate["blocked_by"])
        self.assertGreater(candidate["tp1"], candidate["entry"])
        self.assertGreater(candidate["tp2"], candidate["tp1"])
        self.assertGreater(candidate["tp3"], candidate["tp2"])
        self.assertEqual([leg["fraction"] for leg in candidate["exit_plan"]], [0.5, 0.3, 0.2])
        self.assertEqual(len(candidate["chart_bars"]), 96)
        self.assertLess(candidate["chart_bars"][-1]["time"], self.observed // 1000)
        self.assertIsNone(candidate["shadow_order"])

    def test_public_depth_at_size_can_clear_depth_gate(self):
        liquid = MarketSnapshot(
            **{
                **self.snapshot.__dict__,
                "bid_depth": ((100.49, 1_000.0),),
                "ask_depth": ((100.51, 1_000.0),),
            }
        )
        result = evaluate_public_snapshot(
            liquid,
            self.config,
            observed_at=datetime.fromtimestamp(self.observed / 1000, timezone.utc),
        )
        candidate = result["candidates"][0]
        self.assertNotIn("required depth unavailable", candidate["blocked_by"])
        self.assertGreaterEqual(candidate["costs"]["impact_usd"], 0.0)


class QuantrexStrategyTests(unittest.TestCase):
    def setUp(self):
        self.config = QuantrexConfig()
        self.bars, self.hourly = qsr_fixture()

    def test_qsr_previous_day_sweep_reclaim(self):
        signal = qsr_signal(
            "BTCUSDT", self.bars, self.hourly, 100.49, 100.51, self.config
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.book, Book.QSR)
        self.assertEqual(signal.side, Side.LONG)
        self.assertTrue(signal.reference_level.startswith("PDL:"))
        self.assertEqual(signal.session_arm, "LONDON")
        self.assertGreater(signal.target, signal.entry_quote)
        self.assertEqual(signal.strategy_version, "QSR1_V1")

    def test_breakout_uses_prior_twenty_completed_bars(self):
        bars = list(self.bars[-40:])
        last = bars[-1]
        bars[-1] = candle(last.open_time, 100.2, 102.0, 100.0, 101.8)
        signal = breakout_signal(
            "ETHUSDT", tuple(bars), self.hourly, 101.79, 101.81, self.config
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.book, Book.BREAKOUT)
        self.assertEqual(signal.side, Side.LONG)
        self.assertEqual(signal.time_exit - signal.signal_close_time, 16 * BAR_MS)

    def test_qsr_v0_reference_remains_versioned_separately(self):
        signal = qsr_v0_signal(
            "BTCUSDT", self.bars, self.hourly, 100.49, 100.51, self.config
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.strategy_version, self.config.qsr_v0_version)
        decision = RiskKernel(self.config).decide(
            signal,
            CostEstimate(fee_usd=0.2, spread_usd=0.05, impact_usd=0.05, slippage_usd=0.05),
            depth_available=True,
            feed_age_seconds=0.5,
        )
        broker = PaperBroker(self.config)
        position = broker.open(signal, decision, signal.signal_close_time + 1)
        self.assertEqual(len(position.legs), 1)
        self.assertEqual(position.legs[0].name, "TP")


class QuantrexRiskAndShadowTests(unittest.TestCase):
    def setUp(self):
        self.config = QuantrexConfig()
        bars, hourly = qsr_fixture()
        self.signal = qsr_signal(
            "BTCUSDT", bars, hourly, 100.49, 100.51, self.config
        )
        self.assertIsNotNone(self.signal)
        self.costs = CostEstimate(
            fee_usd=0.20, spread_usd=0.05, impact_usd=0.05, slippage_usd=0.05
        )

    def test_risk_accepts_one_position_and_then_blocks_aggregate_risk(self):
        kernel = RiskKernel(self.config)
        decision = kernel.decide(
            self.signal, self.costs, depth_available=True, feed_age_seconds=0.5
        )
        self.assertTrue(decision.accepted, decision.reasons)
        self.assertAlmostEqual(decision.risk_usd, 10.0)
        net_stop_loss = decision.quantity * abs(self.signal.entry_quote - self.signal.stop) + decision.estimated_costs.round_trip_usd
        self.assertAlmostEqual(net_stop_loss, decision.risk_usd, places=8)
        kernel.register_intent(self.signal, decision)
        duplicate = kernel.decide(
            self.signal, self.costs, depth_available=True, feed_age_seconds=0.5
        )
        self.assertFalse(duplicate.accepted)
        self.assertIn("duplicate intent", duplicate.reasons)
        self.assertIn("aggregate open-risk cap reached", duplicate.reasons)

    def test_kill_switch_and_stale_feed_fail_closed(self):
        state = RiskState(kill_switch=True)
        decision = RiskKernel(self.config, state).decide(
            self.signal, self.costs, depth_available=True, feed_age_seconds=2.1
        )
        self.assertFalse(decision.accepted)
        self.assertIn("manual kill switch active", decision.reasons)
        self.assertIn("quote/book feed stale", decision.reasons)

    def test_utc_day_roll_resets_only_day_scoped_limits(self):
        state = RiskState(
            equity_usd=9_950.0,
            day_start_equity_usd=10_000.0,
            entries_by_book={Book.QSR.value: 3},
            consecutive_losses=3,
            kill_switch=True,
        )
        kernel = RiskKernel(self.config, state)
        kernel.roll_utc_day("2026-01-02")
        self.assertEqual(state.day_start_equity_usd, 9_950.0)
        self.assertEqual(state.entries_by_book, {})
        self.assertEqual(state.consecutive_losses, 3)
        self.assertTrue(state.kill_switch)

    def test_shadow_adapter_has_no_submit_path(self):
        decision = RiskKernel(self.config).decide(
            self.signal, self.costs, depth_available=True, feed_age_seconds=0.5
        )
        venue = NoSubmitVenue()
        order = venue.prepare(self.signal, decision)
        self.assertEqual(order.mode, "SHADOW_NO_SUBMIT")
        with self.assertRaises(SubmissionDisabled):
            venue.submit(order)

    def test_deterministic_replay_hashes_match(self):
        hashes = []
        for _ in range(2):
            ledger = ReplayLedger(self.config.schema_version)
            ledger.append(
                "SIGNAL",
                self.signal.idempotency_key,
                self.signal.signal_close_time,
                {"book": self.signal.book.value},
            )
            ledger.append(
                "INTENT",
                self.signal.idempotency_key,
                self.signal.signal_close_time + 1,
                {"side": self.signal.side.value},
            )
            hashes.append(ledger.output_hash)
        self.assertEqual(hashes[0], hashes[1])

    def _broker(self):
        kernel = RiskKernel(self.config)
        decision = kernel.decide(
            self.signal, self.costs, depth_available=True, feed_age_seconds=0.5
        )
        self.assertTrue(decision.accepted, decision.reasons)
        broker = PaperBroker(self.config, kernel)
        broker.open(self.signal, decision, self.signal.signal_close_time + 1)
        return broker

    def test_qsr_v1_partial_targets_are_50_30_20(self):
        broker = self._broker()
        position = broker.position
        self.assertIsNotNone(position)
        self.assertEqual([leg.name for leg in position.legs], ["TP1", "TP2", "TP3"])
        self.assertEqual([leg.original_fraction for leg in position.legs], [0.5, 0.3, 0.2])
        bar = candle(
            self.signal.signal_close_time + 1,
            self.signal.entry_quote,
            position.legs[0].target,
            self.signal.entry_quote,
            position.legs[0].target,
        )
        self.assertEqual(broker.process_bar(bar), ("TP1",))
        self.assertAlmostEqual(position.remaining_quantity, position.original_quantity * 0.5)
        self.assertEqual(position.legs[0].status, "FILLED")
        self.assertEqual(position.legs[1].status, "OPEN")

    def test_same_bar_stop_target_collision_scores_stop_first(self):
        broker = self._broker()
        position = broker.position
        bar = candle(
            self.signal.signal_close_time + 1,
            self.signal.entry_quote,
            position.legs[-1].target,
            position.stop - 0.01,
            self.signal.entry_quote,
        )
        self.assertEqual(broker.process_bar(bar), ("SL",))
        self.assertEqual(position.status, "STOPPED")
        self.assertTrue(all(leg.status == "CANCELLED_BY_STOP" for leg in position.legs))
        event_types = [event.event_type for event in broker.ledger.events]
        self.assertNotIn("FILL_TARGET", event_types)

    def test_all_qsr_targets_realize_weighted_1_35r_net_of_costs(self):
        broker = self._broker()
        position = broker.position
        bar = candle(
            self.signal.signal_close_time + 1,
            position.entry,
            position.legs[-1].target,
            position.entry,
            position.legs[-1].target,
        )
        self.assertEqual(broker.process_bar(bar), ("TP1", "TP2", "TP3"))
        self.assertEqual(position.status, "TARGETS_COMPLETE")
        self.assertAlmostEqual(position.recognized_cost_usd, position.modeled_cost_usd)
        self.assertAlmostEqual(position.realized_pnl_usd, position.risk_usd * 1.35, places=8)

    def test_gap_through_stop_fills_at_pessimistic_open(self):
        broker = self._broker()
        position = broker.position
        gap_open = position.stop - 0.25
        bar = candle(
            self.signal.signal_close_time + 1,
            gap_open,
            self.signal.entry_quote,
            gap_open - 0.1,
            gap_open,
        )
        broker.process_bar(bar)
        stop_event = next(event for event in broker.ledger.events if event.event_type == "FILL_STOP")
        self.assertEqual(stop_event.payload["price"], gap_open)

    def test_persistent_snapshot_is_atomic_and_deterministic(self):
        hashes = []
        with TemporaryDirectory() as temporary:
            for index in range(2):
                broker = self._broker()
                path = Path(temporary) / f"paper-{index}.json"
                broker.save(path)
                self.assertTrue(path.exists())
                self.assertFalse(path.with_suffix(".json.tmp").exists())
                hashes.append(broker.snapshot()["snapshot_hash"])
        self.assertEqual(hashes[0], hashes[1])

    def test_restart_restores_partial_position_and_reconciles(self):
        with TemporaryDirectory() as temporary:
            broker = self._broker()
            position = broker.position
            first_target = position.legs[0].target
            broker.process_bar(candle(self.signal.signal_close_time + 1, position.entry, first_target, position.entry, first_target))
            path = Path(temporary) / "paper.json"
            broker.save(path)
            restored = PaperBroker.load(self.config, path)
            self.assertTrue(restored.risk.state.reconciliation_ok)
            self.assertAlmostEqual(restored.position.remaining_quantity, position.original_quantity * 0.5)
            self.assertEqual(restored.position.legs[0].status, "FILLED")
            self.assertEqual(restored.ledger.output_hash, broker.ledger.output_hash)

    def test_restart_rejects_tampered_snapshot(self):
        with TemporaryDirectory() as temporary:
            broker = self._broker()
            path = Path(temporary) / "paper.json"
            broker.save(path)
            content = path.read_text(encoding="utf-8").replace('"equity_usd":10000.0', '"equity_usd":999999.0')
            path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
                PaperBroker.load(self.config, path)


class QuantrexResearchTests(unittest.TestCase):
    def test_walk_forward_is_anchored_purged_and_final_window_untouched(self):
        timestamps = [index * HOUR_MS for index in range(100)]
        windows = anchored_purged_windows(timestamps, embargo_ms=2 * HOUR_MS)
        self.assertEqual(len(windows), 4)
        self.assertTrue(windows[-1].untouched)
        for window in windows:
            self.assertLess(window.train_end, window.test_start - window.embargo_ms)
            self.assertEqual(window.train_start, timestamps[0])

    def test_summary_and_promotion_gate_are_deterministic_and_fail_closed(self):
        rows = [
            {
                "entry_time": index * HOUR_MS,
                "symbol": ("BTCUSDT", "ETHUSDT", "SOLUSDT")[index % 3],
                "session": ("LONDON", "NEW_YORK")[index % 2],
                "volatility_regime": ("LOW", "MID", "HIGH")[index % 3],
                "net_r": 0.25 if index % 3 else -0.10,
                "net_bps": 2.0,
                "turnover_usd": 100.0,
                "modeled_cost_usd": 0.10,
            }
            for index in range(180)
        ]
        first = summarize_trade_rows(rows, bootstrap_samples=200)
        second = summarize_trade_rows(rows, bootstrap_samples=200)
        self.assertEqual(first, second)
        self.assertEqual(first["opportunities"], 180)
        verdict = promotion_verdict(
            first,
            doubled_slippage_expectancy_r=0.01,
            delayed_entry_expectancy_r=0.01,
            integrity_defects={"leakage": 0, "stale": 0, "duplicate": 0, "reconciliation": 0},
        )
        self.assertEqual(verdict["verdict"], "GO_FORWARD_PAPER")
        missing_stress = promotion_verdict(
            first,
            doubled_slippage_expectancy_r=None,
            delayed_entry_expectancy_r=None,
            integrity_defects={},
        )
        self.assertEqual(missing_stress["verdict"], "NO_GO")


class QuantrexForwardPaperTests(unittest.TestCase):
    def test_runner_persists_signal_skip_and_deduplicates_replay(self):
        config = QuantrexConfig()
        bars, hourly = qsr_fixture()
        observed = bars[-1].close_time + 1_000
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            primary=bars,
            trend=bars,
            higher=hourly,
            bid=100.49,
            ask=100.51,
            mark=100.50,
            funding_bp=0.0,
            price_change_pct_24h=0.0,
            quote_volume_24h=1_000_000.0,
            captured_at=datetime.fromtimestamp(observed / 1000, timezone.utc).isoformat(),
        )
        evaluation = evaluate_public_snapshot(
            snapshot,
            config,
            observed_at=datetime.fromtimestamp(observed / 1000, timezone.utc),
            depth_available=False,
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "forward.json"
            runner = ForwardPaperRunner(config, path)
            first = runner.consume([evaluation], {"BTCUSDT": snapshot})
            second = runner.consume([evaluation], {"BTCUSDT": snapshot})
            restored = ForwardPaperRunner(config, path).snapshot()
        self.assertEqual(first["signals"], 1)
        self.assertEqual(first["risk_rejections"], 1)
        self.assertEqual(second["signals"], first["signals"])
        self.assertEqual(restored["ledger_hash"], first["ledger_hash"])
        self.assertTrue(restored["no_submit"])


if __name__ == "__main__":
    unittest.main()
