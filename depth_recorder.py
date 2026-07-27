#!/usr/bin/env python3
"""CARRY-1 liquidity-wall recorder — measurement only, context only.

Runs on a 5-minute cadence (Blueprint Automation). For the top-20 USDT perps by
24h quote volume it snapshots the top-100 L2 book from Binance public API and:

  1. Appends the RAW book to depth_history/YYYY-MM-DD.jsonl  (backtest dataset)
  2. Rewrites repo-staging/web/walls.json with the top-5 walls per side within
     ±2% of mid, carrying persistence counters (snapshots, first_seen) across runs.
  3. Best-effort git push of walls.json at most once per hour (dashboard persist
     layer is slow-moving by design; the live view is client-side anyway).
  4. Gzips depth_history files older than 2 days.

DOCTRINE / HONESTY (do not remove):
- 5-min snapshots can measure wall PERSISTENCE at 15m–1h horizons. They CANNOT
  see sub-second spoof-pulls. No bookmap-style microstructure claim is made.
- Walls are CONTEXT. Nothing here enters the CARRY-1 gate or any signal path.
- Visible wall != intent. The pre-registered wall-persistence/absorption test
  runs only after ~6+ weeks of accrued snapshots, under the standard protocol
  (bootstrap null, measured cost floor, negatives published).
"""
import json, os, subprocess, sys, time, gzip, glob, urllib.request
from datetime import datetime, timezone

_here = os.path.dirname(os.path.abspath(__file__))
# Under Blueprint Automation the entry is materialized in the Automation's own
# workspace; fall back to the canonical project root in that case.
ROOT = _here if os.path.isdir(os.path.join(_here, "repo-staging", "web")) \
    else "/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry"
HIST_DIR = os.path.join(ROOT, "depth_history")
WEB = os.path.join(ROOT, "repo-staging", "web")
WALLS_JSON = os.path.join(WEB, "walls.json")
STATE = os.path.join(ROOT, ".depth_state.json")
FAPI = "https://fapi.binance.com/fapi/v1"
STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "EUR", "GBP", "AEUR", "XUSD", "USD1"}
RANGE = 0.02          # ±2% of mid
MATCH_TOL = 8e-4      # same-wall tolerance, 8bp
PUSH_EVERY_SEC = 3600


