"""
Vyāpāram -- the Nifty 50 Wyckoff scanner (entry point).

Runs every detector in patterns.py (plus the Wheel Zones spring/upthrust from
nse_common) across the Nifty 50, gates signals by NIFTY-50 market regime, adds
a realized-vol expected-move line for strike sanity and an FII/DII flow line
for sentiment context, and pushes the result to Telegram.

This is the India sibling of the US scanner's wyckoff_watchlist_scanner.py. It
is deliberately slimmer: no chart PNGs, no local trade journal, no persisted
alert log yet -- those can be ported from the US repo later if the signal
proves worth tracking. The honest framing is identical: every alert is a
DISCRETIONARY REVIEW TRIGGER, not a validated mechanical edge.

Usage: python nifty_scanner.py
"""

import csv
import os
import sys
from pathlib import Path

# The tier labels are emoji (\u2b50). A Windows console defaults to cp1252 and
# crashes on them with UnicodeEncodeError. Force stdout to UTF-8 where we can,
# and fall back to replacing un-encodable chars so a local run never dies on a
# print. (CI/Telegram paths are UTF-8 already and unaffected.)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

import notify
import regime_filter
import fii_dii_flow
from nse_common import (BENCHMARK, fetch_bars, load_api_key, pivots, wilder_atr,
                        is_pure_spring, is_pure_upthrust, get_confidence_tier)
from patterns import trading_range, climax_events, sos_sow_events, lps_lpsy_events, abc_pattern
from pretrade import expected_move, format_line

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "nifty50.csv"
REGIME_MODE = os.environ.get("REGIME_MODE", "strict")


