#!/usr/bin/env bash
# Install/refresh the Brick Scanner systemd units on a Linux host (Ubuntu/Debian).
#
# Linux counterpart of install_agents.sh (which does the same job for macOS
# launchd). Substitutes the real project dir, user, and venv python path into
# the *.service / *.timer templates in this directory, installs them to
# /etc/systemd/system, then enables + starts them.
#
# Run from anywhere (it self-locates):  sudo ./deploy/linux/install_systemd.sh
#
# Re-running is safe — it overwrites the unit files and reloads systemd.
set -euo pipefail

# --- locate things ---------------------------------------------------------
HERE="$(cd "$(dirname "$0")" && pwd)"          # deploy/linux
PROJECT_DIR="$(cd "$HERE/../.." && pwd)"       # repo root
VENV_PY="$PROJECT_DIR/venv/bin/python3"

# The user the services run as = the owner of the project dir (not root, so the
# app reads/writes .env + the .json collection files under a normal account).
RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"

# --- sanity checks ---------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo (installs to /etc/systemd/system):"
  echo "  sudo $0"
  exit 1
fi

if [ ! -x "$VENV_PY" ]; then
  echo "✗ venv python not found at: $VENV_PY"
  echo "  Create it first:"
  echo "    cd \"$PROJECT_DIR\" && python3 -m venv venv && \\"
  echo "    ./venv/bin/pip install flask requests python-dotenv requests-oauthlib"
  exit 1
fi

echo "Project : $PROJECT_DIR"
echo "User    : $RUN_USER"
echo "Python  : $VENV_PY"
echo

UNITS=(
  brick-scanner.service
  brick-scanner-catalog-refresh.service
  brick-scanner-catalog-refresh.timer
  brick-scanner-minifig-prices.service
  brick-scanner-minifig-prices.timer
)

# --- substitute + install --------------------------------------------------
for u in "${UNITS[@]}"; do
  sed -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
      -e "s#__USER__#$RUN_USER#g" \
      -e "s#__PY__#$VENV_PY#g" \
      "$HERE/$u" > "/etc/systemd/system/$u"
  echo "  installed /etc/systemd/system/$u"
done

# --- enable + start --------------------------------------------------------
systemctl daemon-reload
systemctl enable --now brick-scanner.service
systemctl enable --now brick-scanner-catalog-refresh.timer
systemctl enable --now brick-scanner-minifig-prices.timer

echo
echo "✓ Installed and started. Quick checks:"
echo "    systemctl status brick-scanner --no-pager"
echo "    systemctl list-timers 'brick-scanner-*' --no-pager"
echo "    journalctl -u brick-scanner -f"