def get(path):
    req = urllib.request.Request(FAPI + path, headers={"User-Agent": "carry1-recorder/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def universe():
    info = get("/exchangeInfo")
    trad = {s["symbol"] for s in info.get("symbols", [])
            if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT" and s.get("baseAsset") not in STABLES}
    ticks = get("/ticker/24hr")
    rows = sorted(((t["symbol"], float(t.get("quoteVolume", 0))) for t in ticks if t["symbol"] in trad),
                  key=lambda x: -x[1])
    return [s for s, _ in rows[:20]]


def atr14_1h(sym):
    try:
        ks = get(f"/klines?symbol={sym}&interval=1h&limit=60")
        trs = []
        for i in range(1, len(ks)):
            h, l, pc = float(ks[i][2]), float(ks[i][3]), float(ks[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-14:]) / 14 if len(trs) >= 14 else None
    except Exception:
        return None


def walls_from_book(bids, asks):
    """bids/asks: [[price, qty], ...] best-first. Returns (mid, bidwalls, askwalls)."""
    if not bids or not asks:
        return None, [], []
    mid = (bids[0][0] + asks[0][0]) / 2
    def top(lv, side):
        out = []
        for p, q in lv:
            dist = (mid - p) / mid if side == "bid" else (p - mid) / mid
            if dist < 0 or dist > RANGE:
                continue
            out.append({"price": p, "usd": round(p * q), "dist_bp": round(dist * 1e4, 1), "side": side})
        out.sort(key=lambda w: -w["usd"])
        return out[:5]
    return mid, top(bids, "bid"), top(asks, "ask")


def merge_persistence(new_walls, old_walls, now_iso):
    for w in new_walls:
        match = None
        for ow in old_walls or []:
            if ow["side"] == w["side"] and abs(ow["price"] - w["price"]) / max(w["price"], 1e-12) <= MATCH_TOL:
                if match is None or ow["usd"] > match["usd"]:
                    match = ow
        if match:
            w["snapshots"] = match.get("snapshots", 1) + 1
            w["first_seen"] = match.get("first_seen", now_iso)
        else:
            w["snapshots"] = 1
            w["first_seen"] = now_iso
    return new_walls


def rotate_history():
    cutoff = time.time() - 2 * 86400
    for f in glob.glob(os.path.join(HIST_DIR, "*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                with open(f, "rb") as fi, gzip.open(f + ".gz", "wb") as fo:
                    fo.writelines(fi)
                os.remove(f)
        except Exception:
            pass


def maybe_push(state):
    last = state.get("last_push", 0)
    if time.time() - last < PUSH_EVERY_SEC:
        return "skipped (<1h since last)"
    try:
        r = subprocess.run(
            ["git", "add", "web/walls.json"], cwd=os.path.join(ROOT, "repo-staging"),
            capture_output=True, timeout=30)
        subprocess.run(["git", "-c", "user.name=kimi-k3", "-c", "user.email=kimi@localhost",
                        "commit", "-qm", "walls.json hourly persistence update"],
                       cwd=os.path.join(ROOT, "repo-staging"), capture_output=True, timeout=30)
        p = subprocess.run(["git", "push", "-q"], cwd=os.path.join(ROOT, "repo-staging"),
                           capture_output=True, timeout=60)
        if p.returncode == 0:
            state["last_push"] = time.time()
            return "pushed"
        return "push failed: " + p.stderr.decode()[:200]
    except Exception as e:
        return f"push error: {e}"


def collect():
    os.makedirs(HIST_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except Exception:
            pass
    prev = {}
    if os.path.exists(WALLS_JSON):
        try:
            prev = json.load(open(WALLS_JSON)).get("symbols", {})
        except Exception:
            pass

    try:
        syms = universe()
    except Exception as e:
        return {"ok": False, "symbols": 0, "generated_at": now_iso, "error": f"universe: {e}"}

    day_file = os.path.join(HIST_DIR, now.strftime("%Y-%m-%d") + ".jsonl")
    out, n_snaps = {"generated_at": now_iso, "cadence_sec": 300,
                    "note": ("CONTEXT - UNVALIDATED. 5-min L2 snapshots measure wall PERSISTENCE only; "
                             "sub-second spoof-pulls are invisible at this resolution. Walls never enter "
                             "the CARRY-1 gate. Dataset accrues toward a pre-registered backtest (~6 wks)."),
                    "symbols": {}}, 0
    with open(day_file, "a") as fh:
        for s in syms:
            try:
                d = get(f"/depth?symbol={s}&limit=500")
                bids = [[float(p), float(q)] for p, q in d.get("bids", [])]
                asks = [[float(p), float(q)] for p, q in d.get("asks", [])]
                fh.write(json.dumps({"ts": int(time.time() * 1000), "s": s,
                                     "b": bids, "a": asks}, separators=(",", ":")) + "\n")
                n_snaps += 1
                mid, bw, aw = walls_from_book(bids, asks)
                if mid is None:
                    continue
                old = prev.get(s, {})
                bw = merge_persistence(bw, old.get("bids"), now_iso)
                aw = merge_persistence(aw, old.get("asks"), now_iso)
                atr = atr14_1h(s)
                out["symbols"][s] = {"mid": mid, "atr14_1h": atr, "bids": bw, "asks": aw}
                time.sleep(0.15)
            except Exception:
                continue

    tmp = WALLS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, WALLS_JSON)

    rotate_history()
    push_res = maybe_push(state)
    with open(STATE, "w") as f:
        json.dump(state, f)

    result = {"ok": True, "symbols": n_snaps, "history_file": os.path.basename(day_file),
              "push": push_res, "generated_at": now_iso}
    return result


def run(ctx):
    """Managed Blueprint runner entrypoint — returns the AutomationOutput payload."""
    return {"artifact": collect()}


if __name__ == "__main__":
    print(json.dumps({"artifact": collect()}))
