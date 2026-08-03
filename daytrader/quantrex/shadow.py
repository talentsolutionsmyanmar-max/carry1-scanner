"""Physically dormant venue adapter for public-market shadow comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .contracts import RiskDecision, SignalEvent, canonical_hash


class SubmissionDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class ShadowOrder:
    mode: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    reference_quote: float
    idempotency_key: str
    payload_hash: str


class NoSubmitVenue:
    """Has no HTTP client, credential loader, endpoint, or enable switch."""

    mode = "SHADOW_NO_SUBMIT"

    def prepare(self, signal: SignalEvent, decision: RiskDecision) -> ShadowOrder:
        if not decision.accepted:
            raise ValueError("cannot prepare rejected intent")
        payload = {
            "mode": self.mode,
            "symbol": signal.symbol,
            "side": signal.side.value,
            "quantity": decision.quantity,
            "order_type": "MARKET",
            "reference_quote": signal.entry_quote,
            "idempotency_key": signal.idempotency_key,
        }
        return ShadowOrder(**payload, payload_hash=canonical_hash(payload))

    def submit(self, *_args, **_kwargs) -> None:
        raise SubmissionDisabled("exchange order submission is physically absent in Option A")

    @staticmethod
    def telemetry(order: ShadowOrder, observed_quote: float) -> dict:
        return {
            **asdict(order),
            "observed_quote": observed_quote,
            "quote_drift_bp": round(
                (observed_quote / order.reference_quote - 1.0) * 10_000.0, 6
            ),
        }
