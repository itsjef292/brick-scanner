#!/usr/bin/env python3
"""Daily BrickLink price refresh for the local My Sets collection.

Fetches each owned set's last-6-month SOLD average (Used + New) from
BrickLink and stores it back into `.set_meta.json`
(`price_used`/`price_new`/`price_updated`, alongside condition/price_paid).

Run by the launchd LaunchAgent `com.brickscanner.set-prices` at 05:30 local
(see com.brickscanner.set-prices.plist) — offset from the 05:00 minifig-prices
job so the two BrickLink refreshes don't overlap. LOCAL-ONLY — does nothing
useful on Render (the meta store there is empty/ephemeral). Logs to
set_prices.log.

Manual run:  python3 refresh_set_prices.py
"""
import os
import sys
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(_HERE, "set_prices.log")


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
    from app import refresh_set_prices
    _log("Starting set price refresh…")
    try:
        summary = refresh_set_prices()
        _log(f"Done: {summary}")
    except Exception as e:
        _log(f"ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