def load_tickers():
    with open(TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


def scan_ticker(sym, bars, regime_info):
    """Returns a result dict for a ticker with >=1 NEW signal today, else None."""
    n = len(bars)
    if n < 60:
        return None
    atr = wilder_atr(bars)
    res, sup = pivots(bars)

    actionable_events = []   # (setup_type, thesis)
    filtered_events = []     # thesis strings (regime-filtered -> watchlist only)

    def _check(direction):
        return regime_filter.should_take_signal(regime_info, direction)

    # ---- Spring / upthrust (textbook, edge-triggered on today) ----
    if is_pure_spring(bars, sup, n - 1) and not is_pure_spring(bars, sup, n - 2):
        thesis = f"Spring at support {sup[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bullish bias, review for a LONG CALL"
        (actionable_events.append(("spring", thesis)) if _check("bullish")
         else filtered_events.append(thesis))
    if is_pure_upthrust(bars, res, n - 1) and not is_pure_upthrust(bars, res, n - 2):
        thesis = f"Upthrust at resistance {res[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bearish bias, review for a LONG PUT"
        (actionable_events.append(("upthrust", thesis)) if _check("bearish")
         else filtered_events.append(thesis))

    # ---- Trading range: just entered one ----
    tr_today = trading_range(bars)
    tr_yday = trading_range(bars[:-1])
    if tr_today and tr_yday and tr_today["inRange"] and not tr_yday["inRange"]:
        actionable_events.append(("trading_range",
                                  f"Entered a trading range ({tr_today['rangeLow']:.2f}-{tr_today['rangeHigh']:.2f})"))

    # ---- Climax + Automatic Reaction/Rally ----
    for e in climax_events(bars, atr):
        if e["idx"] == n - 1:
            label, bias = (("Selling Climax", "bullish bias, review for a LONG CALL")
                           if e["type"] == "SC"
                           else ("Buying Climax", "bearish bias, review for a LONG PUT"))
            thesis = f"{label} @ {e['price']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "SC" else "bearish"
            (actionable_events.append((e["type"].lower(), thesis)) if _check(direction)
             else filtered_events.append(thesis))
        if e["arIdx"] == n - 1:
            label = "Automatic Rally" if e["type"] == "SC" else "Automatic Reaction"
            actionable_events.append(("ar",
                f"{label} @ {e['arPrice']:.2f} (range boundary from the {e['date']} {e['type']}) -- context, not an entry"))

    # ---- Sign of Strength/Weakness ----
    sos_sow = sos_sow_events(bars, res, sup, atr)
    for e in sos_sow:
        if e["idx"] == n - 1:
            label, bias = (("Sign of Strength (breakout held)", "bullish bias, review for a LONG CALL")
                           if e["type"] == "SOS"
                           else ("Sign of Weakness (breakdown held)", "bearish bias, review for a LONG PUT"))
            thesis = f"{label} @ {e['level']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "SOS" else "bearish"
            (actionable_events.append((e["type"].lower(), thesis)) if _check(direction)
             else filtered_events.append(thesis))

    # ---- Last Point of Support/Supply ----
    for e in lps_lpsy_events(bars, sos_sow, atr):
        if e["idx"] == n - 1:
            label, bias = (("Last Point of Support", "bullish bias, review for a LONG CALL")
                           if e["type"] == "LPS"
                           else ("Last Point of Supply", "bearish bias, review for a LONG PUT"))
            thesis = f"{label} @ {e['level']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "LPS" else "bearish"
            (actionable_events.append((e["type"].lower(), thesis)) if _check(direction)
             else filtered_events.append(thesis))

    # ---- ABC correction ----
    abc = abc_pattern(bars)
    if abc and abc["isNew"]:
        opt = "LONG CALL" if abc["direction"].startswith("bullish") else "LONG PUT"
        thesis = (f"ABC correction complete, {abc['direction']} -- review for a {opt} "
                  f"(B retraced {abc['bRetrace']*100:.0f}% of A, C extended {abc['cExtension']*100:.0f}% of A)")
        direction = "bullish" if abc["direction"].startswith("bullish") else "bearish"
        (actionable_events.append(("abc", thesis)) if _check(direction)
         else filtered_events.append(thesis))

    if not actionable_events and not filtered_events:
        return None

    em = expected_move(bars, 30)
    context_line = "Context: " + format_line(em) + " | check IV rank + events in broker before entry"
    regime_line = regime_filter.regime_context_line(regime_info)

    return {
        "actionable_events": actionable_events,
        "filtered_events": filtered_events,
        "context_line": context_line,
        "regime_line": regime_line,
    }


def scan(tickers, api_key, progress=False):
    bench_bars = fetch_bars(BENCHMARK, api_key)
    if not bench_bars:
        raise RuntimeError(f"Could not fetch benchmark ({BENCHMARK}) data -- aborting scan.")

    regime_info = regime_filter.get_regime(bench_bars, mode=REGIME_MODE)
    if progress:
        print(f"Market regime: {regime_info['message']}")
        print()

    hits = []
    skipped = []
    for idx, sym in enumerate(tickers, 1):
        bars = fetch_bars(sym, api_key)
        if not bars:
            skipped.append(sym)
        else:
            result = scan_ticker(sym, bars, regime_info)
            if result:
                hits.append((sym, result))
        if progress:
            print(f"...{idx}/{len(tickers)} scanned", flush=True)
    return hits, skipped, regime_info


def main():
    api_key = load_api_key()  # optional -- Yahoo is the free primary source
    if not api_key:
        print("No Twelve Data key set -- using the free Yahoo Finance source (expected on the free tier).")
    tickers = load_tickers()
    hits, skipped, regime_info = scan(tickers, api_key, progress=True)
    hits.sort(key=lambda x: x[0])

    print(f"Scanned {len(tickers)} Nifty 50 tickers, {len(skipped)} skipped.")
    if skipped:
        print("Skipped:", ", ".join(skipped))
    print()

    if len(skipped) > len(tickers) / 2:
        notify.send_message(f"Vyaparam scan degraded: {len(skipped)}/{len(tickers)} tickers failed to fetch.")

    # FII/DII flow context (best-effort; degrades to None if NSE blocks us).
    flow = fii_dii_flow.fetch_fii_dii()
    flow_line = fii_dii_flow.flow_context_line(flow)

    if not hits:
        print("No new Wyckoff signals today.")
        if flow_line:
            notify.send_message(f"Vyaparam: no new signals today.\n{flow_line}")
        return

    tier_order = {"HIGH": 0, "MEDIUM": 1, "REVIEW": 2}
    tier_label_map = {"HIGH": "\u2b50\u2b50\u2b50", "MEDIUM": "\u2b50\u2b50", "REVIEW": "\u2b50"}

    actionable_signals = []  # (sym, tier_label, [lines])
    watchlist_signals = []   # (sym, [lines])

    for sym, result in hits:
        if result["actionable_events"]:
            best_tier = "REVIEW"
            lines = []
            for setup_type, thesis in result["actionable_events"]:
                tier_info = get_confidence_tier(setup_type, regime_aligned=True)
                if tier_order.get(tier_info["tier"], 99) < tier_order.get(best_tier, 99):
                    best_tier = tier_info["tier"]
                lines.append(thesis)
            lines.append(result["context_line"])
            lines.append(result["regime_line"])
            actionable_signals.append((sym, tier_label_map[best_tier], lines))

        if result["filtered_events"]:
            lines = result["filtered_events"] + [result["regime_line"]]
            watchlist_signals.append((sym, lines))

    # Console summary
    for sym, result in hits:
        print(f"{sym}:")
        for setup_type, thesis in result["actionable_events"]:
            tier_info = get_confidence_tier(setup_type, regime_aligned=True)
            print(f"  {tier_info['label']} {thesis}")
        for thesis in result["filtered_events"]:
            print(f"  [WATCHLIST] {thesis}")

    tickers_with_actionable = [s[0] for s in actionable_signals]
    tickers_str = ", ".join(tickers_with_actionable[:8])
    more = f" +{len(tickers_with_actionable) - 8} more" if len(tickers_with_actionable) > 8 else ""
    print()
    print(f"SUMMARY: {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist-only signals -- {tickers_str}{more}")

    regime_line = regime_filter.regime_context_line(regime_info)
    header_bits = [
        f"Vyaparam (Nifty 50): {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist signals.",
        f"{regime_line}.",
    ]
    if flow_line:
        header_bits.append(flow_line + ".")
    header_bits.append("Tiers from US-calibrated priors (\u2b50\u2b50\u2b50=HIGH, \u2b50\u2b50=MEDIUM, \u2b50=REVIEW ONLY).")
    header = " ".join(header_bits)

    notify.notify_signals_tiered(header, actionable_signals, watchlist_signals)


if __name__ == "__main__":
    main()
