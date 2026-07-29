"""Small stdlib Binance USD-M Futures public-market-data client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Config


STABLE_LIKE = {
    "USDT",
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "BUSD",
    "EUR",
    "TRY",
}


@dataclass(frozen=True)
class Candle:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int

    @classmethod
    def from_binance(cls, row: list[Any]) -> "Candle":
        return cls(
            open_time=int(row[0]),
            close_time=int(row[6]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
            trades=int(row[8]),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    primary: tuple[Candle, ...]
    trend: tuple[Candle, ...]
    bid: float
    ask: float
    mark: float
    funding_bp: float
    price_change_pct_24h: float
    quote_volume_24h: float
    captured_at: str


class MarketDataError(RuntimeError):
    pass


class BinanceFuturesClient:
    def __init__(self, config: Config):
        self.config = config
        self.ticker_context: dict[str, dict] = {}

    def _get(self, path: str, params: dict | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.config.api_base}{path}"
        if query:
            url = f"{url}?{query}"
        last_error: Exception | None = None
        for attempt in range(self.config.request_retries):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "carry-day/1.0 public-market-data",
                    },
                )
                with urllib.request.urlopen(
                    req, timeout=self.config.request_timeout_seconds
                ) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                last_error = exc
                retry_after = exc.headers.get("Retry-After")
                if exc.code in {418, 429}:
                    wait = min(30.0, float(retry_after or (2**attempt)))
                elif 500 <= exc.code < 600:
                    wait = 0.5 * (2**attempt)
                else:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                wait = 0.5 * (2**attempt)
            if attempt + 1 < self.config.request_retries:
                time.sleep(wait)
        raise MarketDataError(f"{path} failed: {last_error}")

    def top_symbols(self) -> list[str]:
        tickers = self._get("/fapi/v1/ticker/24hr")
        exchange = self._get("/fapi/v1/exchangeInfo")
        now_ms = int(time.time() * 1000)
        min_age_ms = self.config.min_listing_days * 86_400_000
        eligible = {}
        for item in exchange.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("contractType") == "PERPETUAL"
                and item.get("quoteAsset") == "USDT"
                and item.get("baseAsset") not in STABLE_LIKE
                and now_ms - int(item.get("onboardDate") or 0) >= min_age_ms
            ):
                eligible[item["symbol"]] = item

        ranked = []
        context = {}
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            if symbol not in eligible:
                continue
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            price_change_pct = float(ticker.get("priceChangePercent") or 0.0)
            if abs(price_change_pct) > self.config.max_abs_24h_change_pct:
                continue
            context[symbol] = {
                "quote_volume_24h": quote_volume,
                "price_change_pct_24h": price_change_pct,
            }
            ranked.append((quote_volume, symbol))
        ranked.sort(reverse=True)
        self.ticker_context = context
        return [symbol for _, symbol in ranked[: self.config.universe_size]]

    def closed_klines(self, symbol: str, interval: str, limit: int) -> tuple[Candle, ...]:
        now_ms = int(time.time() * 1000)
        rows = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        return tuple(
            candle
            for candle in (Candle.from_binance(row) for row in rows)
            if candle.close_time < now_ms
        )

    def snapshot(self, symbol: str) -> MarketSnapshot:
        primary = self.closed_klines(
            symbol, self.config.primary_interval, self.config.candle_limit
        )
        trend = self.closed_klines(
            symbol, self.config.trend_interval, self.config.candle_limit
        )
        book = self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        premium = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        context = self.ticker_context.get(symbol, {})
        return MarketSnapshot(
            symbol=symbol,
            primary=primary,
            trend=trend,
            bid=float(book.get("bidPrice") or 0.0),
            ask=float(book.get("askPrice") or 0.0),
            mark=float(premium.get("markPrice") or 0.0),
            funding_bp=float(premium.get("lastFundingRate") or 0.0) * 10_000.0,
            price_change_pct_24h=float(context.get("price_change_pct_24h") or 0.0),
            quote_volume_24h=float(context.get("quote_volume_24h") or 0.0),
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    def historical_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> tuple[Candle, ...]:
        """Paginate public klines in chronological order."""
        out: list[Candle] = []
        cursor = start_ms
        while cursor < end_ms:
            rows = self._get(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1500,
                },
            )
            if not rows:
                break
            batch = [Candle.from_binance(row) for row in rows]
            out.extend(batch)
            next_cursor = batch[-1].close_time + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < 1500:
                break
            time.sleep(0.05)
        deduped = {bar.open_time: bar for bar in out if bar.close_time <= end_ms}
        return tuple(deduped[key] for key in sorted(deduped))
