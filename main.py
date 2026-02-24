#!/usr/bin/env python3
"""
GATE 2026 Rank Checker
~~~~~~~~~~~~~~~~~~~~~~
Fetches your GATE rank/score stats from GATEOverflow and sends them to
Telegram every 4 minutes.

Setup
-----
1. Create a virtual environment and install dependencies:
       python3 -m venv venv
       source venv/bin/activate      # Windows: venv\\Scripts\\activate
       pip install requests

2. Fill in the CONFIG section below.

3. Run:
       python gate_rank_checker_sample.py
"""

import socket
import time
from collections import deque
import requests
import os

# ── CONFIG ───────────────────────────────────────────────────────────────────

# Your GATE 2026 response sheet URL.
# Find it on the official GATE portal after results — it looks like:
#   https://cdn.digialm.com//per/g01/pub/585/touchstone/.../<your_file>.html
RESPONSE_SHEET_URL = os.environ.get("RESPONSE_SHEET_URL")

# Telegram Bot Token
# 1. Open Telegram and search for @BotFather
# 2. Send /newbot and follow the prompts
# 3. BotFather will give you a token like: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Telegram Chat ID (where the bot should send messages — your personal chat)
# 1. Start a chat with your bot (search its username and press Start)
# 2. Open Telegram and search for @userinfobot
# 3. Send /start — it will reply with your Chat ID (a plain number like 987654321)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

INTERVAL_SECONDS = 15 * 60  # 15 minutes — change freely

# ─────────────────────────────────────────────────────────────────────────────

RESPONSE_API_URL     = "https://rank.gateoverflow.in/mymarks/responseurl_process.php"
GETRANK_API_URL      = "https://rank.gateoverflow.in/mymarks/api_server/getrank.php"
_RETRY_MSG    = f"Will retry in {INTERVAL_SECONDS // 60} minutes."
_HISTORY_SIZE = 60 * 60 // INTERVAL_SECONDS        # 15 slots = 1 hour at 4-min interval
_norm_history: deque[float] = deque(maxlen=_HISTORY_SIZE)


def is_internet_up(host: str = "8.8.8.8", port: int = 53, timeout: int = 5) -> bool:
    """TCP probe to Google DNS."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
        return True
    except OSError:
        return False


def send_telegram(session: requests.Session, message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = session.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()
        print("[Telegram] Message sent.")
    except Exception as e:
        print(f"[Telegram] Failed: {e}")


def _get_json(session: requests.Session, url: str, params: dict) -> dict | None:
    """GET a URL and return parsed JSON, or None on any failure."""
    try:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[API] Request failed ({url}): {e}")
        return None
    except ValueError as e:
        print(f"[API] JSON parse error ({url}): {e}")
        return None


def fmt(val, suffix: str = "") -> str:
    return f"{val}{suffix}" if val is not None else "N/A"


def fmt_delta(normalised: float | None, raw: float | None) -> str:
    """Show how much normalization added/removed vs raw marks."""
    if normalised is None or raw is None:
        return "N/A"
    delta = round(normalised - raw, 2)
    if delta > 0:
        return f"▲ +{delta}"
    elif delta < 0:
        return f"▼ {delta}"
    else:
        return "● no change"


def format_message(rd: dict, rk: dict, history: deque) -> str:
    norm_mark = rk.get("normalized_mark")
    raw_marks = rd.get("total_marks")

    if history:
        rolling_mean = round(sum(history) / len(history), 2)
        rolling_str  = f"{rolling_mean}  ({len(history)}/{_HISTORY_SIZE} samples)"
    else:
        rolling_str  = "—"

    def row(label: str, value: str) -> str:
        return f"{label:<18}{value}"

    lines = [
        row("Raw Marks:",     fmt(raw_marks)),
        row("Normalised:",    f"{fmt(norm_mark)}  {fmt_delta(norm_mark, raw_marks)}"),
        row("1hr Mean:",      rolling_str),
        row("Est. Score:",    fmt(rk.get("score_estimate"))),
        "",
        row("Est. Rank:",     fmt(rk.get("rank_estimate"))),
        row("Norm. Rank:",    fmt(rk.get("rank_normalized"))),
        row(f"Set {rd.get('set','?')} Rank:", f"{fmt(rk.get('rank_in_set'))} / {fmt(rk.get('total_in_set'))}"),
        row("All Sets:",      f"{fmt(rk.get('total_in_all_sets'))} responses"),
        "",
        row("Correct:",       f"{fmt(rd.get('total_positive'))} ({fmt(rd.get('total_positive_percentage'), '%')})"),
        row("-ve (1M/2M):",   f"{fmt(rd.get('one_mark_negative'))} / {fmt(rd.get('two_marks_negative'))}"),
        row("Attempted:",     f"{fmt(rd.get('total_attempted'))} / 65"),
    ]

    return "📊 <b>GATE 2026 Rank Update</b>\n<pre>" + "\n".join(lines) + "</pre>"


def run_once(session: requests.Session, response_data: dict) -> None:
    marks   = response_data["total_marks"]
    set_num = response_data["set"]
    branch  = response_data["branch"]

    print(f"\n[{time.strftime('%H:%M:%S')}] Fetching rank stats...")

    if not is_internet_up():
        msg = f"🌐 Internet is down. {_RETRY_MSG}"
        print(f"[Network] {msg}")
        send_telegram(session, msg)
        return

    rank_data = _get_json(session, GETRANK_API_URL, {"mymarks": marks, "set_num": set_num, "branch": branch})
    if rank_data is None:
        msg = f"🔴 Rank API is unreachable. {_RETRY_MSG}"
        print(f"[Server] {msg}")
        send_telegram(session, msg)
        return

    norm_mark = rank_data.get("normalized_mark")
    if norm_mark is None:
        msg = f"⚠️ Rank API response missing normalized_mark. {_RETRY_MSG}"
        print(f"[API] {msg}")
        send_telegram(session, msg)
        return

    # Append before formatting so the current value is included in the rolling mean
    _norm_history.append(norm_mark)

    msg = format_message(response_data, rank_data, _norm_history)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", "").replace("<code>", "").replace("</code>", ""))
    send_telegram(session, msg)


def main() -> None:
    # Basic config validation before starting
    if not RESPONSE_SHEET_URL or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Missing required environment variables.")
        return


    with requests.Session() as session:
        # Fetch response sheet once — raw marks/set/branch never change
        print("[Startup] Fetching response sheet data...")
        response_data = _get_json(session, RESPONSE_API_URL, {"url": RESPONSE_SHEET_URL})
        if response_data is None:
            print("[Startup] Failed to fetch response sheet. Exiting.")
            return

        marks   = response_data.get("total_marks")
        set_num = response_data.get("set")
        branch  = response_data.get("branch")

        if None in (marks, set_num, branch):
            print("[Startup] Response sheet missing marks/set/branch. Exiting.")
            return

        print(f"[Startup] Got: marks={marks}, set={set_num}, branch={branch}")
        send_telegram(session, f"🚀 <b>GATE Rank Checker started!</b>\nRaw Marks: <code>{marks}</code>  |  Branch: <code>{branch}</code>  |  Set: <code>{set_num}</code>\nPolling every {INTERVAL_SECONDS // 60} minutes…")

        while True:
            run_once(session, response_data)
            print(f"[Scheduler] Sleeping {INTERVAL_SECONDS // 60} minutes…")
            time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
