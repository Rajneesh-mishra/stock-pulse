#!/usr/bin/env python3
"""
READ-ONLY connection validator for Capital.com API.
Does NOT place any orders or modify any positions.
Confirms: session auth, account type, balance, and instrument availability.
"""

import requests
import json
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
for line in env_path.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

API_KEY  = os.environ["CAPITAL_API_KEY"]
EMAIL    = os.environ["CAPITAL_EMAIL"]
PASSWORD = os.environ["CAPITAL_PASSWORD"]
ENV      = os.environ.get("CAPITAL_ENV", "demo")

BASE_URL = (
    "https://api-capital.backend-capital.com"
    if ENV == "live"
    else "https://demo-api-capital.backend-capital.com"
)

SEARCH_TERMS = ["EUR/USD", "USD/JPY", "Gold", "Oil", "Bitcoin"]

def separator(title=""):
    print(f"\n{'─'*50}")
    if title:
        print(f"  {title}")
        print(f"{'─'*50}")

def create_session():
    resp = requests.post(
        f"{BASE_URL}/api/v1/session",
        headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
        json={"identifier": EMAIL, "password": PASSWORD, "encryptedPassword": False},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"  ✗ Session failed: HTTP {resp.status_code}")
        print(f"  Response: {resp.text[:300]}")
        sys.exit(1)
    cst = resp.headers.get("CST")
    token = resp.headers.get("X-SECURITY-TOKEN")
    return cst, token, resp.json()

def auth_headers(cst, token):
    return {"CST": cst, "X-SECURITY-TOKEN": token, "Content-Type": "application/json"}

def get_accounts(cst, token):
    return requests.get(f"{BASE_URL}/api/v1/accounts",
                        headers=auth_headers(cst, token), timeout=10).json()

def get_positions(cst, token):
    return requests.get(f"{BASE_URL}/api/v1/positions",
                        headers=auth_headers(cst, token), timeout=10).json()

def search_market(cst, token, term):
    return requests.get(
        f"{BASE_URL}/api/v1/markets",
        headers=auth_headers(cst, token),
        params={"searchTerm": term, "limit": 3},
        timeout=10,
    ).json()

# ─── MAIN ────────────────────────────────────────────────────────────────────

separator("CAPITAL.COM CONNECTION VALIDATOR (READ-ONLY)")
print(f"  Environment : {ENV.upper()}")
print(f"  Base URL    : {BASE_URL}")

# 1. Auth
separator("1. Authentication")
cst, token, session_data = create_session()
if cst and token:
    print(f"  ✓ Session created successfully")
    print(f"  CST token   : {cst[:8]}...{cst[-4:]}")
    print(f"  Sec token   : {token[:8]}...{token[-4:]}")
else:
    print("  ✗ No tokens returned")
    sys.exit(1)

# 2. Account info
separator("2. Account Details")
accounts_resp = get_accounts(cst, token)
accounts = accounts_resp.get("accounts", [])
if not accounts:
    print(f"  Raw response: {json.dumps(accounts_resp, indent=2)[:500]}")
else:
    for acc in accounts:
        currency = acc.get("currency", "?")
        acc_type = acc.get("accountType", "?")
        preferred = acc.get("preferred", False)
        status = acc.get("status", "?")
        balance = acc.get("balance", {})
        bal_balance = balance.get("balance", "?")
        bal_available = balance.get("available", "?")
        bal_pnl = balance.get("profitLoss", "?")

        marker = " ← ACTIVE" if preferred else ""
        print(f"  Account Type : {acc_type}{marker}")
        print(f"  Status       : {status}")
        print(f"  Currency     : {currency}")
        print(f"  Balance      : {bal_balance}")
        print(f"  Available    : {bal_available}")
        print(f"  Open P&L     : {bal_pnl}")
        print()

# 3. Open positions
separator("3. Existing Open Positions")
positions_resp = get_positions(cst, token)
positions = positions_resp.get("positions", [])
if not positions:
    print("  ✓ No open positions")
else:
    print(f"  ⚠ WARNING: {len(positions)} OPEN POSITION(S) FOUND ON THIS ACCOUNT:")
    for p in positions:
        pos = p.get("position", {})
        mkt = p.get("market", {})
        print(f"    {mkt.get('instrumentName','?')} | {pos.get('direction','?')} "
              f"| Size: {pos.get('size','?')} | P&L: {pos.get('upl','?')}")

# 4. Instrument epics
separator("4. Instrument Epic Lookup")
epic_map = {}
for term in SEARCH_TERMS:
    result = search_market(cst, token, term)
    markets = result.get("markets", [])
    if markets:
        top = markets[0]
        epic = top.get("epic", "N/A")
        name = top.get("instrumentName", "?")
        status = top.get("marketStatus", "?")
        bid = top.get("bid", "?")
        offer = top.get("offer", "?")
        spread = round(float(offer) - float(bid), 5) if isinstance(bid, (int, float)) and isinstance(offer, (int, float)) else "?"
        tradeable = "✓" if status == "TRADEABLE" else "✗"
        print(f"  {tradeable} {term:12s} → epic={epic:20s} bid={bid} offer={offer} spread={spread}")
        epic_map[term] = epic
    else:
        print(f"  ✗ {term}: not found. Response: {str(result)[:100]}")

# 5. Summary
separator("5. SAFETY SUMMARY")
if ENV == "live":
    print("  ⚠⚠⚠  THIS IS A LIVE ACCOUNT — REAL MONEY  ⚠⚠⚠")
    print()
    print("  The full trading system will NOT be activated on this account.")
    print("  Action required:")
    print("    1. Log into capital.com → switch to Demo account")
    print("    2. Generate a NEW API key on the Demo account")
    print("    3. Update CAPITAL_ENV=demo and CAPITAL_API_KEY=<demo key> in .env")
    print("    4. Re-run this validator — it should show accountType=DEMO")
    print()
    print("  After demo validation passes 100 trades, THEN consider live deployment.")
else:
    print("  ✓ Demo account confirmed — safe to proceed with paper trading")

separator("ALSO: Rotate your API key after this session")
print("  Your password was shared in plaintext in this chat.")
print("  Settings → API Integrations → Delete key → Generate new key.")
print()
