"""
Shared data-fetch and indicator building blocks for the Vyāpāram NSE scanner.

This is the India-specific sibling of the US scanner's wyckoff_common.py. The
*indicator math* (Wilder ATR, confirmed-pivot res/sup, zigzag, spring/upthrust)
is identical and intentionally kept byte-for-byte compatible so the shared
detector logic in patterns.py behaves the same on NSE bars as on US bars. The
only things that change for India are:

  * symbols carry an exchange suffix (RELIANCE -> "RELIANCE.NS" on Yahoo,
    "RELIANCE:NSE" on Twelve Data),
  * the benchmark is the NIFTY 50 index, not SPY.

DATA SOURCE ORDER (verified live 2026-10-24): Twelve Data's FREE tier does
NOT serve NSE equities or the NIFTY 50 index -- both come back empty; NSE
coverage needs a paid Twelve Data plan. Yahoo Finance's chart endpoint serves
NSE daily bars for free, so Yahoo is the PRIMARY source here and Twelve Data
is an optional fallback that only helps on a paid key. This is the opposite
order from the US scanner, where Twelve Data is primary.

Both sources fail loudly (return None) rather than returning stale/synthetic
bars, matching the US scanner's "degrade visibly, never fake data" rule.
"""

import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

API_KEY_FILE = Path(__file__).resolve().parent.parent / "twelvedata_api_key.txt"
API_KEY_ENV_VAR = "TWELVEDATA_API_KEY"

# NIFTY 50 index is the market benchmark for the regime filter (replaces SPY).
# Twelve Data exposes it as "NIFTY 50" and Yahoo as "^NSEI".
BENCHMARK = "NIFTY 50"
BENCHMARK_YAHOO = "^NSEI"

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
OUTPUT_SIZE = 300  # ~1y of daily bars

# ---- Wheel Zones params (match the US scanner's defaults exactly) ----
LEFT_BARS = 20
RIGHT_BARS = 5
ATR_LEN = 14
ZONE_MULT = 0.5

RETRY = 2

# ---- Free-tier rate limit: 8 credits/minute. Stay at 7/min for headroom. ----
MAX_CALLS_PER_MINUTE = 7
_call_times = deque()


def load_api_key():
    """Returns a Twelve Data key if one is configured, else None. Unlike the
    US scanner this is OPTIONAL: the free Yahoo source needs no key, and Twelve
    Data only returns NSE data on a paid plan. CI can set TWELVEDATA_API_KEY as
    a repo secret; local runs may drop the key in a plain-text file at the repo
    root. Missing key -> None, not an error."""
    env_key = os.environ.get(API_KEY_ENV_VAR)
    if env_key:
        return env_key.strip()
    if API_KEY_FILE.exists():
        key = API_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    return None


def _throttle():
    now = time.monotonic()
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= MAX_CALLS_PER_MINUTE:
        wait = 60 - (now - _call_times[0]) + 0.1
        if wait > 0:
            time.sleep(wait)
    _call_times.append(time.monotonic())


def _td_symbol(sym):
    """Twelve Data wants an explicit exchange for NSE equities. The benchmark
    index is already a fully-qualified name, so don't suffix it."""
    if sym == BENCHMARK or ":" in sym:
        return sym
    return f"{sym}:NSE"


def _yahoo_symbol(sym):
    if sym == BENCHMARK:
        return BENCHMARK_YAHOO
    if sym.endswith(".NS") or sym.startswith("^"):
        return sym
    return f"{sym}.NS"


def _parse_td(data):
    values = data.get("values")
    if not values:
        return None
    bars = []
    for v in values:
        try:
            bars.append({
                "date": v["datetime"][:10],
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
                "volume": float(v["volume"]) if v.get("volume") else 0,
            })
        except (KeyError, ValueError, TypeError):
            continue
    if not bars:
        return None
    bars.sort(key=lambda b: b["date"])
    return bars


