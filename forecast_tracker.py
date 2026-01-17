#!/usr/bin/env python3
import requests
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
import json, os

# =========================
# CONFIG
# =========================
FORECAST_AREAS = [
    ("New York, NY", "KXHIGHNY", "https://forecast.weather.gov/MapClick.php?lat=40.78&lon=-73.97"),
    ("Miami, FL", "KXHIGHMIA", "https://forecast.weather.gov/MapClick.php?lat=25.76&lon=-80.19"),
]

SHEET_URL = "https://docs.google.com/spreadsheets/d/1DtRLA88PCDRD3DFd6r9H1HN5Q7K2nK075LNbbXpy7sU/edit"

# =========================
# GOOGLE SHEETS
# =========================
def init_sheet():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(SHEET_URL)

    try:
        ws = sh.worksheet("FORECASTS_30")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="FORECASTS_30", rows=10000, cols=20)

    if not ws.row_values(1):
        ws.append_row([
            "Timestamp",
            "AreaName",
            "Code",
            "HighToday",
            "LowToday",
            "HighTomorrow",
            "LowTomorrow",
            "LastUpdate",
            "ForecastValid",
            "Specs",
            "Summary"
        ])

    return ws

# =========================
# FORECAST PARSER (NO BS4)
# =========================
def fetch_forecast(url):
    html = requests.get(url, timeout=10).text

    def strip_tags(s):
        out, inside = "", False
        for c in s:
            if c == "<":
                inside = True
            elif c == ">":
                inside = False
            elif not inside:
                out += c
        return out.strip()

    def clean(s):
        return (
            s.replace("&deg;", "°")
             .replace("&nbsp;", " ")
             .replace("\n", " ")
             .replace("\t", " ")
             .strip()
        )

    def first_int(text):
        num = ""
        for c in text:
            if c.isdigit():
                num += c
            elif num:
                break
        return num

    highs, lows = [], []

    for b in html.split('class="temp temp-high"')[1:]:
        t = first_int(strip_tags(b))
        if t:
            highs.append(t)

    for b in html.split('class="temp temp-low"')[1:]:
        t = first_int(strip_tags(b))
        if t:
            lows.append(t)

    high_today    = highs[0] if len(highs) > 0 else ""
    low_today     = lows[0] if len(lows) > 0 else ""
    high_tomorrow = highs[1] if len(highs) > 1 else ""
    low_tomorrow  = lows[1] if len(lows) > 1 else ""

    summary_parts = []
    for block in html.split('class="short-desc"')[1:5]:
        part = block.split("</p>", 1)[0]
        part = part.replace("<br>", " ")
        part = clean(strip_tags(part))
        if part:
            summary_parts.append(part)

    summary = " | ".join(summary_parts)

    specs = ""
    if "current_conditions_detail" in html:
        block = html.split("current_conditions_detail", 1)[1].split("</table>", 1)[0]
        cells = [
            clean(strip_tags(c))
            for c in block.split("<td>")[1:]
            if clean(strip_tags(c))
        ]
        specs = "; ".join(cells)

    last_update = ""
    if "Last Update" in html:
        block = html.split("Last Update", 1)[1]
        block = block.split('class="right">', 1)[1]
        last_update = clean(strip_tags(block.split("</div>", 1)[0]))

    forecast_valid = ""
    if "Forecast Valid" in html:
        block = html.split("Forecast Valid", 1)[1]
        block = block.split('class="right">', 1)[1]
        forecast_valid = clean(strip_tags(block.split("</div>", 1)[0]))

    return (
        high_today,
        low_today,
        high_tomorrow,
        low_tomorrow,
        last_update,
        forecast_valid,
        specs,
        summary
    )

# =========================
# MAIN (ONE SHOT – GITHUB CRON)
# =========================
def main():
    ws = init_sheet()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for area, code, url in FORECAST_AREAS:
        (
            ht,
            lt,
            ht2,
            lt2,
            last_upd,
            valid,
            specs,
            summary
        ) = fetch_forecast(url)

        ws.append_row([
            ts,
            area,
            code,
            ht,
            lt,
            ht2,
            lt2,
            last_upd,
            valid,
            specs,
            summary
        ])

if __name__ == "__main__":
    main()
