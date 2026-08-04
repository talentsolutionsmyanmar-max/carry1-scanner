"""Fail-closed process and health controls for the paper runner."""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timezone
from pathlib import Path


class RunnerAlreadyActive(RuntimeError):
    pass


class SingleInstanceLock:
    """Hold an advisory lock for the lifetime of one state-file writer."""

    def __init__(self, path: Path):
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise RunnerAlreadyActive(
                f"another paper runner holds {self.path}"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.fsync(descriptor)
        self._descriptor = descriptor
        return self

    def release(self) -> None:
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *_args) -> None:
        self.release()


def health_snapshot(
    state: dict,
    *,
    now: datetime | None = None,
    stale_after_seconds: float,
) -> tuple[int, dict]:
    """Return HTTP status and explicit freshness evidence."""

    checked_at = now or datetime.now(timezone.utc)
    last_scan = state.get("last_scan")
    age_seconds: float | None = None
    if last_scan:
        try:
            observed = datetime.fromisoformat(str(last_scan))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (checked_at - observed).total_seconds())
        except (TypeError, ValueError):
            age_seconds = None
    fresh = age_seconds is not None and age_seconds <= stale_after_seconds
    live = state.get("status") == "LIVE"
    healthy = live and fresh
    return (
        200 if healthy else 503,
        {
            "status": state.get("status"),
            "healthy": healthy,
            "fresh": fresh,
            "last_scan": last_scan,
            "scan_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
            "stale_after_seconds": stale_after_seconds,
            "errors": state.get("errors", []),
        },
    )
