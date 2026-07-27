#!/usr/bin/env python3
"""CARRY-1 Live Scanner — honest signal gate + day-trading context panel.

Serves a world-class dark dashboard (index.html) and a /api/state JSON feed.

THE GATE (pre-registered, fail-closed): a name FIRES only when
    last_funding >= theta  AND  persistence_median(theta) >= 2 * live_cost
where persistence_median is the measured median gross carry at that theta
from the name's own funding history (carry_stage1 method), and live_cost is
a real order-book walk. If history or depth is unavailable the gate stays
silent. Silence is the honest output — see RESEARCH.md.

Stdlib only. Run: python3 server.py [--port 7100]
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")
sys.path.insert(0, str(ROOT.parent))  # carry_stage0/carry_stage1 helpers

from k3 import data as k3data  # noqa: E402
from carry_stage1 import depth_cost_bp, funding_history_paged  # noqa: E402
from carry_stage0 import simulate_trades, top_symbols  # noqa: E402

THETA_FIRE_BP = 3.0          # the stronger of the two Stage-0 survivors
COST_REFRESH_S = 600         # per-name book walk cadence (round-robin)
STATE_TTL_S = 60             # funding/mark refresh cadence
HIST_CACHE = ROOT / ".hist_cache.json"
COST_CACHE = ROOT / ".cost_cache.json"
HIST_TTL = 6 * 3600
ALERTS = ROOT / "alerts.jsonl"

KZ_BLOCKS = [  # the measured mask: 4h blocks aligned to rebalance boundaries
    (0, "ASIA", "Tokyo open — measured KZ member"),
    (8, "LONDON", "London open — measured KZ member"),
    (12, "NEW YORK", "NY open — measured KZ member"),
]

STATE = {
    "ts": None, "names": {}, "dispersion": None, "kz": None,
    "fires": [], "tickets": {}, "universe": [], "errors": [],
}
LOCK = threading.Lock()


def build_ticket(sym, info, persist_rec, now):
    """Full trade ticket for a FIRE: Entry / SL / TP1-2-3.

    Honest ladder for a HEDGED carry trade — the TPs are defined on funding
    collected (bp of notional), not on fake price targets:
      TP1  cost recovered      (breakeven: carry = measured round-trip cost)
      TP2  persistence median  (the measured expectation at this theta)
      TP3  persistence p75     (stretch)
      SL1  funding flip        (exit at first epoch printing <= 0)
      SL2  basis stop          (adverse basis move >= full round-trip cost)
      HARD FLAT day 5          (no position survives the measured window cap)
    Epoch ETAs come from the name's own funding-interval length.
    """
    from datetime import timedelta
    f = info["funding_bp"]           # bp per epoch, last print
    c = info["cost_bp"]              # measured round trip, both legs
    epoch_h = (persist_rec or {}).get("epoch_h", 8.0) or 8.0
    th_key = next((k for k in ("3.0", "2.0", "5.0") if k in (persist_rec or {})), None)
    th_stats = (persist_rec or {}).get(th_key, {}) if th_key else {}
    p_med = th_stats.get("median_gross_bp")
    p75 = th_stats.get("p75_gross_bp")
    max_epochs = max(1, int(5 * 24 / epoch_h))

    def leg(bp_target):
        if bp_target is None or f <= 0:
            return None
        ep = min(max_epochs, max(1, int(-(-bp_target // f))))  # ceil
        return {"bp": round(bp_target, 1), "epochs": ep,
                "eta": (now + timedelta(hours=ep * epoch_h)).isoformat()}

    return {
        "symbol": sym,
        "side": "SHORT perp + delta hedge",
        "entry_mark": info["mark"],
        "entry_index": info.get("index"),
        "entry_basis_bp": info.get("basis_bp"),
        "funding_per_epoch_bp": f,
        "epoch_hours": epoch_h,
        "cost_bp": c,
        "tp1": leg(c),        # breakeven
        "tp2": leg(p_med),    # measured expectation
        "tp3": leg(p75),      # stretch
        "sl_funding_flip": "exit at first epoch printing <= 0 bp",
        "sl_basis_bp": c,
        "hard_flat": (now + timedelta(days=5)).isoformat(),
        "max_epochs": max_epochs,
    }


def kz_state(now):
    h = now.hour
    cur = None
    for start, name, desc in KZ_BLOCKS:
        if start <= h < start + 4:
            cur = (start, name, desc)
            break
    nxt = min([s for s, _, _ in KZ_BLOCKS if s > h] or [KZ_BLOCKS[0][0] + 24])
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    next_boundary = base + timedelta(hours=nxt)
    if cur:
        start, name, desc = cur
        prog = (h - start + now.minute / 60 + now.second / 3600) / 4.0
        return {"in_zone": True, "name": name, "desc": desc,
                "progress": round(prog, 3), "ends_in_min": int((4 - prog * 4) * 60),
                "next_boundary_in_min": int((next_boundary - now).total_seconds() // 60)}
    return {"in_zone": False, "name": "OFF-ZONE", "desc": "between measured kill zones — half size / no fire context",
            "progress": 0.0, "ends_in_min": 0,
            "next_boundary_in_min": int((next_boundary - now).total_seconds() // 60)}


def load_persistence(universe):
    """Median gross carry per name at theta buckets, from own history.
    Fail-closed: missing history -> None -> gate cannot fire."""
    cache = {}
    if HIST_CACHE.exists() and time.time() - HIST_CACHE.stat().st_mtime < HIST_TTL:
        try:
            cache = json.loads(HIST_CACHE.read_text())
        except Exception:
            cache = {}
    out = {}
    dirty = False
    for s in universe:
        cached = cache.get(s)
        if isinstance(cached, dict) and "epoch_h" in cached:
            out[s] = cached
            continue
        try:
            df = funding_history_paged(s, years=1)
            if df is None:
                out[s] = None
                continue
            epoch_h = float(df["t"].diff().dt.total_seconds().median() / 3600.0) if len(df) > 2 else 8.0
            rec = {"epoch_h": round(epoch_h, 2)}
            for th in (2.0, 3.0, 5.0):
                tr = simulate_trades(df, th)
                if len(tr) >= 5:
                    g = sorted(t["gross_bp"] for t in tr)
                    rec[str(th)] = {"n": len(tr),
                                    "median_gross_bp": g[len(g) // 2],
                                    "p75_gross_bp": g[int(len(g) * 0.75)]}
            out[s] = rec if len(rec) > 1 else None
        except Exception:
            out[s] = None
        dirty = True
        time.sleep(0.3)
    if dirty:
        try:
            HIST_CACHE.write_text(json.dumps(out))
        except Exception:
            pass
    return out


def poll_loop():
    universe = []
    persist = {}
    costs = {}
    cost_idx = 0
    last_cost_ts = 0.0
    fired_sig = set()
    while True:
        try:
            if not universe:
                try:
                    universe = top_symbols()
                except Exception:
                    universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
                persist = load_persistence(universe)
                with LOCK:
                    STATE["universe"] = universe
            now = datetime.now(timezone.utc)
            names = {}
            for s in universe:
                try:
                    j = k3data._get("/premiumIndex", {"symbol": s}, 15)
                    last_fr = float(j.get("lastFundingRate") or 0.0) * 1e4
                    mark = float(j.get("markPrice") or 0.0)
                    index = float(j.get("indexPrice") or 0.0)
                    nft = int(j.get("nextFundingTime") or 0)
                except Exception:
                    continue
                p = persist.get(s)
                p_med = None
                if p:
                    for th_key in ("3.0", "2.0", "5.0"):
                        if th_key in p:
                            p_med = p[th_key]["median_gross_bp"]
                            break
                cost = costs.get(s)
                fire = False
                reason = []
                if last_fr >= THETA_FIRE_BP:
                    reason.append(f"funding {last_fr:.1f}bp ≥ θ {THETA_FIRE_BP:.0f}bp")
                    if p_med is None:
                        reason.append("persistence unknown → fail-closed")
                    elif cost is None:
                        reason.append("cost not yet measured → fail-closed")
                    elif p_med >= 2 * cost:
                        fire = True
                        reason.append(f"persistence {p_med:.0f}bp ≥ 2× cost {cost:.0f}bp ✓")
                    else:
                        reason.append(f"persistence {p_med:.0f}bp < 2× cost {2*cost:.0f}bp")
                basis_bp = round((mark / index - 1.0) * 1e4, 2) if index > 0 else None
                names[s] = {
                    "funding_bp": round(last_fr, 2), "mark": mark, "index": index,
                    "basis_bp": basis_bp,
                    "next_funding_in_min": max(0, int((nft / 1000 - time.time()) // 60)),
                    "persistence_median_bp": p_med, "cost_bp": cost,
                    "margin_bp": (round(p_med - 2 * cost, 1) if (p_med is not None and cost) else None),
                    "fire": fire, "gate_log": reason,
                }
            # round-robin book walks: 2 names per cycle, full sweep ~10 min
            if time.time() - last_cost_ts > 30 and universe:
                for _ in range(2):
                    s = universe[cost_idx % len(universe)]
                    cost_idx += 1
                    try:
                        c = depth_cost_bp(s, snaps=1)
                        if c is not None:
                            costs[s] = round(c, 1)
                    except Exception:
                        pass
                last_cost_ts = time.time()
                try:
                    COST_CACHE.write_text(json.dumps(costs))
                except Exception:
                    pass
            # alert on new fires + build trade tickets
            new_fires = {s for s, v in names.items() if v["fire"]}
            tickets = {}
            for s in new_fires:
                tickets[s] = build_ticket(s, names[s], persist.get(s), now)
            for s in new_fires - fired_sig:
                rec = {"ts": now.isoformat(), "symbol": s,
                       "funding_bp": names[s]["funding_bp"],
                       "margin_bp": names[s]["margin_bp"],
                       "ticket": tickets.get(s)}
                with open(ALERTS, "a") as f:
                    f.write(json.dumps(rec) + "\n")
            fired_sig = new_fires
            with LOCK:
                STATE["ts"] = now.isoformat()
                STATE["names"] = names
                STATE["kz"] = kz_state(now)
                STATE["fires"] = sorted(new_fires)
                STATE["tickets"] = tickets
        except Exception as e:
            with LOCK:
                STATE["errors"] = [f"{type(e).__name__}: {e}"] + STATE["errors"][:4]
        time.sleep(STATE_TTL_S)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html", code=200):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            with LOCK:
                self._send(json.dumps(STATE), "application/json")
        elif self.path.startswith("/api/alerts"):
            lines = ALERTS.read_text().strip().split("\n")[-50:] if ALERTS.exists() else []
            self._send(json.dumps([json.loads(x) for x in lines if x]), "application/json")
        else:
            self._send((ROOT / "index.html").read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7100)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    a = ap.parse_args()
    threading.Thread(target=poll_loop, daemon=True).start()
    print(f"CARRY-1 scanner live → http://{a.host}:{a.port}/  (gate: θ≥{THETA_FIRE_BP:.0f}bp & persistence ≥ 2× measured cost; fail-closed)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
