"""
Telegram notification for the headless GitHub Actions runs. Mirrors the US
scanner's wyckoff_notify.py -- there's no interactive session in CI, so the
scanner pushes to Telegram itself via the Bot API.

Requires two env vars (repo secrets in CI):
  TELEGRAM_BOT_TOKEN -- from @BotFather
  TELEGRAM_CHAT_ID   -- your personal chat id
"""

import os

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _configured():
    return bool(BOT_TOKEN and CHAT_ID)


def send_message(text):
    if not _configured():
        print("Telegram not configured (missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) -- skipping notification.")
        return
    url = API_BASE.format(token=BOT_TOKEN, method="sendMessage")
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    if not resp.ok:
        print(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")


def notify_signals_tiered(header, actionable_signals, watchlist_signals=None):
    """
    Send notifications with separate sections for actionable vs watchlist-only
    signals.

    actionable_signals: list of (sym, tier_label, [line, ...])
    watchlist_signals:  list of (sym, [line, ...]) for regime-filtered signals
    """
    if not actionable_signals and not watchlist_signals:
        return

    body_lines = [header, ""]

    tier_order = {"\u2b50\u2b50\u2b50": 0, "\u2b50\u2b50": 1, "\u2b50": 2}
    if actionable_signals:
        actionable_sorted = sorted(actionable_signals, key=lambda x: tier_order.get(x[1], 99))
        body_lines.append("\u2705 ACTIONABLE ALERTS:")
        for sym, tier_label, lines in actionable_sorted:
            body_lines.append(f"{tier_label} {sym}:")
            for line in lines:
                body_lines.append("  - " + line)
        body_lines.append("")

    if watchlist_signals:
        body_lines.append("\U0001f4cb WATCHLIST (regime-filtered, monitor only):")
        for sym, lines in watchlist_signals:
            body_lines.append(f"{sym}:")
            for line in lines:
                clean_line = line.replace("[REGIME-FILTERED] ", "")
                body_lines.append("  - " + clean_line)

    send_message("\n".join(body_lines)[:4000])
