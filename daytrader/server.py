#!/usr/bin/env python3
"""CARRY-DAY live intraday scanner with automatic paper trading."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parent))
    from daytrader.config import Config
    from daytrader.engine import evaluate_snapshot
    from daytrader.market import BinanceFuturesClient
    from daytrader.paper import PaperBroker
else:
    from .config import Config
    from .engine import evaluate_snapshot
    from .market import BinanceFuturesClient
    from .paper import PaperBroker


class Scanner:
    def __init__(self, config: Config, state_file: Path):
        self.config = config
        self.client = BinanceFuturesClient(config)
        self.broker = PaperBroker(state_file, config)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.universe: list[str] = []
        self.last_universe_refresh = 0.0
        self.state = {
            "app": "CARRY-DAY",
            "mode": "PAPER_ONLY",
            "research_status": "UNVALIDATED_V2_AFTER_FAILED_V1",
            "status": "STARTING",
            "last_scan": None,
            "next_scan": None,
            "universe": [],
            "signals": [],
            "errors": [],
            "paper": self.broker.snapshot(),
            "config": self.config.public_dict(),
            "method": (
                "closed 5m trigger + momentum/strength/volume/VWAP, aligned with "
                "15m trend and 1h regime; friction, session, and risk gates fail closed"
            ),
        }

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _refresh_universe(self) -> None:
        if (
            not self.universe
            or time.time() - self.last_universe_refresh
            >= self.config.universe_refresh_seconds
        ):
            self.universe = self.client.top_symbols()
            self.last_universe_refresh = time.time()

    def scan_once(self) -> dict:
        started = datetime.now(timezone.utc)
        errors = []
        signals = []
        prices = {}
        try:
            self._refresh_universe()
        except Exception as exc:
            errors.append(f"universe: {type(exc).__name__}: {exc}")

        if self.universe:
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
                jobs = {
                    pool.submit(self.client.snapshot, symbol): symbol
                    for symbol in self.universe
                }
                snapshots = []
                for future in as_completed(jobs):
                    symbol = jobs[future]
                    try:
                        snapshots.append(future.result())
                    except Exception as exc:
                        errors.append(f"{symbol}: {type(exc).__name__}: {exc}")

            paper_before = self.broker.snapshot(started)
            equity = float(paper_before["equity_usd"])
            for market in snapshots:
                try:
                    signal = evaluate_snapshot(
                        market, self.config, equity_usd=equity, now=started
                    )
                    signals.append(signal.to_dict())
                    prices[market.symbol] = market.mark or market.primary[-1].close
                except Exception as exc:
                    errors.append(
                        f"{market.symbol} engine: {type(exc).__name__}: {exc}"
                    )

        events = self.broker.update_prices(prices, started)
        signals.sort(
            key=lambda item: (
                {"LIVE": 0, "ARMED": 1, "WATCH": 2}.get(item["state"], 3),
                -item["score"],
                item["symbol"],
            )
        )
        if self.config.auto_paper:
            for signal in signals:
                if signal["state"] != "LIVE" or not signal["ticket"]:
                    continue
                opened, reason = self.broker.open_from_ticket(
                    signal["ticket"], now=started
                )
                if opened:
                    events.append(
                        {
                            "symbol": signal["symbol"],
                            "event": "OPEN",
                            "side": signal["side"],
                        }
                    )
                elif reason not in {"signal already processed", "symbol already open"}:
                    signal["paper_blocked_by"] = reason

        finished = datetime.now(timezone.utc)
        directional_count = sum(
            item["state"] == "LIVE" for item in signals
        )
        with self.lock:
            self.state.update(
                {
                    "status": "LIVE" if self.universe else "DEGRADED",
                    "last_scan": finished.isoformat(),
                    "scan_duration_seconds": round(
                        (finished - started).total_seconds(), 2
                    ),
                    "next_scan": (
                        finished
                        + timedelta(seconds=self.config.scan_interval_seconds)
                    ).isoformat(),
                    "universe": self.universe,
                    "signals": signals,
                    "fires": directional_count,
                    "events": events,
                    "errors": errors[-20:],
                    "paper": self.broker.snapshot(finished),
                }
            )
            return self.snapshot()

    def run(self) -> None:
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                self.scan_once()
            except Exception as exc:
                with self.lock:
                    self.state["status"] = "DEGRADED"
                    self.state["errors"] = [
                        f"scan: {type(exc).__name__}: {exc}"
                    ] + self.state["errors"][:19]
            elapsed = time.monotonic() - cycle_started
            self.stop_event.wait(max(1.0, self.config.scan_interval_seconds - elapsed))


def make_handler(scanner: Scanner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, body: bytes | str, content_type: str, code: int = 200):
            payload = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self._send(
                    json.dumps(scanner.snapshot()), "application/json; charset=utf-8"
                )
            elif path == "/api/health":
                state = scanner.snapshot()
                code = 200 if state["status"] == "LIVE" else 503
                self._send(
                    json.dumps(
                        {
                            "status": state["status"],
                            "last_scan": state["last_scan"],
                            "errors": state["errors"],
                        }
                    ),
                    "application/json; charset=utf-8",
                    code,
                )
            elif path in {"/", "/index.html"}:
                self._send(
                    (ROOT / "index.html").read_text(),
                    "text/html; charset=utf-8",
                )
            elif path == "/static.js":
                self._send(
                    (ROOT / "static.js").read_text(),
                    "text/javascript; charset=utf-8",
                )
            else:
                self._send("not found", "text/plain; charset=utf-8", 404)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CARRY-DAY intraday scanner and paper trader"
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7200)))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--once", action="store_true", help="scan once and print JSON")
    parser.add_argument(
        "--no-auto-paper", action="store_true", help="scan without opening paper trades"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=ROOT / ".paper_state.json",
    )
    args = parser.parse_args()
    config = Config.from_env()
    if args.no_auto_paper:
        config = Config(**{**config.__dict__, "auto_paper": False})
    scanner = Scanner(config, args.state_file)
    if args.once:
        print(json.dumps(scanner.scan_once(), indent=2))
        return

    thread = threading.Thread(target=scanner.run, daemon=True)
    thread.start()
    print(
        f"CARRY-DAY paper scanner → http://{args.host}:{args.port}/ "
        f"({config.primary_interval}/{config.trend_interval}/{config.higher_interval}, "
        f"risk {config.risk_per_trade_pct:.2f}%/trade)"
    )
    try:
        ThreadingHTTPServer((args.host, args.port), make_handler(scanner)).serve_forever()
    except KeyboardInterrupt:
        scanner.stop_event.set()


if __name__ == "__main__":
    main()
