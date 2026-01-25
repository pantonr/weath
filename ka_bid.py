#!/usr/bin/env python3
import os
import requests
import base64
import re
from datetime import datetime, timedelta, timezone, date
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# =========================
# CONFIG
# =========================
KALSHI_API_BASE = "https://api.elections.kalshi.com"
KALSHI_API_KEY_ID = os.environ["KALSHI_API_KEY_ID"]
KALSHI_PRIVATE_KEY_PEM = os.environ["KALSHI_PRIVATE_KEY_PEM"]

LIVE_TRADING = False   # <<< ONLY SWITCH YOU EVER TOUCH
ORDER_SIDE = "no"
ORDER_COUNT = 1

MARKETS = {
    "KXHIGHNY": {
        "label": "New York, NY (High)",
        "type": "high",
        "noaa_url": "https://forecast.weather.gov/MapClick.php?lat=40.78&lon=-73.97",
    },
    "KXLOWTNYC": {
        "label": "New York, NY (Low)",
        "type": "low",
        "noaa_url": "https://forecast.weather.gov/MapClick.php?lat=40.78&lon=-73.97",
    },
    "KXHIGHMIA": {
        "label": "Miami, FL (High)",
        "type": "high",
        "noaa_url": "https://forecast.weather.gov/MapClick.php?lat=25.76&lon=-80.19",
    },
    "KXLOWTMIA": {
        "label": "Miami, FL (Low)",
        "type": "low",
        "noaa_url": "https://forecast.weather.gov/MapClick.php?lat=25.76&lon=-80.19",
    },
}

# =========================
# AUTH
# =========================
def load_private_key():
    return serialization.load_pem_private_key(
        KALSHI_PRIVATE_KEY_PEM.encode(),
        password=None,
    )

def sign_pss_text(private_key, text):
    sig = private_key.sign(
        text.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()

def kalshi_headers(method, path):
    ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    msg = ts + method + path
    key = load_private_key()
    sig = sign_pss_text(key, msg)

    return {
        "KALSHI-ACCESS-KEY": KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": sig,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "Content-Type": "application/json",
    }

# =========================
# NOAA
# =========================
def get_noaa_temp(cfg):
    r = requests.get(cfg["noaa_url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()

    soup = BeautifulSoup(r.content, "html.parser")

    for item in soup.select("li.forecast-tombstone"):
        period_el = item.select_one(".period-name")
        if not period_el:
            continue

        period = period_el.text.strip()

        if cfg["type"] == "low" and period.lower() not in ("tonight", "overnight"):
            continue

        temp_el = (
            item.select_one(".temp-high")
            if cfg["type"] == "high"
            else item.select_one(".temp-low")
        )

        if not temp_el:
            continue

        raw = temp_el.text.strip()
        m = re.search(r"(\d+)", raw)
        parsed = int(m.group(1)) if m else None

        return period, raw, parsed

    return None, None, None

# =========================
# KALSHI HELPERS
# =========================
def kalshi_fragment_for_date(d: date):
    return d.strftime("%y%b%d").upper()

def strike_from_ticker(ticker):
    m = re.search(r'-(?:B|T)(\d+(?:\.5)?)$', ticker)
    return float(m.group(1)) if m else None

# =========================
# MAIN
# =========================
def main():
    obs_date = datetime.now().date()
    kalshi_day = kalshi_fragment_for_date(obs_date)

    path = "/trade-api/v2/markets"

    print(f"\nKALSHI + NOAA BRACKET MAP — {kalshi_day}\n")

    for series, cfg in MARKETS.items():
        print(cfg["label"])

        period, raw, temp = get_noaa_temp(cfg)
        print(f"  NOAA Period:   {period}")
        print(f"  NOAA Raw:      {raw}")
        print(f"  Parsed Temp:   {temp}")

        if temp is None:
            print("  NOAA FAILED")
            print("-" * 60)
            continue

        headers = kalshi_headers("GET", path)
        params = {
            "series_ticker": series,
            "status": "open",
            "limit": 200,
        }

        r = requests.get(
            f"{KALSHI_API_BASE}{path}",
            headers=headers,
            params=params,
            timeout=15,
        )

        if r.status_code != 200:
            print("  KALSHI API ERROR")
            print("-" * 60)
            continue

        markets = [
            m for m in r.json().get("markets", [])
            if kalshi_day in m.get("ticker", "")
        ]

        if not markets:
            print(f"  KALSHI: NO MARKETS FOR {kalshi_day}")
            print("-" * 60)
            continue

        parsed = []
        for m in markets:
            strike = strike_from_ticker(m["ticker"])
            if strike is not None:
                parsed.append((strike, m["ticker"]))

        parsed.sort(key=lambda x: x[0])

        chosen_ticker = None
        for strike, ticker in parsed:
            if temp < strike:
                continue
            if temp < strike + 1:
                print(f"  ✅ BRACKET: {strike}–{strike + 1} °F  ({ticker})")
                chosen_ticker = ticker
                break

        if not chosen_ticker:
            strike, chosen_ticker = parsed[-1]
            print(f"  ⚠️ ABOVE TOP BRACKET: {strike + 1}+ °F  ({chosen_ticker})")

        # =========================
        # LIVE ORDER (DISABLED)
        # =========================
        if LIVE_TRADING:
            order_path = "/trade-api/v2/orders"
            payload = {
                "ticker": chosen_ticker,
                "side": ORDER_SIDE,
                "type": "market",
                "count": ORDER_COUNT,
            }

            resp = requests.post(
                f"{KALSHI_API_BASE}{order_path}",
                headers=kalshi_headers("POST", order_path),
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            print("ORDER PLACED:", resp.json())
        else:
            print("LIVE_TRADING is False — no order sent")

        print("-" * 60)

if __name__ == "__main__":
    main()
