#!/usr/bin/env python3
import os
import base64
import requests
from datetime import datetime, timezone
import re

import gspread
from google.oauth2.service_account import Credentials
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# =========================
# CONFIG
# =========================
SHEET_URL = "https://docs.google.com/spreadsheets/d/1CwsxbjF1AajK8O0mdLOpKg7BXGlJM4-MYTeH672P9hg/edit"
TAB_NAME = "kalshi past bids"

SERVICE_ACCOUNT_FILE = "service_account.json"

KALSHI_API_KEY_ID = os.environ["KALSHI_API_KEY_ID"]
KALSHI_PRIVATE_KEY_PEM = os.environ["KALSHI_PRIVATE_KEY_PEM"]

KALSHI_BASE_URL = "https://api.elections.kalshi.com"

MAX_OPTIONS = 10
HTTP_TIMEOUT = 15

# =========================
# AUTH
# =========================
def load_private_key():
    return serialization.load_pem_private_key(
        KALSHI_PRIVATE_KEY_PEM.encode("utf-8"),
        password=None
    )

def sign_pss_text(private_key, text: str) -> str:
    sig = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(sig).decode("utf-8")

def kalshi_headers(method: str, path: str):
    ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    msg = ts + method + path
    sig = sign_pss_text(load_private_key(), msg)
    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "Content-Type": "application/json",
    }

# =========================
# GOOGLE SHEETS
# =========================
def init_sheet():
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    sh = gspread.authorize(creds).open_by_url(SHEET_URL)

    try:
        ws = sh.worksheet(TAB_NAME)
    except:
        ws = sh.add_worksheet(title=TAB_NAME, rows=10000, cols=80)

    return ws

def build_header():
    header = [
        "fetched_at","order_id","event_ticker","ticker","side","shares",
        "entry_price_cents","total_cost_dollars",
        "market_result","won_lost","pnl_dollars",
        "current_yes_price_cents","current_implied_win_pct",
        "created_time","market_url"
    ]
    for i in range(1, MAX_OPTIONS + 1):
        header.append(f"option{i:02d}")
    for i in range(1, MAX_OPTIONS + 1):
        header.append(f"option{i:02d}_label")
    return header

def rebuild_sheet(ws, rows):
    ws.clear()
    ws.append_row(build_header())
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")

# =========================
# KALSHI READS
# =========================
def fetch_orders():
    path = "/trade-api/v2/portfolio/orders"
    r = requests.get(
        KALSHI_BASE_URL + path,
        headers=kalshi_headers("GET", path),
        timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    return r.json().get("orders", [])

def fetch_market(ticker: str):
    r = requests.get(
        f"{KALSHI_BASE_URL}/trade-api/v2/markets/{ticker}",
        timeout=HTTP_TIMEOUT
    )
    if r.status_code != 200:
        return {}
    return r.json().get("market", {}) or {}

def fetch_event_markets(event_ticker: str):
    r = requests.get(
        f"{KALSHI_BASE_URL}/trade-api/v2/markets",
        params={"event_ticker": event_ticker, "limit": 200},
        timeout=HTTP_TIMEOUT
    )
    if r.status_code != 200:
        return []

    markets = r.json().get("markets", []) or []

    def strike_sort_key(m):
        t = m.get("ticker", "")
        m2 = re.search(r"-(?:B|T)-?(\d+(?:\.5)?)$", t)
        if not m2:
            return 10**9
        return float(m2.group(1))

    return sorted(markets, key=strike_sort_key)

def derive_event_ticker_from_market_ticker(ticker: str) -> str:
    # Removes trailing temp outcome only:
    # -B21.5, -T45, -B-6.5, -T-13
    return re.sub(r"-(?:B|T)-?\d+(?:\.5)?$", "", ticker)


def get_entry_price_cents(order: dict) -> int | None:
    side = order.get("side")
    if side == "yes" and order.get("yes_price") is not None:
        return int(order["yes_price"])
    if side == "no" and order.get("no_price") is not None:
        return int(order["no_price"])

    for k in ("price", "fill_price", "avg_price", "average_price"):
        v = order.get(k)
        if v is not None:
            try:
                return int(v)
            except:
                pass
    return None

# =========================
# MAIN
# =========================
def main():
    ws = init_sheet()

    orders = fetch_orders()
    rows = []

    event_markets_cache = {}
    market_cache = {}

    for o in orders:
        oid = o.get("order_id") or ""
        ticker = o.get("ticker")
        if not ticker:
            continue

        event_ticker = derive_event_ticker_from_market_ticker(ticker)

        side = (o.get("side") or "").lower()
        shares = int(o.get("fill_count") or 0)
        price = get_entry_price_cents(o)

        if shares == 0 or price is None or side not in ("yes", "no"):
            continue

        total_cost = (price * shares) / 100.0

        if event_ticker not in event_markets_cache:
            event_markets_cache[event_ticker] = fetch_event_markets(event_ticker)

        markets = event_markets_cache[event_ticker]

        option_marks = [""] * MAX_OPTIONS
        option_labels = [""] * MAX_OPTIONS

        for i, m in enumerate(markets[:MAX_OPTIONS]):
            label = (
                m.get("subtitle")
                or m.get("title")
                or m.get("description")
                or m.get("yes_title")
                or m.get("no_title")
                or ""
            )
            option_labels[i] = label

            if m.get("ticker") == ticker:
                option_marks[i] = "YES" if side == "yes" else "NO"

        if ticker not in market_cache:
            market_cache[ticker] = fetch_market(ticker)

        market = market_cache[ticker]

        market_result = (market.get("result") or "").upper()

        # ---- LIVE PRICE / IMPLIED PROBABILITY
        current_yes = (
            market.get("last_price")
            or market.get("yes_bid")
            or market.get("yes_ask")
        )

        implied_pct = ""
        if current_yes is not None:
            if side == "yes":
                implied_pct = round(current_yes / 100, 4)
            else:
                implied_pct = round((100 - current_yes) / 100, 4)

        won_lost = ""
        pnl = ""

        if market_result in ("YES", "NO"):
            win = (side == "yes" and market_result == "YES") or (side == "no" and market_result == "NO")
            pnl_val = ((100 - price) * shares) / 100.0 if win else -(price * shares) / 100.0
            pnl = f"{pnl_val:.2f}"
            won_lost = "WON" if win else "LOST"

        rows.append([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            oid,
            event_ticker,
            ticker,
            side,
            shares,
            price,
            f"{total_cost:.2f}",
            market_result,
            won_lost,
            pnl,
            current_yes,
            implied_pct,
            o.get("created_time") or "",
            f"https://kalshi.com/markets/{ticker}",
            *option_marks,
            *option_labels
        ])

    rebuild_sheet(ws, rows)
    print(f"✅ Rebuilt sheet with {len(rows)} rows")

if __name__ == "__main__":
    main()
