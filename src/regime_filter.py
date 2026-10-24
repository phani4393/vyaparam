"""
Market regime filter for live signal gating -- India edition.

Same logic as the US scanner's regime_filter.py, but the benchmark is the
NIFTY 50 index instead of SPY. The idea is unchanged: trading WITH the broad
trend (bullish signals when NIFTY is above its 200d SMA, bearish when below)
is less bad than fighting it.

NOTE: the US scanner validated this against a 20-year backtest. There is no
equivalent NSE backtest yet, so on Indian data this gate is a reasonable
prior borrowed from the US study, not a locally-validated filter. Documented
honestly rather than presented as tested.

Usage:
    from regime_filter import get_regime, should_take_signal
    nifty_bars = fetch_bars("NIFTY 50", api_key)
    regime = get_regime(nifty_bars)
    if should_take_signal(regime, "bullish"):
        ...
"""

SMA_LEN = 200
DEFAULT_MODE = "strict"

# Label the benchmark by name in all human-readable output.
BENCHMARK_LABEL = "NIFTY"


def _sma(bars, length):
    if len(bars) < length:
        return [None] * len(bars)
    out = [None] * len(bars)
    running = sum(b["close"] for b in bars[:length])
    out[length - 1] = running / length
    for i in range(length, len(bars)):
        running += bars[i]["close"] - bars[i - length]["close"]
        out[i] = running / length
    return out


def get_regime(bench_bars, mode=None):
    """Determine current market regime from NIFTY 50 bars. Returns a dict with
    regime/gates/message (same shape as the US scanner)."""
    mode = mode or DEFAULT_MODE

    if not bench_bars or len(bench_bars) < SMA_LEN:
        return {
            "regime": "unknown", "bench_close": None, "sma_200": None,
            "distance_pct": None, "trend_strength": "unknown",
            "take_bullish": True, "take_bearish": True, "mode": mode,
            "message": f"Insufficient {BENCHMARK_LABEL} data for regime detection -- all signals allowed",
        }

    sma = _sma(bench_bars, SMA_LEN)
    bench_close = bench_bars[-1]["close"]
    sma_200 = sma[-1]

    if sma_200 is None or sma_200 <= 0:
        return {
            "regime": "unknown", "bench_close": bench_close, "sma_200": None,
            "distance_pct": None, "trend_strength": "unknown",
            "take_bullish": True, "take_bearish": True, "mode": mode,
            "message": f"Could not compute {BENCHMARK_LABEL} 200d SMA -- all signals allowed",
        }

    regime = "bull" if bench_close > sma_200 else "bear"
    distance_pct = ((bench_close - sma_200) / sma_200) * 100

    if abs(distance_pct) > 3.0:
        trend_strength = "strong"
    elif abs(distance_pct) < 1.0:
        trend_strength = "neutral"
    else:
        trend_strength = "weak"

    if mode == "strict":
        take_bullish = regime == "bull"
        take_bearish = regime == "bear"
    elif mode == "permissive":
        take_bullish = True
        take_bearish = True
    elif mode == "adaptive":
        if trend_strength == "strong":
            take_bullish = regime == "bull"
            take_bearish = regime == "bear"
        else:
            take_bullish = True
            take_bearish = True
    else:
        take_bullish = True
        take_bearish = True

    regime_word = "BULL" if regime == "bull" else "BEAR"
    strength_word = f"({trend_strength})" if trend_strength != "neutral" else "(near SMA)"
    gate_msg = []
    if not take_bullish:
        gate_msg.append("bullish signals filtered")
    if not take_bearish:
        gate_msg.append("bearish signals filtered")
    gate_str = "; ".join(gate_msg) if gate_msg else "all signals allowed"

    message = (f"{BENCHMARK_LABEL} {regime_word} regime {strength_word}: "
               f"close {bench_close:.2f} vs 200d SMA {sma_200:.2f} ({distance_pct:+.1f}%) -- {gate_str}")

    return {
        "regime": regime, "bench_close": bench_close, "sma_200": sma_200,
        "distance_pct": distance_pct, "trend_strength": trend_strength,
        "take_bullish": take_bullish, "take_bearish": take_bearish,
        "mode": mode, "message": message,
    }


def should_take_signal(regime_info, direction):
    if direction == "bullish":
        return regime_info.get("take_bullish", True)
    elif direction == "bearish":
        return regime_info.get("take_bearish", True)
    return True


def regime_context_line(regime_info):
    if regime_info["regime"] == "unknown":
        return "Regime: unknown (insufficient data)"
    regime_word = regime_info["regime"].upper()
    strength = regime_info["trend_strength"]
    dist = regime_info["distance_pct"]
    above_below = "above" if dist >= 0 else "below"
    return f"Regime: {BENCHMARK_LABEL} {regime_word} ({strength}), {abs(dist):.1f}% {above_below} 200d SMA"


def main():
    import argparse
    import nse_common as c

    parser = argparse.ArgumentParser(description="Check current NIFTY market regime")
    parser.add_argument("--mode", choices=["strict", "permissive", "adaptive"],
                        default=DEFAULT_MODE, help="Filter mode")
    args = parser.parse_args()

    api_key = c.load_api_key()
    print(f"Fetching {c.BENCHMARK} data...", flush=True)
    bench_bars = c.fetch_bars(c.BENCHMARK, api_key)
    if not bench_bars:
        print(f"ERROR: Could not fetch {c.BENCHMARK} data")
        return

    regime = get_regime(bench_bars, mode=args.mode)
    print()
    print("=" * 60)
    print("MARKET REGIME CHECK (NIFTY 50)")
    print("=" * 60)
    print(f"NIFTY Close:   {regime['bench_close']:.2f}")
    print(f"200d SMA:      {regime['sma_200']:.2f}")
    print(f"Distance:      {regime['distance_pct']:+.2f}%")
    print(f"Regime:        {regime['regime'].upper()}")
    print(f"Trend Strength:{regime['trend_strength']}")
    print(f"Mode:          {regime['mode']}")
    print()
    print(f"Take Bullish:  {'YES' if regime['take_bullish'] else 'NO'}")
    print(f"Take Bearish:  {'YES' if regime['take_bearish'] else 'NO'}")
    print()
    print(f"Summary: {regime['message']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
