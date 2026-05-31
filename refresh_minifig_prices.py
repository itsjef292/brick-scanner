#!/usr/bin/env python3
"""Daily BrickLink price refresh for the local My Minifigs collection.

Fetches each owned minifig's last-6-month SOLD average (Used + New) from
BrickLink and stores it back into `.minifig_collection.json`
(`price_used`/`price_new`/`price_updated`). Figs without a BrickLink id are
skipped (Rebrickable exposes none).

Run by the launchd LaunchAgent `com.brickscanner.minifig-prices` at 05:00 local
(see com.brickscanner.minifig-prices.plist). LOCAL-ONLY — does nothing useful on
Render (the collection there is empty / ephemeral). Logs to minifig_prices.log.

Manual run:  python3 refresh_minifig_prices.py
"""
import os
import sys
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_HERE, "minifig_prices.log")


def _log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main():
    # Import here so a failure to load the app is logged rather than silent.
    from app import refresh_minifig_prices
    _log("Starting minifig price refresh…")
    try:
        summary = refresh_minifig_prices()
        _log(f"Done: {summary}")
    except Exception as e:
        _log(f"ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
