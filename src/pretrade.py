"""
Pre-trade context for an options buyer, computed from daily bars (free -- no
options/IV feed). India edition: same realized-vol expected-move math as the
US scanner, formatted in rupees (Rs) instead of dollars.

What this gives you (free): a 1-sigma realized-vol expected move over your
holding horizon -- the single most useful free number for STRIKE SELECTION.

What it does NOT give you (needs paid data): IV rank / implied vol, and
automatic events dates. Check IV and the events calendar in your broker.

Usage: python pretrade.py RELIANCE --horizon 30
"""

import argparse
import math
import statistics

import nse_common as c

VOL_WINDOW = 20


def realized_daily_vol(bars, window=VOL_WINDOW):
    if len(bars) < window + 1:
        return None
    closes = [b["close"] for b in bars[-(window + 1):]]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def expected_move(bars, horizon_days):
    """1-sigma expected move over `horizon_days` trading days, from realized vol."""
    dv = realized_daily_vol(bars)
    if dv is None or not bars:
        return None
    close = bars[-1]["close"]
    move_pct = dv * math.sqrt(max(horizon_days, 1))
    move_abs = close * move_pct
    return {
        "close": close,
        "daily_vol_pct": dv * 100,
        "horizon_days": horizon_days,
        "move_pct": move_pct * 100,
        "move_abs": move_abs,
        "low": close - move_abs,
        "high": close + move_abs,
    }


def format_line(em, direction=None):
    if not em:
        return "expected move: n/a (insufficient history)"
    base = (f"~{em['horizon_days']}d expected move +/-{em['move_pct']:.1f}% "
            f"(+/-Rs{em['move_abs']:.2f}); 1-sigma range Rs{em['low']:.2f}-Rs{em['high']:.2f}")
    if direction == "bullish":
        base += f"; realistic upside target ~Rs{em['high']:.2f}"
    elif direction == "bearish":
        base += f"; realistic downside target ~Rs{em['low']:.2f}"
    return base


def main():
    ap = argparse.ArgumentParser(description="Free pre-trade context (expected move) for an NSE options buyer")
    ap.add_argument("ticker")
    ap.add_argument("--horizon", type=int, default=30, help="holding horizon in trading days (default 30)")
    a = ap.parse_args()
    key = c.load_api_key()
    bars = c.fetch_bars(a.ticker.upper(), key)
    if not bars:
        raise SystemExit(f"Could not fetch {a.ticker}")
    em = expected_move(bars, a.horizon)
    print(f"{a.ticker.upper()}: {format_line(em)}")
    print("  (IV rank NOT included -- needs a paid options feed; check it in your broker before buying.)")


if __name__ == "__main__":
    main()
