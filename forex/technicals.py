#!/usr/bin/env python3
"""
Technical analysis calculator using Capital.com candle data.
Calculates RSI, EMA, ATR, market structure for the 7-gate framework.

Usage:
  python3 forex/technicals.py USDJPY        # Full analysis (4H default)
  python3 forex/technicals.py GOLD HOUR     # 1H analysis
  python3 forex/technicals.py EURUSD DAY    # Daily analysis
"""

import json, sys, subprocess

def get_candles(epic, resolution="HOUR_4", count=200):
    """Fetch candles from Capital.com API."""
    result = subprocess.run(
        ["python3", "forex/api.py", "history", epic, str(count), resolution],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        # Try alternate resolution names
        alt_map = {"HOUR_4": "HOUR", "DAY": "DAY", "HOUR": "HOUR"}
        if resolution in alt_map:
            result = subprocess.run(
                ["python3", "forex/api.py", "history", epic, str(count), alt_map[resolution]],
                capture_output=True, text=True, timeout=30
            )
    data = json.loads(result.stdout)
    return data.get("candles", [])

def get_full_candles(epic, resolution="HOUR", count=200):
    """Get full candle data (not just last 10) by calling API directly."""
    import requests, os
    from pathlib import Path

    env_path = Path(__file__).parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

    API_KEY = os.environ["CAPITAL_API_KEY"]
    EMAIL = os.environ["CAPITAL_EMAIL"]
    PASSWORD = os.environ["CAPITAL_PASSWORD"]
    BASE = "https://demo-api-capital.backend-capital.com"

    # Create session
    r = requests.post(f"{BASE}/api/v1/session",
        headers={"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"},
        json={"identifier": EMAIL, "password": PASSWORD, "encryptedPassword": False},
        timeout=15)
    cst = r.headers.get("CST")
    tok = r.headers.get("X-SECURITY-TOKEN")

    # Get candles
    r = requests.get(f"{BASE}/api/v1/prices/{epic}",
        headers={"X-CAP-API-KEY": API_KEY, "CST": cst, "X-SECURITY-TOKEN": tok,
                 "Content-Type": "application/json"},
        params={"resolution": resolution, "max": count},
        timeout=15).json()

    candles = []
    for p in r.get("prices", []):
        candles.append({
            "time": p.get("snapshotTimeUTC"),
            "open": (p["openPrice"]["bid"] + p["openPrice"]["ask"]) / 2 if "openPrice" in p else None,
            "high": (p["highPrice"]["bid"] + p["highPrice"]["ask"]) / 2 if "highPrice" in p else None,
            "low": (p["lowPrice"]["bid"] + p["lowPrice"]["ask"]) / 2 if "lowPrice" in p else None,
            "close": (p["closePrice"]["bid"] + p["closePrice"]["ask"]) / 2 if "closePrice" in p else None,
            "volume": p.get("lastTradedVolume", 0),
        })
    return candles

def calc_rsi(closes, period=14):
    """Calculate RSI."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_ema(closes, period):
    """Calculate EMA."""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 5)

def calc_atr(candles, period=14):
    """Calculate ATR."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        if h is None or l is None or pc is None:
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 5)

def detect_structure(candles, lookback=20):
    """Detect market structure: HH/HL (bullish) or LH/LL (bearish)."""
    if len(candles) < lookback:
        return "unknown"

    recent = candles[-lookback:]
    swing_highs = []
    swing_lows = []

    for i in range(2, len(recent) - 2):
        if (recent[i]["high"] > recent[i-1]["high"] and
            recent[i]["high"] > recent[i-2]["high"] and
            recent[i]["high"] > recent[i+1]["high"] and
            recent[i]["high"] > recent[i+2]["high"]):
            swing_highs.append(recent[i]["high"])
        if (recent[i]["low"] < recent[i-1]["low"] and
            recent[i]["low"] < recent[i-2]["low"] and
            recent[i]["low"] < recent[i+1]["low"] and
            recent[i]["low"] < recent[i+2]["low"]):
            swing_lows.append(recent[i]["low"])

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]

        if hh and hl:
            return "BULLISH (HH/HL)"
        elif lh and ll:
            return "BEARISH (LH/LL)"
        elif hh and ll:
            return "EXPANDING (volatile)"
        elif lh and hl:
            return "CONTRACTING (range)"

    return "UNCLEAR"

