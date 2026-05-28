#!/usr/bin/env python3
"""Daily refresh of the offline catalog.  LOCAL-ONLY (does not run on Render).

Render rebuilds the catalog from scratch on each deploy onto an ephemeral
filesystem, so there's no persisted prior DB to diff and no non-deploy rebuild
trigger — the refresh button and change tracking are gated off there (see
IS_RENDER in app.py). This tool and the launchd job are for the local dev box.


Checks Rebrickable's CSV dump for changes via cheap HEAD requests (ETag /
Last-Modified — no big downloads). If anything changed, re-downloads the whole
dump (for a consistent snapshot), rebuilds brick_parts.db into a temp file, and
atomically swaps it in. The dev server opens a fresh SQLite connection per
request, so it picks up the new DB on the next request — no restart needed.

Run manually:   python3 refresh_catalog.py [--force]
Run daily:      via launchd (see com.brickscanner.catalog-refresh.plist)

Exit codes: 0 = up to date or updated OK; 1 = error.
"""
import datetime
import json
import os
import sqlite3
import sys

import requests

import build_brick_db
import download_csvs

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, ".catalog_manifest.json")
CHANGES = os.path.join(HERE, ".catalog_changes.json")
LOG = os.path.join(HERE, "catalog_refresh.log")
DB = build_brick_db.DEFAULT_DB
SRC = download_csvs.DEFAULT_DEST

# Cap how many added/removed items we store per category (keeps the JSON small;
# a daily Rebrickable update rarely changes more than a few hundred).
CHANGES_CAP = 500


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def remote_manifest():
    """HEAD each table and capture its change signals (etag / last-modified / size)."""
    m = {}
    for table in download_csvs.TABLES:
        url = f"{download_csvs.CDN}/{table}.csv.gz"
        r = requests.head(url, timeout=30)
        r.raise_for_status()
        m[table] = {
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
            "size": r.headers.get("Content-Length"),
        }
    return m


def load_local_manifest():
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _catalog_index(db_path):
    """Return {parts:{num:name}, minifigs:{num:name}, sets:{num:name}} for diffing."""
    conn = sqlite3.connect(db_path)
    try:
        return {
            "parts": dict(conn.execute("SELECT part_num, name FROM parts")),
            "minifigs": dict(conn.execute("SELECT fig_num, name FROM minifigs")),
            "sets": dict(conn.execute("SELECT set_num, name FROM sets")),
        }
    finally:
        conn.close()


def _diff_catalog(old_db, new_db):
    """Compare two catalog DBs and return what was added/removed per category.

    Returns a dict with a per-category summary (counts) plus capped item lists,
    or None on failure / when there are no differences.
    """
    old = _catalog_index(old_db)
    new = _catalog_index(new_db)
    result = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "summary": {},
    }
    total = 0
    for kind in ("sets", "minifigs", "parts"):
        o, n = old[kind], new[kind]
        added = sorted((k for k in n if k not in o))
        removed = sorted((k for k in o if k not in n))
        total += len(added) + len(removed)
        result["summary"][kind] = {"added": len(added), "removed": len(removed)}
        result[kind] = {
            "added": [{"num": k, "name": n[k]} for k in added[:CHANGES_CAP]],
            "removed": [{"num": k, "name": o[k]} for k in removed[:CHANGES_CAP]],
            "truncated": len(added) > CHANGES_CAP or len(removed) > CHANGES_CAP,
        }
    return result if total else None


def run(force=False):
    """Check for changes and rebuild the catalog if needed.

    Returns a dict: {ok, changed, message, updated:[tables]}. Safe to call from
    the Flask app (e.g. a manual "refresh now" button) or the CLI.
    """
    log("=== catalog refresh start ===")

    try:
        remote = remote_manifest()
    except Exception as e:
        log(f"ERROR: could not fetch remote headers: {e}")
        return {"ok": False, "changed": False,
                "message": f"Couldn't reach Rebrickable: {e}", "updated": []}

    local = load_local_manifest()
    changed = [t for t in download_csvs.TABLES if remote.get(t) != local.get(t)]
    db_missing = not os.path.exists(DB)

    if not force and not changed and not db_missing:
        log("no changes — catalog already up to date")
        return {"ok": True, "changed": False,
                "message": "Already up to date", "updated": []}

    if db_missing:
        log("brick_parts.db missing — building fresh")
    elif force:
        log("--force: rebuilding regardless of changes")
    else:
        log(f"changes detected in: {', '.join(changed)}")

    try:
        # Re-download the full dump so every table is from the same snapshot.
        download_csvs.main(SRC, force=True)
        # Build into a unique temp file, then swap atomically (zero-downtime for
        # readers; pid suffix avoids clashing with a concurrent launchd run).
        tmp = f"{DB}.new.{os.getpid()}"
        build_brick_db.build(SRC, tmp)

        # Diff old vs new (both exist now) to record what was added/removed.
        changes = None
        if not db_missing and os.path.exists(DB):
            try:
                changes = _diff_catalog(DB, tmp)
            except Exception as e:
                log(f"WARN: could not compute catalog diff: {e}")

        os.replace(tmp, DB)
        log(f"swapped in new brick_parts.db ({os.path.getsize(DB)/1_000_000:.1f} MB)")

        if changes:
            s = changes["summary"]
            log("changes: " + ", ".join(
                f"{k} +{s[k]['added']}/-{s[k]['removed']}" for k in ("sets", "minifigs", "parts")))
            with open(CHANGES, "w") as f:
                json.dump(changes, f)
    except SystemExit as e:           # download_csvs.main calls sys.exit on failure
        log(f"ERROR: download/build failed (exit {e.code})")
        return {"ok": False, "changed": False,
                "message": "Download/build failed", "updated": []}
    except Exception as e:
        log(f"ERROR: download/build failed: {e}")
        return {"ok": False, "changed": False,
                "message": f"Download/build failed: {e}", "updated": []}

    with open(MANIFEST, "w") as f:
        json.dump(remote, f, indent=2)
    log("=== refresh complete ===")
    msg = "Catalog updated" if changed or db_missing else "Rebuilt (forced)"
    return {"ok": True, "changed": True, "message": msg, "updated": changed,
            "changes": changes.get("summary") if changes else None}


def main():
    result = run(force="--force" in sys.argv)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