def _fetch_twelvedata(sym, api_key):
    params = {
        "symbol": _td_symbol(sym),
        "interval": "1day",
        "outputsize": OUTPUT_SIZE,
        "order": "ASC",
        "apikey": api_key,
    }
    url = f"{TIME_SERIES_URL}?{urllib.parse.urlencode(params)}"
    for _ in range(RETRY + 1):
        _throttle()
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            if data.get("status") == "error":
                if data.get("code") == 429:
                    time.sleep(10)
                    continue
                return None  # bad symbol / not found -- don't retry
            return _parse_td(data)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            time.sleep(1)
    return None


def _fetch_yahoo(sym):
    """Best-effort fallback. Yahoo's chart endpoint is unofficial; any failure
    returns None so the caller can skip the symbol rather than fake a bar."""
    ysym = urllib.parse.quote(_yahoo_symbol(sym))
    url = YAHOO_CHART_URL.format(symbol=ysym) + "?range=1y&interval=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    try:
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q.get("open"), q.get("high"), q.get("low"), q.get("close")
        v = q.get("volume")
        try:
            if None in (o[i], h[i], l[i], c[i]):
                continue
            bars.append({
                "date": time.strftime("%Y-%m-%d", time.gmtime(t)),
                "open": float(o[i]), "high": float(h[i]),
                "low": float(l[i]), "close": float(c[i]),
                "volume": float(v[i]) if v and v[i] is not None else 0,
            })
        except (TypeError, ValueError, IndexError):
            continue
    if not bars:
        return None
    bars.sort(key=lambda b: b["date"])
    return bars


def fetch_bars(sym, api_key=None):
    """Primary: Yahoo Finance (.NS) -- the only source that serves NSE for
    free. Fallback: Twelve Data (NSE), which only returns data on a paid plan,
    so it's tried second and only when an api_key is present. Returns
    confirmed-last-bar-trimmed bars, or None if no source has the symbol."""
    bars = _fetch_yahoo(sym)
    if not bars and api_key:
        bars = _fetch_twelvedata(sym, api_key)
    if not bars:
        return None
    return drop_unconfirmed_last_bar(bars)


def drop_unconfirmed_last_bar(bars):
    # Heuristic: if the freshest bar's volume is far below the recent average,
    # the session likely hasn't settled yet -- use the last fully-formed bar.
    if len(bars) < 21:
        return bars
    recent_avg = statistics.mean(b["volume"] for b in bars[-21:-1])
    if recent_avg > 0 and bars[-1]["volume"] < 0.1 * recent_avg:
        return bars[:-1]
    return bars


# ---------------------------------------------------------------------------
# Indicator math -- kept identical to the US scanner's wyckoff_common so the
# shared detectors behave the same on NSE bars.
# ---------------------------------------------------------------------------
def wilder_atr(bars, length=ATR_LEN):
    trs = [None]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = [None] * len(bars)
    if len(bars) <= length:
        return atr
    seed = statistics.mean(trs[1:length + 1])
    atr[length] = seed
    for i in range(length + 1, len(bars)):
        atr[i] = (atr[i - 1] * (length - 1) + trs[i]) / length
    return atr


def pivots(bars, left=LEFT_BARS, right=RIGHT_BARS):
    """Returns (res, sup) arrays: last CONFIRMED swing high/low as of each bar."""
    n = len(bars)
    res = [None] * n
    sup = [None] * n
    cur_res = None
    cur_sup = None
    for j in range(n):
        k = j - right
        if k >= left and k + right < n:
            window_hi = [bars[i]["high"] for i in range(k - left, k + right + 1)]
            if bars[k]["high"] == max(window_hi):
                cur_res = bars[k]["high"]
            window_lo = [bars[i]["low"] for i in range(k - left, k + right + 1)]
            if bars[k]["low"] == min(window_lo):
                cur_sup = bars[k]["low"]
        res[j] = cur_res
        sup[j] = cur_sup
    return res, sup


