#!/usr/bin/env python3
import requests
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
import json, os

SERIES = ["KXHIGHNY", "KXLOWTNYC", "KXHIGHMIA", "KXLOWTMIA"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/1DtRLA88PCDRD3DFd6r9H1HN5Q7K2nK075LNbbXpy7sU/edit"
BASE = "https://api.elections.kalshi.com"

# =========================
# GOOGLE SHEETS
# =========================
def init_sheets():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(SHEET_URL)
    out = {}

    for s in SERIES:
        try:
            ws = sh.worksheet(s)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=s, rows=12000, cols=40)

        if not ws.row_values(1):
            ws.append_row([
                "Timestamp","Series","Event","Market","Strike",
                "Floor","Ceiling",
                "YesBid","YesAsk","NoBid","NoAsk","YesProb",
                "YesDepth","NoDepth",
                "Volume","OpenInterest",
                "LastPrice","MarketCreated",
                "EventOpen","EventClose",
                "MarketOpen","MarketClose",
                "Station","Rules"
            ])
        out[s] = ws

    return out

# =========================
# KALSHI API
# =========================
def get_events(series):
    return requests.get(
        f"{BASE}/trade-api/v2/events",
        params={"series_ticker": series, "status": "open"},
        timeout=10
    ).json().get("events", [])

def get_event_details(event):
    return requests.get(
        f"{BASE}/trade-api/v2/events/{event}",
        timeout=10
    ).json()["event"]

def get_markets(event):
    return requests.get(
        f"{BASE}/trade-api/v2/markets",
        params={"event_ticker": event, "limit": 100},
        timeout=10
    ).json().get("markets", [])

def get_market_details(ticker):
    return requests.get(
        f"{BASE}/trade-api/v2/markets/{ticker}",
        timeout=10
    ).json()["market"]

def get_orderbook(ticker):
    return requests.get(
        f"{BASE}/trade-api/v2/markets/{ticker}/orderbook",
        timeout=10
    ).json().get("orderbook", {})

def best(levels):
    if not isinstance(levels, list):
        return 0
    prices = [x[0] for x in levels if isinstance(x, list)]
    return max(prices) if prices else 0

def depth(levels):
    if not isinstance(levels, list):
        return 0
    return sum(x[0] * x[1] for x in levels if len(x) == 2) / 100

# =========================
# MAIN
# =========================
def main():
    sheets = init_sheets()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for series in SERIES:
        ws = sheets[series]
        events = get_events(series)

        for e in events:
            et = e["event_ticker"]
            ed = get_event_details(et)
            event_close = ed.get("strike_date", "")

            markets = get_markets(et)
            market_details = {}
            opens = []

            for m in markets:
                md = get_market_details(m["ticker"])
                market_details[m["ticker"]] = md
                if md.get("open_time"):
                    opens.append(md["open_time"])

            event_open = min(opens) if opens else ""

            for m in markets:
                t = m["ticker"]
                md = market_details[t]
                ob = get_orderbook(t)

                y = ob.get("yes", [])
                n = ob.get("no", [])

                yb = best(y)
                nb = best(n)
                ya = 100 - nb
                na = 100 - yb

                ws.append_row([
                    ts, series, et, t, md.get("subtitle", ""),
                    md.get("floor_strike", ""), md.get("cap_strike", ""),
                    yb, ya, nb, na, round(ya / 100, 4) if ya else 0,
                    round(depth(y), 2), round(depth(n), 2),
                    md.get("volume", 0), md.get("open_interest", 0),
                    md.get("last_price", ""),
                    md.get("created_time", ""),
                    event_open, event_close,
                    md.get("open_time", ""),
                    md.get("close_time", ""),
                    (
                        md.get("rules_primary", "").split("http")[1].split(" ")[0]
                        if "http" in md.get("rules_primary", "") else ""
                    ),
                    md.get("rules_primary", "")
                ])

if __name__ == "__main__":
    main()
