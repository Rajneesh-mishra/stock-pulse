#!/usr/bin/env python3
"""
Capital.com API — thin CLI wrapper. Zero trading logic.
Claude is the brain. This is just the remote control.

Usage:
  python3 forex/api.py account          # Balance, margin, P&L
  python3 forex/api.py positions        # All open positions
  python3 forex/api.py prices           # Live bid/ask for all instruments
  python3 forex/api.py price GOLD       # Single instrument price
  python3 forex/api.py history GOLD 200 # 200 hourly candles
  python3 forex/api.py open GOLD BUY 0.01 4760 4900   # Open with SL + TP
  python3 forex/api.py close <dealId>                  # Close position
  python3 forex/api.py modify <dealId> 4780 4920       # Move SL + TP
  python3 forex/api.py search "EUR/USD"                # Find instrument epics
"""

import requests, json, sys, os
from pathlib import Path
from datetime import datetime, timezone

# ── Load .env ────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
for line in env_path.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

API_KEY  = os.environ["CAPITAL_API_KEY"]
EMAIL    = os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]
ENV      = os.environ.get("CAPITAL_ENV", "demo")

BASE = ("https://api-capital.backend-capital.com" if ENV == "live"
        else "https://demo-api-capital.backend-capital.com")

INSTRUMENTS = {
    "EURUSD":    {"epic": "EURUSD",    "name": "EUR/USD",     "type": "forex"},
    "USDJPY":    {"epic": "USDJPY",    "name": "USD/JPY",     "type": "forex"},
    "GOLD":      {"epic": "GOLD",      "name": "Gold",        "type": "commodity"},
    "OIL_CRUDE": {"epic": "OIL_CRUDE", "name": "WTI Crude",   "type": "commodity"},
    "BTCUSD":    {"epic": "BTCUSD",    "name": "Bitcoin/USD",  "type": "crypto"},
}

# ── Session ──────────────────────────────────────────────────────────────────
def create_session():
    r = requests.post(f"{BASE}/api/v1/session",
        headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
        json={"identifier": EMAIL, "password": PASSWORD, "encryptedPassword": False},
        timeout=15)
    if r.status_code != 200:
        print(json.dumps({"error": f"Session failed: {r.status_code}", "body": r.text[:300]}))
        sys.exit(1)
    return r.headers["CST"], r.headers["X-SECURITY-TOKEN"]

def h(cst, tok):
    return {"CST": cst, "X-SECURITY-TOKEN": tok, "Content-Type": "application/json"}

# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_account(cst, tok):
    r = requests.get(f"{BASE}/api/v1/accounts", headers=h(cst,tok), timeout=10).json()
    acc = r["accounts"][0]
    bal = acc["balance"]
    out = {
        "account_type": acc.get("accountType"),
        "currency": acc.get("currency"),
        "status": acc.get("status"),
        "balance": bal.get("balance"),
        "available": bal.get("available"),
        "deposit": bal.get("deposit"),
        "profit_loss": bal.get("profitLoss"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(out, indent=2))

def cmd_positions(cst, tok):
    r = requests.get(f"{BASE}/api/v1/positions", headers=h(cst,tok), timeout=10).json()
    positions = []
    for p in r.get("positions", []):
        pos = p.get("position", {})
        mkt = p.get("market", {})
        positions.append({
            "dealId": pos.get("dealId"),
            "epic": mkt.get("epic"),
            "name": mkt.get("instrumentName"),
            "direction": pos.get("direction"),
            "size": pos.get("size"),
            "level": pos.get("level"),          # entry price
            "stopLevel": pos.get("stopLevel"),
            "profitLevel": pos.get("profitLevel"),
            "upl": pos.get("upl"),              # unrealised P&L
            "bid": mkt.get("bid"),
            "offer": mkt.get("offer"),
            "createdDateUTC": pos.get("createdDateUTC"),
        })
    print(json.dumps({"count": len(positions), "positions": positions}, indent=2))

def cmd_prices(cst, tok, epic=None):
    epics = [epic] if epic else [v["epic"] for v in INSTRUMENTS.values()]
    prices = []
    for ep in epics:
        r = requests.get(f"{BASE}/api/v1/markets/{ep}",
            headers=h(cst,tok), timeout=10)
        if r.status_code != 200:
            prices.append({"epic": ep, "error": f"HTTP {r.status_code}"})
            continue
        m = r.json()
        snap = m.get("snapshot", {})
        inst = m.get("instrument", {})
        prices.append({
            "epic": ep,
            "name": inst.get("name"),
            "bid": snap.get("bid"),
            "offer": snap.get("offer"),
            "spread": round(snap.get("offer",0) - snap.get("bid",0), 6) if snap.get("bid") else None,
            "high": snap.get("high"),
            "low": snap.get("low"),
            "change_pct": snap.get("percentageChange"),
            "status": snap.get("marketStatus"),
            "update_time": snap.get("updateTime"),
            "min_size": inst.get("minDealSize"),
            "max_size": inst.get("maxDealSize"),
            "margin_factor": inst.get("marginFactor"),
            "margin_factor_unit": inst.get("marginFactorUnit"),
        })
    print(json.dumps({"prices": prices}, indent=2))

def cmd_history(cst, tok, epic, count=200, resolution="HOUR"):
    r = requests.get(f"{BASE}/api/v1/prices/{epic}",
        headers=h(cst,tok), params={"resolution": resolution, "max": count}, timeout=15).json()
    candles = []
    for p in r.get("prices", []):
        candles.append({
            "time": p.get("snapshotTimeUTC"),
            "open": p["openPrice"]["mid"] if "openPrice" in p else None,
            "high": p["highPrice"]["mid"] if "highPrice" in p else None,
            "low": p["lowPrice"]["mid"] if "lowPrice" in p else None,
            "close": p["closePrice"]["mid"] if "closePrice" in p else None,
            "volume": p.get("lastTradedVolume"),
        })
    print(json.dumps({"epic": epic, "resolution": resolution, "count": len(candles),
                       "candles": candles[-10:]}, indent=2))  # Print last 10 for brevity

def cmd_open(cst, tok, epic, direction, size, sl, tp):
    """Open position. SL and TP are MANDATORY — risk_guard enforced."""
    size = float(size)
    sl = float(sl)
    tp = float(tp)

    payload = {
        "epic": epic,
        "direction": direction.upper(),
        "size": size,
        "orderType": "MARKET",
        "stopLevel": sl,
        "profitLevel": tp,
        "guaranteedStop": False,
    }
    r = requests.post(f"{BASE}/api/v1/positions",
        headers=h(cst,tok), json=payload, timeout=15)

    result = r.json()
    result["http_status"] = r.status_code
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(result, indent=2))

    # Verify position actually exists
    if r.status_code == 200:
        import time; time.sleep(1)
        pos_r = requests.get(f"{BASE}/api/v1/positions", headers=h(cst,tok), timeout=10).json()
        found = any(p["position"]["dealId"] == result.get("dealId")
                     for p in pos_r.get("positions", []))
        if found:
            print(f'\n{{"verified": true, "dealId": "{result.get("dealId")}"}}')
        else:
            print(f'\n{{"verified": false, "warning": "Position not found after placement"}}')

def cmd_close(cst, tok, deal_id):
    r = requests.delete(f"{BASE}/api/v1/positions/{deal_id}",
        headers=h(cst,tok), timeout=15)
    result = r.json() if r.text else {}
    result["http_status"] = r.status_code
    print(json.dumps(result, indent=2))

def cmd_modify(cst, tok, deal_id, sl, tp):
    payload = {"stopLevel": float(sl), "profitLevel": float(tp)}
    r = requests.put(f"{BASE}/api/v1/positions/{deal_id}",
        headers=h(cst,tok), json=payload, timeout=15)
    result = r.json() if r.text else {}
    result["http_status"] = r.status_code
    print(json.dumps(result, indent=2))

def cmd_search(cst, tok, term):
    r = requests.get(f"{BASE}/api/v1/markets",
        headers=h(cst,tok), params={"searchTerm": term, "limit": 8}, timeout=10).json()
    results = []
    for m in r.get("markets", []):
        results.append({
            "epic": m.get("epic"),
            "name": m.get("instrumentName"),
            "type": m.get("instrumentType"),
            "status": m.get("marketStatus"),
            "bid": m.get("bid"),
            "offer": m.get("offer"),
        })
    print(json.dumps({"query": term, "results": results}, indent=2))

# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    cst, tok = create_session()

    if cmd == "account":
        cmd_account(cst, tok)
    elif cmd == "positions":
        cmd_positions(cst, tok)
    elif cmd == "prices":
        epic = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_prices(cst, tok, epic)
    elif cmd == "price":
        cmd_prices(cst, tok, sys.argv[2])
    elif cmd == "history":
        epic = sys.argv[2]
        count = int(sys.argv[3]) if len(sys.argv) > 3 else 200
        cmd_history(cst, tok, epic, count)
    elif cmd == "open":
        if len(sys.argv) < 7:
            print("Usage: api.py open <epic> <BUY|SELL> <size> <stopLevel> <profitLevel>")
            sys.exit(1)
        cmd_open(cst, tok, sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    elif cmd == "close":
        cmd_close(cst, tok, sys.argv[2])
    elif cmd == "modify":
        if len(sys.argv) < 5:
            print("Usage: api.py modify <dealId> <newSL> <newTP>")
            sys.exit(1)
        cmd_modify(cst, tok, sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "search":
        cmd_search(cst, tok, " ".join(sys.argv[2:]))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
