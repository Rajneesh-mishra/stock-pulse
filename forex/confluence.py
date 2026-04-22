#!/usr/bin/env python3
"""
Multi-timeframe confluence scorer. Pure measurement tool — emits numbers,
never a trade call. Claude reads the output and judges whether to act.

Pulls D1 / H4 / H1 / M15 candles, runs SMC + EMA bias on each, composes a
weighted alignment score in [-100, +100]. Positive = bullish bias, negative
= bearish.

Readiness is TIERED (was a single 60-threshold binary veto — too strict for
a $1k account on 10-min tick cadence):

  strong    |composite| >= 60 AND all TFs agree
  moderate  |composite| >= 40 AND ≥ (n_tfs - 1) TFs agree (e.g. 3-of-4)
  weak      |composite| >= 25 AND ≥ 2 TFs agree
  none      otherwise

Claude maps readiness → sizing:
  strong   → full size (1.5% risk), market on confirmation
  moderate → half size (0.5% risk), anticipation LIMIT at level OK
  weak     → watchlist only, no entry
  none     → not even a watch

Usage:
  python3 forex/confluence.py AUDUSD
  python3 forex/confluence.py USDJPY --tfs DAY HOUR_4 HOUR MINUTE_15
"""

import argparse, json, sys
from technicals import get_full_candles, calc_ema, calc_atr, smc_analyze

# Weight per timeframe. Bigger TF = bigger weight. Sums to 1.0.
TF_WEIGHTS = {
    "DAY": 0.40,
    "HOUR_4": 0.30,
    "HOUR": 0.20,
    "MINUTE_15": 0.10,
}

# Minimum candles required per TF for a reliable read
MIN_CANDLES = {
    "DAY": 60,
    "HOUR_4": 80,
    "HOUR": 100,
    "MINUTE_15": 100,
}


def tf_bias(candles):
    """Score a single timeframe's directional bias in [-1, +1].

    Combines: EMA21/EMA50 alignment (macro trend) + last SMC BOS direction
    (structure) + last CHoCH (if more recent than BOS, it outweighs)."""
    closes = [c["close"] for c in candles if c["close"] is not None]
    if len(closes) < 50:
        return {"score": 0.0, "reasons": ["insufficient_candles"]}

    reasons = []
    score = 0.0

    # EMA component: current vs EMA21 vs EMA50 (max ±0.4)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    current = closes[-1]
    if ema21 and ema50:
        if current > ema21 > ema50:
            score += 0.4
            reasons.append("price>ema21>ema50")
        elif current < ema21 < ema50:
            score -= 0.4
            reasons.append("price<ema21<ema50")
        elif ema21 > ema50:
            score += 0.15
            reasons.append("ema21>ema50_mixed")
        elif ema21 < ema50:
            score -= 0.15
            reasons.append("ema21<ema50_mixed")

    # SMC structure component: last BOS / CHoCH (max ±0.6)
    smc_out = smc_analyze(candles)
    if smc_out:
        bos = smc_out.get("last_bos")
        choch = smc_out.get("last_choch")

        # CHoCH is a trend flip — if more recent than last BOS, it dominates
        if choch and bos and choch["bars_ago"] < bos["bars_ago"]:
            delta = 0.4 if choch["direction"] == "bull" else -0.4
            score += delta
            reasons.append(f"choch_{choch['direction']}_recent")
        elif bos:
            delta = 0.5 if bos["direction"] == "bull" else -0.5
            score += delta
            reasons.append(f"bos_{bos['direction']}")
        elif choch:
            delta = 0.3 if choch["direction"] == "bull" else -0.3
            score += delta
            reasons.append(f"choch_{choch['direction']}_only")

    # Clamp
    score = max(-1.0, min(1.0, score))
    return {"score": round(score, 3), "reasons": reasons, "smc": smc_out}


def scan(epic, timeframes=None):
    """Score an instrument across multiple timeframes."""
    if timeframes is None:
        timeframes = list(TF_WEIGHTS.keys())

    per_tf = {}
    current_price = None

    for tf in timeframes:
        count = MIN_CANDLES.get(tf, 100) + 50  # extra for swing-length warmup
        try:
            candles = get_full_candles(epic, tf, count)
        except Exception as e:
            per_tf[tf] = {"error": str(e)}
            continue

        if len(candles) < MIN_CANDLES.get(tf, 100):
            per_tf[tf] = {"error": f"only_{len(candles)}_candles"}
            continue

        bias = tf_bias(candles)
        bias["candles"] = len(candles)
        bias["last_close"] = candles[-1]["close"]
        bias["atr"] = calc_atr(candles)
        per_tf[tf] = bias
        current_price = candles[-1]["close"]

    # Composite score: weighted sum of per-TF scores, normalized to [-100, 100]
    total_weight = 0.0
    weighted_sum = 0.0
    for tf, w in TF_WEIGHTS.items():
        if tf in per_tf and "score" in per_tf[tf]:
            weighted_sum += w * per_tf[tf]["score"]
            total_weight += w

    composite = (weighted_sum / total_weight * 100) if total_weight else 0.0

    # Per-TF sign — 0 means TF was indecisive (|score| <= 0.1)
    signs = [1 if per_tf[tf].get("score", 0) > 0.1
             else -1 if per_tf[tf].get("score", 0) < -0.1
             else 0
             for tf in per_tf if "score" in per_tf[tf]]
    n_tfs = len(signs)
    # agree_count = how many TFs agree with the dominant (most populous) direction
    pos = sum(1 for s in signs if s > 0)
    neg = sum(1 for s in signs if s < 0)
    agree_count = max(pos, neg)
    all_agree = n_tfs > 0 and all(s == signs[0] and s != 0 for s in signs)

    # Tiered readiness — see module docstring for the sizing map
    abs_comp = abs(composite)
    if abs_comp >= 60 and all_agree:
        readiness = "strong"
    elif abs_comp >= 40 and agree_count >= max(1, n_tfs - 1):
        readiness = "moderate"
    elif abs_comp >= 25 and agree_count >= 2:
        readiness = "weak"
    else:
        readiness = "none"

    # Back-compat: legacy "aligned" key still means "strong"
    aligned = readiness == "strong"

    if composite > 10:
        call = "bullish"
    elif composite < -10:
        call = "bearish"
    else:
        call = "neutral"

    return {
        "epic": epic,
        "current_price": current_price,
        "composite_score": round(composite, 1),
        "directional_call": call,
        "readiness": readiness,            # strong | moderate | weak | none
        "aligned": aligned,                # kept for back-compat — readiness == "strong"
        "all_tfs_agree": all_agree,
        "agree_count": agree_count,
        "n_tfs_scored": n_tfs,
        "per_timeframe": per_tf,
        "weights": {k: v for k, v in TF_WEIGHTS.items() if k in timeframes},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("epic")
    ap.add_argument("--tfs", nargs="+", default=None,
                    help="Timeframes, e.g. DAY HOUR_4 HOUR MINUTE_15")
    args = ap.parse_args()
    result = scan(args.epic, args.tfs)
    print(json.dumps(result, indent=2, default=str))
