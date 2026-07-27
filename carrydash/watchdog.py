#!/usr/bin/env python3
"""CARRY-1 watchdog — one-shot gate check for scheduled execution.

Reads the shared caches (.hist_cache.json persistence, .cost_cache.json
book costs; the dashboard server keeps them warm, and this script tops up
stale costs itself), fetches live funding, evaluates the pre-registered
gate, and prints a JSON artifact to stdout:

  {"fires": [...], "tickets": {...}, "checked_at": ..., "top": [...]}

Exit code 0 when at least one name FIRES, 1 otherwise — so schedulers can
use it as a condition. Fail-closed everywhere: unknown persistence or cost
means no fire.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")
sys.path.insert(0, str(ROOT.parent))

from k3 import data as k3data  # noqa: E402
from carry_stage0 import top_symbols  # noqa: E402
from carry_stage1 import depth_cost_bp  # noqa: E402
from server import build_ticket, THETA_FIRE_BP, HIST_CACHE, COST_CACHE  # noqa: E402

COST_MAX_AGE = 1800  # re-walk books older than 30 min


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def main():
    now = datetime.now(timezone.utc)
    try:
        universe = top_symbols()
    except Exception:
        universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

    persist = load_json(HIST_CACHE, {})
    costs = load_json(COST_CACHE, {})
    cost_age = time.time() - COST_CACHE.stat().st_mtime if COST_CACHE.exists() else 1e9

    # top up stale/missing costs for theta-tripped names only (fast path)
    names = {}
    for s in universe:
        try:
            j = k3data._get("/premiumIndex", {"symbol": s}, 15)
            names[s] = {
                "funding_bp": round(float(j.get("lastFundingRate") or 0.0) * 1e4, 2),
                "mark": float(j.get("markPrice") or 0.0),
                "index": float(j.get("indexPrice") or 0.0),
            }
        except Exception:
            continue

    tripped = [s for s, v in names.items() if v["funding_bp"] >= THETA_FIRE_BP]
    if tripped and cost_age > COST_MAX_AGE:
        for s in tripped:
            try:
                c = depth_cost_bp(s, snaps=1)
                if c is not None:
                    costs[s] = round(c, 1)
            except Exception:
                continue
        try:
            COST_CACHE.write_text(json.dumps(costs))
        except Exception:
            pass

    fires, tickets, top = [], {}, []
    for s, v in names.items():
        p = persist.get(s)
        p_med = None
        if isinstance(p, dict):
            for k in ("3.0", "2.0", "5.0"):
                if k in p:
                    p_med = p[k]["median_gross_bp"]
                    break
        cost = costs.get(s)
        v["persistence_median_bp"] = p_med
        v["cost_bp"] = cost
        v["margin_bp"] = round(p_med - 2 * cost, 1) if (p_med is not None and cost) else None
        v["basis_bp"] = round((v["mark"] / v["index"] - 1.0) * 1e4, 2) if v["index"] > 0 else None
        top.append((s, v["funding_bp"], v["margin_bp"]))
        if (v["funding_bp"] >= THETA_FIRE_BP and p_med is not None
                and cost is not None and p_med >= 2 * cost):
            fires.append(s)
            tickets[s] = build_ticket(s, v, p, now)

    top.sort(key=lambda x: -x[1])
    artifact = {
        "checked_at": now.isoformat(),
        "fires": fires,
        "tickets": tickets,
        "top": [{"symbol": s, "funding_bp": f, "margin_bp": m} for s, f, m in top[:8]],
        "gate": f"funding >= {THETA_FIRE_BP}bp AND persistence_median >= 2x measured cost",
    }
    print(json.dumps(artifact))
    sys.exit(0 if fires else 1)


if __name__ == "__main__":
    main()
