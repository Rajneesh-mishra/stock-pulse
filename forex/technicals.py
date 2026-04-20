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

# Optional SMC dependency — degrade gracefully if missing.
# smc prints a thank-you banner on import; silence it so JSON output stays clean.
try:
    import contextlib, io
    import pandas as pd
    with contextlib.redirect_stdout(io.StringIO()):
        from smartmoneyconcepts import smc
    SMC_AVAILABLE = True
except ImportError:
    SMC_AVAILABLE = False

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

_SESSION_CACHE = {"cst": None, "tok": None, "api_key": None, "base": None}

# Capital.com sessions live 10 minutes. Cache on disk so multiple scripts
# (confluence, watcher, Claude's ticks) share the same token and avoid 429.
from pathlib import Path as _Path
_SESSION_FILE = _Path(__file__).parent.parent / "state" / ".capital_session.json"
_SESSION_TTL_SEC = 480  # 8 min — refresh before the 10-min server expiry


def _load_env():
    import os
    env_path = _Path(__file__).parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()


def _ensure_session():
    """Get-or-create a Capital.com session with disk cache (8-min TTL).
    Survives across separate Python process invocations."""
    import requests, os, json, time

    if _SESSION_CACHE["cst"] and _SESSION_CACHE["tok"]:
        return _SESSION_CACHE

    _load_env()
    api_key = os.environ["CAPITAL_API_KEY"]
    email = os.environ["CAPITAL_EMAIL"]
    password = os.environ["CAPITAL_PASSWORD"]
    base = ("https://api-capital.backend-capital.com"
            if os.environ.get("CAPITAL_ENV") == "live"
            else "https://demo-api-capital.backend-capital.com")

    # Try disk cache first
    if _SESSION_FILE.exists():
        try:
            cached = json.loads(_SESSION_FILE.read_text())
            if (time.time() - cached.get("ts", 0) < _SESSION_TTL_SEC
                    and cached.get("base") == base):
                _SESSION_CACHE.update({
                    "cst": cached["cst"], "tok": cached["tok"],
                    "api_key": api_key, "base": base,
                })
                return _SESSION_CACHE
        except Exception:
            pass

    # Create fresh session with retry on 429
    for attempt in range(4):
        r = requests.post(f"{base}/api/v1/session",
            headers={"X-CAP-API-KEY": api_key, "Content-Type": "application/json"},
            json={"identifier": email, "password": password, "encryptedPassword": False},
            timeout=15)
        if r.status_code == 200:
            break
        if r.status_code == 429:
            time.sleep(2 + attempt * 2)
            continue
        r.raise_for_status()
    r.raise_for_status()

    cst, tok = r.headers["CST"], r.headers["X-SECURITY-TOKEN"]
    _SESSION_CACHE.update({"cst": cst, "tok": tok, "api_key": api_key, "base": base})
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps({
            "cst": cst, "tok": tok, "base": base, "ts": time.time(),
        }))
        _SESSION_FILE.chmod(0o600)
    except Exception:
        pass
    return _SESSION_CACHE


