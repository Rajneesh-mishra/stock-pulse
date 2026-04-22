#!/usr/bin/env python3
"""
Forex news watcher — the MACRO EYE (third sensor layer).

Polls Google News RSS with Claude-authored queries from state/news_queries.json.
For each new headline that contains ≥1 required keyword AND the query's
cooldown has elapsed, emits a news_flash event to forex_events.jsonl.

Does NO judgment on whether the news is actually material — that's Claude's
job. This daemon just filters the firehose down to headlines matching
Claude's stated interests.

Closes the "news between ticks" blind spot. If Iran formally signs at 3am,
a matching headline fires an event within 10 min, waker spawns Claude,
Claude reasons + acts.
"""

import fcntl
import json
import os
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

REPO = Path("/Users/rajneeshmishra/Downloads/stock-pulse")
os.chdir(REPO)

STATE = REPO / "state"
EVENTS_FILE = STATE / "forex_events.jsonl"
QUERIES_FILE = STATE / "news_queries.json"
CONTROL_FILE = STATE / "forex_news.control"
STATUS_FILE = STATE / "forex_news_status.json"
RUNTIME_FILE = STATE / ".news_runtime.json"
SIGNALS_FILE = STATE / "forex_watchlist_signals.json"

USER_AGENT = "ForexPulse-NewsWatcher/1.0"
MAX_SEEN_URLS = 1000
DEFAULT_POLL_ACTIVE = 600       # 10 min active market
DEFAULT_POLL_QUIET = 1200       # 20 min overnight/weekend
DEFAULT_HOURLY_CAP = 15         # max events emitted per hour


def log(msg):
    stamp = datetime.now(timezone.utc).isoformat()
    print(f"[{stamp}] {msg}", flush=True)


def utc_now():
    return datetime.now(timezone.utc)


def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        log(f"read_json({path.name}) failed: {e}")
    return default


def write_json_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def append_event(event):
    event.setdefault("event_id", f"evt_{int(time.time()*1000)}_news_flash")
    event.setdefault("ts_utc", utc_now().isoformat())
    event.setdefault("consumed_by_claude", False)
    event.setdefault("source", "news_watcher")
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Use flock so we don't interleave lines with the other daemons
    with EVENTS_FILE.open("a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(event, default=str) + "\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    log(f"EVENT news_flash  query={event['payload']['query_id']}  "
        f"kw={event['payload']['matched_keywords']}  "
        f"headline={event['payload']['headline'][:80]}")


def read_control():
    if not CONTROL_FILE.exists():
        return "run"
    try:
        val = CONTROL_FILE.read_text().strip().lower()
        return val if val in ("run", "pause", "stop") else "run"
    except Exception:
        return "run"


def market_active():
    """Same 24/5 forex logic as forex_watcher."""
    now = utc_now()
    if now.weekday() == 5:  # Saturday
        return False
    if now.weekday() == 6 and now.hour < 21:
        return False
    if now.weekday() == 4 and now.hour >= 21:
        return False
    return True


# ── RSS ──────────────────────────────────────────────────────────────────────

def fetch_rss(query, timeout=15):
    """Fetch Google News RSS for a search query. Returns list of items."""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as e:
        log(f"RSS fetch fail ({query[:40]}): {e}")
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        log(f"RSS parse fail: {e}")
        return []

    items = []
    for item in root.findall(".//item"):
        src_node = item.find("source")
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "pubDate": (item.findtext("pubDate") or "").strip(),
            "source": (src_node.text.strip() if src_node is not None and src_node.text else ""),
        })
    return items


def matched_keywords(title, keywords):
    """Return list of required keywords found in title (case-insensitive).
    Empty list = match (if keywords_required was empty). Empty keywords
    list in config means "emit on any new headline for this query"."""
    if not keywords:
        return []
    title_lc = title.lower()
    return [k for k in keywords if k.lower() in title_lc]


# ── Runtime state ────────────────────────────────────────────────────────────

