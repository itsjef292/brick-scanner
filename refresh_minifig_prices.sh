#!/bin/bash
# Wrapper so launchd runs the minifig price refresh with the right cwd and
# interpreter. Self-locating: cd to this script's own directory (project root).
cd "$(cd "$(dirname "$0")" && pwd)" || exit 1
exec /usr/bin/python3 refresh_minifig_prices.py "$@"
