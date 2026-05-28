#!/bin/bash
# Wrapper so launchd/cron run the refresh with the right cwd and interpreter.
cd "/Users/jef/Claude/Brick Scanner" || exit 1
exec /usr/bin/python3 refresh_catalog.py "$@"
