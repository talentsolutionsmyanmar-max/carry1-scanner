"""Persistent, fail-closed paper broker. It has no live-order code path."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


class PaperBroker:
    def __init__(self, path: Path, config: Config):
        self.path = path
        self.config = config
        self.lock = threading.RLock()
        self.state = self._load()

    def _new_state(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "version": 1,
            "mode": "PAPER_ONLY",
            "starting_equity_usd": self.config.starting_equity_usd,
            "cash_usd": self.config.starting_equity_usd,
            "equity_usd": self.config.starting_equity_usd,
            "utc_day": now.date().isoformat(),
            "day_start_equity_usd": self.config.starting_equity_usd,
            "daily_realized_pnl_usd": 0.0,
            "trades_today": 0,
            "positions": {},
            "history": [],
            "seen_signals": [],
            "last_action": None,
        }

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text())
            if state.get("version") == 1 and state.get("mode") == "PAPER_ONLY":
                state.setdefault("day_start_equity_usd", state.get("cash_usd", 0.0))
                return state
        except (OSError, ValueError, TypeError):
            pass
        return self._new_state()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def _roll_day(self, now: datetime) -> None:
        day = now.date().isoformat()
        if day != self.state["utc_day"]:
            self.state["utc_day"] = day
            self.state["day_start_equity_usd"] = self.state["cash_usd"]
            self.state["daily_realized_pnl_usd"] = 0.0
            self.state["trades_today"] = 0

    def _risk_status(self) -> dict:
        loss_limit = (
            self.state["day_start_equity_usd"]
            * self.config.max_daily_loss_fraction
        )
        reasons = []
        if self.state["daily_realized_pnl_usd"] <= -loss_limit:
            reasons.append("daily loss limit reached")
        if self.state["trades_today"] >= self.config.max_trades_per_day:
            reasons.append("daily trade limit reached")
        if len(self.state["positions"]) >= self.config.max_open_positions:
            reasons.append("maximum open positions reached")
        return {
            "can_open": not reasons,
            "blocked_by": reasons,
            "daily_loss_limit_usd": round(loss_limit, 2),
            "daily_realized_pnl_usd": round(
                self.state["daily_realized_pnl_usd"], 2
            ),
            "trades_today": self.state["trades_today"],
            "max_trades_per_day": self.config.max_trades_per_day,
            "open_positions": len(self.state["positions"]),
            "max_open_positions": self.config.max_open_positions,
        }

    def open_from_ticket(self, ticket: dict, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self._roll_day(now)
            signal_id = ticket.get("signal_id")
            symbol = ticket.get("symbol")
            if not signal_id or ticket.get("mode") != "PAPER_ONLY":
                return False, "invalid paper ticket"
            if signal_id in self.state["seen_signals"]:
                return False, "signal already processed"
            if symbol in self.state["positions"]:
                return False, "symbol already open"
            for trade in self.state["history"]:
                if trade.get("symbol") != symbol or not trade.get("closed_at"):
                    continue
                closed_at = datetime.fromisoformat(trade["closed_at"])
                elapsed_minutes = (now - closed_at).total_seconds() / 60.0
                if elapsed_minutes < self.config.cooldown_minutes:
                    return False, (
                        f"{symbol} cooldown "
                        f"({self.config.cooldown_minutes - elapsed_minutes:.0f}m left)"
                    )
                break
            risk = self._risk_status()
            if not risk["can_open"]:
                return False, ", ".join(risk["blocked_by"])

            position = {
                **ticket,
                "status": "OPEN",
                "opened_at": now.isoformat(),
                "mark": ticket["entry"],
                "unrealized_pnl_usd": -ticket["estimated_round_trip_cost_usd"],
            }
            self.state["positions"][symbol] = position
            self.state["seen_signals"] = (
                self.state["seen_signals"] + [signal_id]
            )[-500:]
            self.state["trades_today"] += 1
            self.state["last_action"] = f"OPEN {ticket['side']} {symbol} (paper)"
            self._save()
            return True, "paper position opened"

    def _close(self, symbol: str, price: float, reason: str, now: datetime) -> None:
        position = self.state["positions"].pop(symbol)
        direction = 1.0 if position["side"] == "LONG" else -1.0
        gross = (
            direction
            * (price - float(position["entry"]))
            * float(position["quantity"])
        )
        cost = float(position["estimated_round_trip_cost_usd"])
        net = gross - cost
        record = {
            **position,
            "status": "CLOSED",
            "exit": price,
            "closed_at": now.isoformat(),
            "exit_reason": reason,
            "gross_pnl_usd": round(gross, 2),
            "cost_usd": round(cost, 2),
            "net_pnl_usd": round(net, 2),
        }
        self.state["cash_usd"] += net
        self.state["daily_realized_pnl_usd"] += net
        self.state["history"] = ([record] + self.state["history"])[:200]
        self.state["last_action"] = f"CLOSE {symbol} {reason} {net:+.2f} USD"

    def update_prices(
        self, prices: dict[str, float], now: datetime | None = None
    ) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        events = []
        with self.lock:
            self._roll_day(now)
            unrealized_total = 0.0
            for symbol, position in list(self.state["positions"].items()):
                price = float(prices.get(symbol) or 0.0)
                if price <= 0:
                    continue
                side = position["side"]
                direction = 1.0 if side == "LONG" else -1.0
                stop = float(position["stop"])
                target = float(position["tp2"])
                opened = datetime.fromisoformat(position["opened_at"])
                deadline = datetime.fromisoformat(position["time_exit"])
                reason = None
                exit_price = price
                if (side == "LONG" and price <= stop) or (
                    side == "SHORT" and price >= stop
                ):
                    reason = "STOP"
                elif (side == "LONG" and price >= target) or (
                    side == "SHORT" and price <= target
                ):
                    reason = "TP2"
                    exit_price = target
                elif now >= deadline:
                    reason = "TIME_EXIT"
                elif opened.date() != now.date():
                    reason = "UTC_DAY_FLAT"

                if reason:
                    self._close(symbol, exit_price, reason, now)
                    events.append(
                        {"symbol": symbol, "event": "CLOSE", "reason": reason}
                    )
                    continue

                gross = (
                    direction
                    * (price - float(position["entry"]))
                    * float(position["quantity"])
                )
                estimated_cost = float(position["estimated_round_trip_cost_usd"])
                unrealized = gross - estimated_cost
                position["mark"] = price
                position["unrealized_pnl_usd"] = round(unrealized, 2)
                unrealized_total += unrealized
            self.state["equity_usd"] = round(
                self.state["cash_usd"] + unrealized_total, 2
            )
            self._save()
        return events

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self.lock:
            self._roll_day(now)
            return {
                "mode": self.state["mode"],
                "cash_usd": round(self.state["cash_usd"], 2),
                "equity_usd": round(self.state["equity_usd"], 2),
                "starting_equity_usd": self.state["starting_equity_usd"],
                "utc_day": self.state["utc_day"],
                "positions": list(self.state["positions"].values()),
                "history": self.state["history"][:20],
                "last_action": self.state["last_action"],
                "risk": self._risk_status(),
            }
