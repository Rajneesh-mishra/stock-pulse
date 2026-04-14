#!/usr/bin/env python3
"""
Demo account setup: top up balance + find correct instrument epics.
"""

import requests, json, os
from pathlib import Path

env_path = Path(__file__).parent.parent / ".env"
for line in env_path.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

API_KEY  = os.environ["CAPITAL_API_KEY"]
EMAIL    = os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]
BASE_URL = "https://demo-api-capital.backend-capital.com"

def session():
    r = requests.post(f"{BASE_URL}/api/v1/session",
        headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
        json={"identifier": EMAIL, "password": PASSWORD, "encryptedPassword": False},
        timeout=10)
    return r.headers["CST"], r.headers["X-SECURITY-TOKEN"]

def hdrs(cst, tok):
    return {"CST": cst, "X-SECURITY-TOKEN": tok, "Content-Type": "application/json"}

cst, tok = session()
h = hdrs(cst, tok)

# ── 1. Top up balance ────────────────────────────────────────────────────────
print("1. Topping up demo balance to $10,000...")
current = requests.get(f"{BASE_URL}/api/v1/accounts", headers=h, timeout=10).json()
current_balance = current["accounts"][0]["balance"]["balance"]
add_amount = max(0, 10000 - current_balance)

if add_amount > 0:
    r = requests.post(f"{BASE_URL}/api/v1/accounts/topUp",
        headers=h, json={"amount": add_amount}, timeout=10)
    print(f"   Added ${add_amount:.2f} → response: {r.status_code}")
else:
    print(f"   Already at ${current_balance:.2f}, no top-up needed")

# Verify new balance
new_bal = requests.get(f"{BASE_URL}/api/v1/accounts", headers=h, timeout=10).json()
print(f"   New balance: ${new_bal['accounts'][0]['balance']['balance']:.2f}")

# ── 2. Find correct spot epics ───────────────────────────────────────────────
print("\n2. Searching for correct spot instrument epics...")

searches = {
    "EUR/USD":  ["EUR/USD", "EURUSD", "Euro Dollar"],
    "USD/JPY":  ["USD/JPY", "USDJPY", "Dollar Yen"],
    "Gold":     ["Gold", "XAUUSD", "XAU/USD"],
    "WTI Oil":  ["Oil - Crude", "WTI", "OIL_CRUDE"],
    "Bitcoin":  ["Bitcoin", "BTC/USD", "BTCUSD"],
}

epic_results = {}
for instrument, terms in searches.items():
    print(f"\n   {instrument}:")
    for term in terms:
        r = requests.get(f"{BASE_URL}/api/v1/markets",
            headers=h, params={"searchTerm": term, "limit": 5}, timeout=10)
        markets = r.json().get("markets", [])
        for m in markets:
            epic = m.get("epic", "")
            name = m.get("instrumentName", "")
            status = m.get("marketStatus", "")
            bid = m.get("bid", "?")
            offer = m.get("offer", "?")
            itype = m.get("instrumentType", "")
            spread = round(float(offer) - float(bid), 5) if isinstance(bid, (int,float)) and isinstance(offer, (int,float)) else "?"
            tradeable = "✓" if status == "TRADEABLE" else "~"
            print(f"     {tradeable} {epic:25s} | {name:35s} | spread={spread} | {itype}")
        if markets:
            break  # Found results, stop trying alternative terms

# ── 3. Print recommended config ──────────────────────────────────────────────
print("\n3. Recommended INSTRUMENTS config for forex/config.py:")
print("""
INSTRUMENTS = {
    # Update epics below based on search results above
    # Pick the TRADEABLE spot CFD with the tightest spread
    "EURUSD":    {"epic": "EURUSD",     "pip_size": 0.0001, "margin_rate": 0.0333},
    "USDJPY":    {"epic": "USDJPY",     "pip_size": 0.01,   "margin_rate": 0.0333},
    "GOLD":      {"epic": "GOLD",       "pip_size": 0.1,    "margin_rate": 0.05  },
    "OIL_CRUDE": {"epic": "OIL_CRUDE",  "pip_size": 0.01,   "margin_rate": 0.05  },
    "BTCUSD":    {"epic": "BTCUSD",     "pip_size": 1.0,    "margin_rate": 0.50  },
}
""")