def load_runtime():
    rt = read_json(RUNTIME_FILE, {
        "seen_urls": [],               # recent URLs we've processed (bounded)
        "last_emit_per_query": {},     # {query_id: ts_iso}
        "hourly_window": {"start": utc_now().isoformat(), "count": 0},
    })
    # Ensure seen_urls is a list (JSON-safe); we treat it as an ordered list
    if not isinstance(rt.get("seen_urls"), list):
        rt["seen_urls"] = []
    return rt


def save_runtime(rt):
    # Bound seen_urls
    if len(rt["seen_urls"]) > MAX_SEEN_URLS:
        rt["seen_urls"] = rt["seen_urls"][-MAX_SEEN_URLS:]
    write_json_atomic(RUNTIME_FILE, rt)


# ── Main loop ────────────────────────────────────────────────────────────────

_STARTED_AT = utc_now().isoformat()


_AUDIT_REQUEST_COOLDOWN_SEC = 900   # 15min per alert_id to avoid spam
_audit_last_emit = {}   # alert_id -> timestamp


def _emit_audit_requests_for_alerts(qid, headline, matched_keywords, url):
    """If headline matches terms already tracked by active level alerts, emit
    a targeted alert_audit_request event. One event per matched alert, per
    15min cooldown. Claude's tick Step 4f handles these by re-scoring that
    alert and arming anticipation LIMIT if readiness improved."""
    try:
        wl = json.loads(SIGNALS_FILE.read_text())
    except Exception:
        return
    now = time.time()
    headline_l = headline.lower()
    kw_set = set(k.lower() for k in (matched_keywords or []))

    for alert in wl.get("level_alerts", []):
        aid = alert.get("id")
        note = (alert.get("note") or "").lower()
        inst = alert.get("instrument", "")
        # Match if any of:
        #   - any matched_keyword appears in alert note
        #   - alert note shares 2+ significant words with the headline
        score = 0
        matched_terms = []
        for kw in kw_set:
            if kw and kw in note:
                score += 2
                matched_terms.append(kw)
        # crude fallback: 3+ significant shared tokens — strip punctuation
        # symmetrically on both sides so e.g. "deadline," matches "deadline"
        _punct = ".,;:!?'\"()[]"
        note_tokens = set(t.strip(_punct) for t in note.split() if len(t.strip(_punct)) >= 5)
        head_tokens = set(t.strip(_punct) for t in headline_l.split() if len(t.strip(_punct)) >= 5)
        overlap = note_tokens & head_tokens
        if len(overlap) >= 3:
            score += 1
            matched_terms.extend(list(overlap)[:5])
        if score < 2:
            continue

        # Per-alert cooldown
        last = _audit_last_emit.get(aid, 0)
        if now - last < _AUDIT_REQUEST_COOLDOWN_SEC:
            continue
        _audit_last_emit[aid] = now

        append_event({
            "type": "alert_audit_request",
            "instrument": inst,
            "alert_id": aid,
            "payload": {
                "trigger_query_id": qid,
                "trigger_headline": headline[:200],
                "trigger_url": url,
                "match_score": score,
                "matched_terms": matched_terms[:10],
            },
        })


