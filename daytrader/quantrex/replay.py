"""Append-only deterministic replay and reconciliation."""

from __future__ import annotations

from dataclasses import asdict

from .contracts import LedgerEvent, canonical_hash


class ReplayLedger:
    def __init__(self, schema_version: str):
        self.schema_version = schema_version
        self.events: list[LedgerEvent] = []
        self.keys: set[str] = set()

    def append(self, event_type: str, idempotency_key: str, timestamp: int, payload: dict) -> None:
        if idempotency_key in self.keys and event_type == "INTENT":
            raise ValueError("duplicate intent")
        if event_type == "INTENT":
            self.keys.add(idempotency_key)
        self.events.append(
            LedgerEvent(
                schema_version=self.schema_version,
                sequence=len(self.events) + 1,
                event_type=event_type,
                idempotency_key=idempotency_key,
                timestamp=timestamp,
                payload=payload,
            )
        )

    def reconcile(self) -> bool:
        expected = list(range(1, len(self.events) + 1))
        return [event.sequence for event in self.events] == expected

    @property
    def output_hash(self) -> str:
        if not self.reconcile():
            raise ValueError("ledger sequence mismatch")
        return canonical_hash([asdict(event) for event in self.events])
