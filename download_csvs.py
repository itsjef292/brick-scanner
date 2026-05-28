#!/usr/bin/env python3
"""Download the Rebrickable CSV bulk dump used to build the offline catalog.

Fetches the gzipped CSVs from Rebrickable's public download CDN and decompresses
them into the source folder that build_brick_db.py reads (default "Brick Parts/").

Used on deploy (e.g. Render) so the offline catalog can be rebuilt from scratch
without committing the ~330MB of data to git:

    python3 download_csvs.py && python3 build_brick_db.py

Only the tables build_brick_db.py actually imports are downloaded.

Usage:
    python3 download_csvs.py                  # → ./Brick Parts/
    python3 download_csvs.py --dest DIR --force
"""
import argparse
import gzip
import os
import shutil
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEST = os.path.join(HERE, "Brick Parts")
CDN = "https://cdn.rebrickable.com/media/downloads"

# Only the tables build_brick_db.py imports (skips elements / part_relationships /
# inventory_sets, which the catalog doesn't use).
TABLES = [
    "colors",
    "part_categories",
    "parts",
    "themes",
    "sets",
    "minifigs",
    "inventories",
    "inventory_parts",      # the big one (~128MB decompressed)
    "inventory_minifigs",
]


def download_one(table, dest_dir, force):
    out_csv = os.path.join(dest_dir, f"{table}.csv")
    if os.path.exists(out_csv) and not force:
        print(f"  ✓ {table}.csv already present (skip; use --force to refresh)")
        return os.path.getsize(out_csv)

    url = f"{CDN}/{table}.csv.gz"
    ts = time.time()
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()

    # Stream the gzip response straight through a decompressor to the .csv file.
    tmp = out_csv + ".tmp"
    raw = resp.raw
    raw.decode_content = True  # let requests handle any transfer-encoding
    with gzip.GzipFile(fileobj=raw) as gz, open(tmp, "wb") as f:
        shutil.copyfileobj(gz, f, length=1024 * 1024)
    os.replace(tmp, out_csv)

    size = os.path.getsize(out_csv)
    print(f"  ↓ {table}.csv  {size/1_000_000:.1f} MB  ({time.time()-ts:.1f}s)")
    return size


def main(dest_dir, force):
    os.makedirs(dest_dir, exist_ok=True)
    print(f"Downloading Rebrickable catalog CSVs → {dest_dir}")
    t0 = time.time()
    total = 0
    for table in TABLES:
        try:
            total += download_one(table, dest_dir, force)
        except Exception as e:
            print(f"  ! FAILED {table}: {e}")
            sys.exit(1)
    print(f"Done in {time.time()-t0:.1f}s — {total/1_000_000:.1f} MB across {len(TABLES)} files")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=DEFAULT_DEST, help="output folder for CSVs")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args()
    main(args.dest, args.force)