def process_query(q, runtime, hourly_cap):
    """Fetch RSS for one query, emit events for new matching headlines.
    Returns number of events emitted."""
    qid = q.get("id", "unknown")
    query = q.get("query", "")
    keywords = q.get("keywords_required", [])
    cooldown_min = q.get("cooldown_min", 30)

    # Per-query cooldown
    last = runtime["last_emit_per_query"].get(qid)
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (utc_now() - last_dt).total_seconds() < cooldown_min * 60:
                return 0
        except Exception:
            pass

    items = fetch_rss(query)
    seen_set = set(runtime["seen_urls"])
    emitted = 0

    # Walk OLDEST → NEWEST so if multiple match we emit in temporal order
    for item in reversed(items):
        url = item.get("link", "")
        title = item.get("title", "")
        if not url or url in seen_set:
            continue
        seen_set.add(url)
        runtime["seen_urls"].append(url)

        matches = matched_keywords(title, keywords)
        if keywords and not matches:
            continue

        # Global hourly cap
        if runtime["hourly_window"]["count"] >= hourly_cap:
            log(f"hourly cap hit ({hourly_cap}) — deferring further emissions")
            break

        append_event({
            "type": "news_flash",
            "payload": {
                "query_id": qid, "query": query,
                "headline": title, "url": url,
                "source": item.get("source", ""),
                "published": item.get("pubDate", ""),
                "matched_keywords": matches,
                "note": q.get("note", ""),
            },
        })
        runtime["last_emit_per_query"][qid] = utc_now().isoformat()
        runtime["hourly_window"]["count"] += 1
        emitted += 1

        # Alert re-audit request: if this headline's keywords overlap with
        # any active alert's note/keywords, emit a targeted audit event so
        # Claude re-scores THAT alert's readiness even if price isn't at
        # trigger. Prevents news-reactive blind sizing (would whipsaw) while
        # still letting material news drive thesis re-evaluation.
        _emit_audit_requests_for_alerts(qid, title, matches, url)

    return emitted


def roll_hourly_window(runtime):
    try:
        start = datetime.fromisoformat(runtime["hourly_window"]["start"])
        if (utc_now() - start).total_seconds() >= 3600:
            runtime["hourly_window"] = {"start": utc_now().isoformat(), "count": 0}
    except Exception:
        runtime["hourly_window"] = {"start": utc_now().isoformat(), "count": 0}


def _count_events():
    if not EVENTS_FILE.exists():
        return 0
    try:
        return sum(1 for _ in EVENTS_FILE.open())
    except Exception:
        return 0


def main():
    log(f"forex_news_watcher starting, pid={os.getpid()}")
    STATE.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    runtime = load_runtime()
    polls, errors, events_start = 0, 0, _count_events()
    last_error = None

    while True:
        control = read_control()
        if control == "stop":
            log("control=stop → exiting")
            write_json_atomic(STATUS_FILE, {"status": "stopped", "polls_total": polls})
            save_runtime(runtime)
            return 0
        if control == "pause":
            write_json_atomic(STATUS_FILE, {"status": "paused", "polls_total": polls})
            time.sleep(30)
            continue

        cfg = read_json(QUERIES_FILE, {"queries": [], "global_settings": {}})
        queries = cfg.get("queries", [])
        gs = cfg.get("global_settings", {})
        hourly_cap = gs.get("max_events_per_hour", DEFAULT_HOURLY_CAP)

        roll_hourly_window(runtime)

        total_emitted = 0
        for q in queries:
            try:
                total_emitted += process_query(q, runtime, hourly_cap)
            except Exception as e:
                errors += 1
                last_error = f"{q.get('id','?')}: {e}"
                log(f"query {q.get('id')} failed: {e}\n{traceback.format_exc()[-400:]}")

        save_runtime(runtime)

        polls += 1
        write_json_atomic(STATUS_FILE, {
            "status": "running",
            "pid": os.getpid(),
            "started_at": _STARTED_AT,
            "last_poll": utc_now().isoformat(),
            "polls_total": polls,
            "events_emitted": _count_events() - events_start,
            "errors": errors,
            "last_error": last_error,
            "query_count": len(queries),
            "hourly_cap": hourly_cap,
            "hourly_used": runtime["hourly_window"]["count"],
        })

        cadence = (gs.get("poll_sec_active", DEFAULT_POLL_ACTIVE)
                   if market_active()
                   else gs.get("poll_sec_quiet", DEFAULT_POLL_QUIET))
        if total_emitted > 0:
            log(f"tick: {total_emitted} events emitted, sleeping {cadence}s")
        time.sleep(cadence)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("KeyboardInterrupt → exiting")
        sys.exit(0)
