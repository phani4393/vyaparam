# Vyāpāram (व्यापारम्) — Indian Stock Options Scanner

An automated, Wyckoff-based market scanner for **Indian markets (NSE)**, built
as a sibling to the US-focused [`wyckoff-scanner-ci-personal`](../wyckoff-scanner-ci-personal).
It watches the Nifty 50, and when a Wyckoff-relevant event appears it pushes a
"go look at this chart" alert to Telegram — with the directional bias for a
long call/put and a realized-vol expected move for strike sanity. It also
layers in **FII/DII cash-flow** context, which is a distinctively Indian signal.

> **Read this first — honesty about what this is.**
> This reuses the exact detector logic from the US Wyckoff scanner, where
> backtesting showed **none of these signals beat a naive "trade the swing"
> baseline** — there is **no validated mechanical edge**. So this is a
> *discretionary review aid*, not a buy/sell system. Every alert is a prompt
> for **your** judgment (context, IV, events, FII/DII flow), never an
> instruction. The same honesty applies to Indian markets: these are
> review triggers, not a tested edge on NSE data.

---

## Why a separate repo instead of flags on the US scanner

The detector math (`patterns.py`, ATR/pivots/zigzag, spring/upthrust) is
market-agnostic — it operates on OHLCV bars and nothing else — so it is reused
verbatim here. Only three things are India-specific, and they are the only
things this repo changes:

1. **Data layer** — Yahoo Finance (`RELIANCE.NS`, `^NSEI`) as the free primary
   source, since Twelve Data's free tier does **not** cover NSE (verified live
   2026-10-24 — both equities and the NIFTY 50 index return empty). Twelve Data
   (`RELIANCE:NSE`) is kept as an optional fallback for anyone on a paid plan.
2. **Universe** — `data/nifty50.csv` instead of the S&P names.
3. **FII/DII flow** — foreign/domestic institutional net cash flow from NSE's
   public data, which has no US analogue and is a widely-watched India signal.

---

## What's in here

```
vyaparam/
├── README.md                     <- you are here
├── requirements.txt
├── .env.example
├── .gitignore
├── .github/workflows/
│   └── nifty-scan.yml            <- daily scan of the Nifty 50 (IST-aware cron)
├── src/
│   ├── nse_common.py             <- data fetch (Twelve Data NSE + Yahoo fallback), ATR, pivots, zigzag
│   ├── patterns.py               <- Wyckoff detectors (REUSED from the US scanner, market-agnostic)
│   ├── regime_filter.py          <- NIFTY-50 > 200d SMA regime gating
│   ├── fii_dii_flow.py           <- foreign/domestic institutional net cash flow (NSE public data)
│   ├── pretrade.py               <- realized-vol expected move (strike sanity)
│   ├── notify.py                 <- Telegram push
│   └── nifty_scanner.py          <- the scanner entry point
├── data/
│   └── nifty50.csv               <- the Nifty 50 universe
└── tests/
    └── test_patterns.py          <- detector regression tests (synthetic OHLC)
```

---

## Setup

1. **No market-data key needed.** Yahoo Finance serves NSE daily bars for free.
   (Optional: a *paid* Twelve Data key set as `TWELVEDATA_API_KEY` adds a
   fallback source — the free tier does not return NSE data, so it's useless
   here.)
2. Telegram bot via @BotFather → `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Copy `.env.example` to `.env` (local) or set repo secrets (CI).

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in your Telegram keys
```

## Run a scan

```bash
# locally (reads env vars from your shell / .env):
python src/nifty_scanner.py

# just the FII/DII flow snapshot:
python src/fii_dii_flow.py

# check the current NIFTY regime:
python src/regime_filter.py
```

## How it runs headless

`.github/workflows/nifty-scan.yml` runs on a schedule keyed to the **NSE close
(~15:30 IST)** and pushes results to Telegram. GitHub cron is fixed UTC and
India does **not** observe DST, so a single cron entry (`10:05 UTC` ≈ `15:35
IST`) stays aligned year-round — simpler than the US scanner's dual DST crons.

## Data & limits (honest)

- **Data:** Yahoo Finance daily bars (free, unofficial chart endpoint), delayed.
  No options/IV, no survivorship-free history. Twelve Data's free tier does NOT
  serve NSE (verified live), so it's only a fallback on a paid plan. Yahoo's
  endpoint is unofficial and can rate-limit or change shape without notice —
  if it does, a run degrades visibly (skipped symbols) rather than faking data.
- **FII/DII flow** comes from NSE's public figures and is best-effort scraping;
  any failure degrades gracefully to "flow unavailable", it never blocks a scan.
- **No validated edge.** Same conclusion as the US scanner — treat every number
  as descriptive, not predictive, and trade your own judgment.