def zigzag(bars, left=LEFT_BARS, right=RIGHT_BARS):
    """Ordered list of alternating swing points: [{'idx','date','price','type'}, ...]
    type is 'H' (swing high) or 'L' (swing low), with strict alternation."""
    n = len(bars)
    raw = []
    for k in range(left, n - right):
        window_hi = [bars[i]["high"] for i in range(k - left, k + right + 1)]
        if bars[k]["high"] == max(window_hi):
            raw.append({"idx": k, "date": bars[k]["date"], "price": bars[k]["high"], "type": "H"})
        window_lo = [bars[i]["low"] for i in range(k - left, k + right + 1)]
        if bars[k]["low"] == min(window_lo):
            raw.append({"idx": k, "date": bars[k]["date"], "price": bars[k]["low"], "type": "L"})
    raw.sort(key=lambda p: p["idx"])

    swings = []
    for p in raw:
        if swings and swings[-1]["type"] == p["type"]:
            if (p["type"] == "H" and p["price"] >= swings[-1]["price"]) or \
               (p["type"] == "L" and p["price"] <= swings[-1]["price"]):
                swings[-1] = p
        else:
            swings.append(p)
    return swings


def build_close_by_date(bars):
    return {b["date"]: b["close"] for b in bars}


def is_pure_spring(bars, sup, idx):
    """Textbook spring: today's LOW undercuts the last confirmed support, but
    the CLOSE recovers back above it (a failed breakdown -> bullish)."""
    return sup[idx] is not None and bars[idx]["low"] < sup[idx] and bars[idx]["close"] > sup[idx]


def is_pure_upthrust(bars, res, idx):
    """Textbook upthrust with stricter 2026-09 criteria (penetration depth,
    volume spike, reversal confirmation) -- identical to the US scanner."""
    if res[idx] is None:
        return False

    bar = bars[idx]
    resistance = res[idx]

    if not (bar["high"] > resistance and bar["close"] < resistance):
        return False

    penetration_pct = (bar["high"] - resistance) / resistance * 100
    if penetration_pct < 1.5:
        return False

    if idx >= 20:
        avg_vol = statistics.mean(bars[i]["volume"] for i in range(idx - 20, idx))
        if avg_vol > 0 and bar["volume"] < avg_vol * 1.2:
            return False

    day_range = bar["high"] - bar["low"]
    if day_range > 0:
        close_position = (bar["close"] - bar["low"]) / day_range
        if close_position > 0.5:
            return False

    return True


# ---------------------------------------------------------------------------
# Confidence Tier System -- carried over from the US scanner's live-performance
# analysis. NOTE: these tiers were calibrated on US names; on NSE they are a
# starting prior, not a validated ranking. Treat them as such.
# ---------------------------------------------------------------------------
SETUP_BASE_TIERS = {
    "spring": "HIGH",
    "lps": "HIGH",
    "sos": "HIGH",
    "abc": "MEDIUM",
    "sc": "MEDIUM",
    "upthrust": "REVIEW",
    "bc": "REVIEW",
    "sow": "REVIEW",
    "lpsy": "REVIEW",
}

TIER_LABELS = {
    "HIGH": "\u2b50\u2b50\u2b50",
    "MEDIUM": "\u2b50\u2b50",
    "REVIEW": "\u2b50",
}


def get_confidence_tier(setup_type, regime_aligned=True):
    """Returns {'tier','label','reason'} for a setup based on historical priors."""
    setup_lower = setup_type.lower()
    base_tier = SETUP_BASE_TIERS.get(setup_lower, "REVIEW")

    if not regime_aligned:
        if base_tier == "HIGH":
            tier = "MEDIUM"
            reason = f"{setup_type} is normally HIGH confidence, but downgraded (against market regime)"
        elif base_tier == "MEDIUM":
            tier = "REVIEW"
            reason = f"{setup_type} downgraded to REVIEW ONLY (against market regime)"
        else:
            tier = "REVIEW"
            reason = f"{setup_type} is REVIEW ONLY (low historical edge + against regime)"
    else:
        tier = base_tier
        if tier == "HIGH":
            reason = f"{setup_type} has strong historical performance with regime alignment"
        elif tier == "MEDIUM":
            reason = f"{setup_type} has decent hit rate but mixed P&L historically"
        else:
            reason = f"{setup_type} has weak/negative historical edge -- extra discretion needed"

    return {"tier": tier, "label": TIER_LABELS[tier], "reason": reason}
