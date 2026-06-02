# Running Brick Scanner on a Linux VM (Ubuntu / Debian)

This walks through moving the **local** instance from macOS to a Linux VM on your
PC. It does **not** affect the Render production deploy, which keeps running
independently from `main`.

On macOS the app + its two daily jobs run as **launchd** agents
(`install_agents.sh`). On Linux the equivalents are **systemd** units in
`deploy/linux/`, installed by `deploy/linux/install_systemd.sh`. Same schedules,
same behavior — but systemd starts at **boot** (no login required), so the VM is
a true always-on appliance.

> **Doing this with Claude Code on the VM?** Install it on the VM
> (`curl -fsSL https://claude.ai/install.sh | bash`), `cd` into this project, run
> `claude`, and point it at this file. It can run every command below for you.

---

## 1. Provision the VM

- **Distro:** Ubuntu 24.04 LTS (Desktop or Server — both fine). Debian 12 also works.
- **Resources:** 2 vCPU, 2–4 GB RAM, **≥ 15 GB disk** (the offline catalog
  `brick_parts.db` is ~195 MB and the CSV dump needs scratch space to rebuild).
- **Networking:** use **bridged** mode so the VM gets its own LAN IP.

If on Ubuntu **Desktop**, install the SSH server so you can `scp` files in:
```bash
sudo apt install -y openssh-server
```

## 2. Install system dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

## 3. Clone the repo

```bash
git clone <your-brick-scanner-repo-url> brick-scanner
cd brick-scanner
```

## 4. Python virtualenv + dependencies

The systemd units expect a venv at `./venv` (the installer checks for
`./venv/bin/python3`).

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install flask requests python-dotenv requests-oauthlib
```

## 5. Copy the git-ignored files from your Mac

These are **not in the repo** and must be transferred by hand. The two `.json`
files are your **live collection data — don't lose them**.

```bash
# Run this FROM YOUR MAC, in the project dir:
scp .env .set_meta.json .minifig_collection.json \
    <vm-user>@<vm-ip>:~/brick-scanner/
```

(`.catalog_changes.json` / `.catalog_manifest.json` regenerate themselves — no
need to copy them.)

## 6. Build the offline catalog on the VM

Don't copy the 195 MB DB across — rebuild it natively:

```bash
./venv/bin/python3 download_csvs.py
./venv/bin/python3 build_brick_db.py
```

Quick smoke test before installing services:
```bash
./venv/bin/python3 app.py     # should serve on http://0.0.0.0:5001 — Ctrl+C to stop
```

## 7. Install the systemd units

```bash
sudo ./deploy/linux/install_systemd.sh
```

The script auto-detects the project dir, the owning user, and the venv python,
substitutes them into the templates, installs to `/etc/systemd/system`, then
enables + starts:

| Unit | Replaces (launchd) | Schedule |
|------|--------------------|----------|
| `brick-scanner.service` | `com.brickscanner.app` | always-on (Restart=always) |
| `brick-scanner-catalog-refresh.timer` | `com.brickscanner.catalog-refresh` | daily 07:30 |
| `brick-scanner-minifig-prices.timer` | `com.brickscanner.minifig-prices` | daily 05:00 |

Verify:
```bash
systemctl status brick-scanner --no-pager
systemctl list-timers 'brick-scanner-*' --no-pager
journalctl -u brick-scanner -f          # live app logs
```

Run a daily job by hand to test it:
```bash
sudo systemctl start brick-scanner-catalog-refresh.service
sudo systemctl start brick-scanner-minifig-prices.service
```

## 8. Tailscale (private phone access + HTTPS)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then expose the app over **HTTPS** on the tailnet:
```bash
sudo tailscale serve --bg 5001
```

HTTPS matters: the **live auto-scan and PWA** features need a *secure context*.
Over plain `http://…:5001` they fall back to the manual "Take Photo" flow. With
`tailscale serve`, open `https://<vm-name>.<tailnet>.ts.net` on your phone for the
full live-scan experience.

## 9. Decommission the Mac instance

So the two instances don't both think they own the data / port 5001, stop the
macOS agents once the VM is verified:
```bash
# On the Mac:
launchctl unload ~/Library/LaunchAgents/com.brickscanner.app.plist
launchctl unload ~/Library/LaunchAgents/com.brickscanner.catalog-refresh.plist
launchctl unload ~/Library/LaunchAgents/com.brickscanner.minifig-prices.plist
```

---

## Notes & differences from macOS

- **Catalog refresh works here** (unlike Render): the VM has a persistent
  filesystem, so change tracking, the daily rebuild, and the scan-screen
  "Check for updates" footer all function exactly like they did on the Mac.
  `IS_RENDER` stays false.
- **Logs:** the app logs to the **journal** (`journalctl -u brick-scanner`)
  instead of `app.log`. The refresh scripts still write their own
  `catalog_refresh.log` / `minifig_prices.log` in the project dir.
- **Updating the app:** `git pull` then `sudo systemctl restart brick-scanner`.
  (Flask debug auto-reload also picks up changes live, as on macOS.)
- **Timezone:** timers fire on the VM's local time — set it with
  `sudo timedatectl set-timezone America/New_York` so 07:30/05:00 match your
  intent.