def check_divergence(closes, period=14):
    """Check for RSI divergence on last 20 candles."""
    if len(closes) < 30:
        return "insufficient data"

    # Calculate RSI for each point
    rsi_values = []
    for i in range(period + 1, len(closes)):
        rsi = calc_rsi(closes[:i+1], period)
        if rsi:
            rsi_values.append(rsi)

    if len(rsi_values) < 10:
        return "insufficient data"

    recent_closes = closes[-10:]
    recent_rsi = rsi_values[-10:]

    price_lower = recent_closes[-1] < recent_closes[0]
    rsi_higher = recent_rsi[-1] > recent_rsi[0]
    price_higher = recent_closes[-1] > recent_closes[0]
    rsi_lower = recent_rsi[-1] < recent_rsi[0]

    if price_lower and rsi_higher:
        return "BULLISH DIVERGENCE (price lower, RSI higher)"
    elif price_higher and rsi_lower:
        return "BEARISH DIVERGENCE (price higher, RSI lower)"
    return "none"

def analyze(epic, resolution="HOUR"):
    """Full technical analysis for an instrument."""
    candles = get_full_candles(epic, resolution, 200)

    if not candles or len(candles) < 20:
        return {"error": f"Not enough candle data for {epic} ({len(candles) if candles else 0} candles)"}

    closes = [c["close"] for c in candles if c["close"] is not None]

    current = closes[-1]
    rsi = calc_rsi(closes)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    atr = calc_atr(candles)
    structure = detect_structure(candles)
    divergence = check_divergence(closes)

    # Recent high/low
    recent_20 = candles[-20:]
    recent_high = max(c["high"] for c in recent_20 if c["high"])
    recent_low = min(c["low"] for c in recent_20 if c["low"])

    # EMA position
    ema_signal = "unknown"
    if ema21 and ema50:
        if current > ema21 > ema50:
            ema_signal = "BULLISH (price > EMA21 > EMA50)"
        elif current < ema21 < ema50:
            ema_signal = "BEARISH (price < EMA21 < EMA50)"
        elif ema21 > ema50:
            ema_signal = "MIXED BULLISH (EMA21 > EMA50 but price diverging)"
        else:
            ema_signal = "MIXED BEARISH (EMA21 < EMA50)"

    result = {
        "epic": epic,
        "resolution": resolution,
        "candles_analyzed": len(candles),
        "current_price": current,
        "rsi_14": rsi,
        "rsi_signal": "overbought" if rsi and rsi > 70 else "oversold" if rsi and rsi < 30 else "neutral",
        "ema_21": ema21,
        "ema_50": ema50,
        "ema_signal": ema_signal,
        "atr_14": atr,
        "atr_pips": round(atr * 10000, 1) if atr and atr < 1 else round(atr, 2) if atr else None,
        "market_structure": structure,
        "divergence": divergence,
        "recent_20_high": recent_high,
        "recent_20_low": recent_low,
        "range_position": round((current - recent_low) / (recent_high - recent_low) * 100, 1) if recent_high != recent_low else 50,
    }
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 forex/technicals.py EPIC [RESOLUTION]")
        print("  RESOLUTION: MINUTE_15, HOUR, HOUR_4, DAY")
        sys.exit(1)

    epic = sys.argv[1].upper()
    resolution = sys.argv[2].upper() if len(sys.argv) > 2 else "HOUR"

    result = analyze(epic, resolution)
    print(json.dumps(result, indent=2))
