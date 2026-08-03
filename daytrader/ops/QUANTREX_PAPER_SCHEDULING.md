# Quantrex paper scheduling

Quantrex entries are already restricted by the frozen strategy configuration to
07:00–11:00 UTC (London) and 13:00–17:00 UTC (New York). Do not launch a new
process at every kill-zone boundary. The safe operational shape is one
supervised process with one durable state directory.

Separate cron invocations are unsafe because an open QSR position may require
eight later 15-minute bars and a Breakout position may require sixteen. Starting
and stopping only at session boundaries would miss stop, target, and time-exit
processing. Overlapping `--once` invocations would also compete for the same
ledger. The server now holds a non-blocking single-writer lock for its lifetime.

Example supervisor command (replace both absolute paths before use):

```bash
DAY_SCAN_SECONDS=60 python3 /absolute/repo/daytrader/server.py \
  --host 127.0.0.1 \
  --port 7200 \
  --state-file /absolute/durable-state/carry-day-paper.json \
  --quantrex-state-file /absolute/durable-state/quantrex-paper.json \
  --lock-file /absolute/durable-state/quantrex-paper.lock
```

The supervisor should start this command at boot and restart it only after a
non-zero exit. The runtime must use UTC. Keep the durable state directory
outside the repository checkout and back it up without modifying snapshots.

Monitor `GET http://127.0.0.1:7200/api/health`. It returns HTTP 503 when the
scanner is not `LIVE`, has never completed a scan, or its last scan is stale.
The response includes `scan_age_seconds` and `stale_after_seconds`. Alert on a
503 response, a held-lock startup failure, a ledger load/reconciliation error,
or a missing process.

No language model is in the scheduling, signal, risk, or ledger path. A low-cost
model may summarize health alerts, but it must never control timing, restart a
kill switch, change frozen parameters, or authorize an order.

This document is deployment guidance only. Do not install a supervisor, cron
entry, credential, or funded execution path until the independent review gate
explicitly permits deployment.