def get_full_candles(epic, resolution="HOUR", count=200):
    """Fetch candles using the cached session (safe for many sequential calls)."""
    import requests, time

    s = _ensure_session()
    for attempt in range(3):
        r = requests.get(f"{s['base']}/api/v1/prices/{epic}",
            headers={"X-CAP-API-KEY": s["api_key"], "CST": s["cst"],
                     "X-SECURITY-TOKEN": s["tok"], "Content-Type": "application/json"},
            params={"resolution": resolution, "max": count}, timeout=15)
        if r.status_code == 429:
            time.sleep(1 + attempt)
            continue
        if r.status_code == 401:
            # Session died — reset (disk + memory) and retry once
            _SESSION_CACHE.update({"cst": None, "tok": None})
            try:
                _SESSION_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            s = _ensure_session()
            continue
        break

    if r.status_code != 200:
        return []

    data = r.json()
    candles = []
    for p in data.get("prices", []):
        if "openPrice" not in p or "closePrice" not in p:
            continue
        op, hp, lp, cp = p["openPrice"], p["highPrice"], p["lowPrice"], p["closePrice"]
        candles.append({
            "time": p.get("snapshotTimeUTC"),
            "open": (op["bid"] + op["ask"]) / 2,
            "high": (hp["bid"] + hp["ask"]) / 2,
            "low": (lp["bid"] + lp["ask"]) / 2,
            "close": (cp["bid"] + cp["ask"]) / 2,
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

def _candles_to_df(candles):
    """Convert our candle dicts into an OHLC DataFrame smc expects."""
    df = pd.DataFrame(candles)
    if df.empty:
        return df
    df = df.rename(columns={"time": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _last_signal(col_df, direction_col, level_col, df_index):
    """Find the most recent non-null signal. smc returns RangeIndex frames —
    map back to df_index for timestamps."""
    sig = col_df[col_df[direction_col].notna()]
    if sig.empty:
        return None
    pos = int(sig.index[-1])
    row = col_df.iloc[pos]
    bars_ago = len(col_df) - 1 - pos
    out = {
        "direction": "bull" if row[direction_col] == 1 else "bear",
        "bars_ago": bars_ago,
        "bar_ts": df_index[pos].isoformat() if pos < len(df_index) else None,
    }
    if level_col and pd.notna(row[level_col]):
        out["level"] = round(float(row[level_col]), 5)
    return out


def smc_analyze(candles, swing_length=20):
    """Run smartmoneyconcepts detectors; return a flat summary dict.

    Returns None if SMC unavailable or not enough data."""
    if not SMC_AVAILABLE or len(candles) < swing_length * 2:
        return None

    df = _candles_to_df(candles)
    if len(df) < swing_length * 2:
        return None

    current_price = float(df["close"].iloc[-1])

    swings = smc.swing_highs_lows(df, swing_length=swing_length)
    bos_df = smc.bos_choch(df, swings, close_break=True)
    fvg_df = smc.fvg(df, join_consecutive=False)
    ob_df = smc.ob(df, swings, close_mitigation=False)

    # Last BOS and CHoCH separately
    last_bos = _last_signal(bos_df, "BOS", "Level", df.index)
    last_choch = _last_signal(bos_df, "CHOCH", "Level", df.index)

    # Most recent FVG and whether it's still unmitigated (MitigatedIndex == 0)
    last_fvg = None
    fvg_active = fvg_df[fvg_df["FVG"].notna()]
    if not fvg_active.empty:
        pos = int(fvg_active.index[-1])
        row = fvg_df.iloc[pos]
        last_fvg = {
            "direction": "bull" if row["FVG"] == 1 else "bear",
            "top": round(float(row["Top"]), 5),
            "bottom": round(float(row["Bottom"]), 5),
            "mitigated": bool(row["MitigatedIndex"] != 0),
            "bars_ago": len(df) - 1 - pos,
        }

    # Nearest unmitigated bullish + bearish OBs to current price
    nearest_bull_ob = nearest_bear_ob = None
    unmitigated = ob_df[(ob_df["OB"].notna()) & (ob_df["MitigatedIndex"] == 0)]
    for idx_pos in unmitigated.index:
        row = ob_df.iloc[int(idx_pos)]
        direction = "bull" if row["OB"] == 1 else "bear"
        entry = {
            "top": round(float(row["Top"]), 5),
            "bottom": round(float(row["Bottom"]), 5),
            "volume": int(row["OBVolume"]) if pd.notna(row["OBVolume"]) else None,
            "bars_ago": len(df) - 1 - int(idx_pos),
        }
        if direction == "bull" and entry["top"] < current_price:
            if nearest_bull_ob is None or entry["top"] > nearest_bull_ob["top"]:
                nearest_bull_ob = entry
        elif direction == "bear" and entry["bottom"] > current_price:
            if nearest_bear_ob is None or entry["bottom"] < nearest_bear_ob["bottom"]:
                nearest_bear_ob = entry

    # HTF bias from most recent BOS (best mechanical trend read available)
    trend = "unknown"
    if last_bos:
        trend = "bullish" if last_bos["direction"] == "bull" else "bearish"
    elif last_choch:
        trend = "bullish" if last_choch["direction"] == "bull" else "bearish"

    return {
        "trend_from_bos": trend,
        "last_bos": last_bos,
        "last_choch": last_choch,
        "last_fvg": last_fvg,
        "nearest_bull_ob": nearest_bull_ob,
        "nearest_bear_ob": nearest_bear_ob,
        "swing_length": swing_length,
    }


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
        "smc": smc_analyze(candles),
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
