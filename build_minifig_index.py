#!/usr/bin/env python3
"""Build a minifig-variant index from a BrickLink catalog download.

BrickLink has no "variants of" API, so the app historically discovered variant
families (sw0574, sw0574a, sw0574b…) by *probing* the BrickLink API one candidate
id at a time — slow, incomplete, and LOCAL-ONLY (it needs BrickLink creds, so it
403s on Render). BrickLink does, however, publish its *entire* catalog as a flat
tab-delimited file. Download the Minifig item list once and every variant family
is just a group-by on the numeric base.

The resulting `minifig_variants.json` is tiny (~1–2 MB) and **committed** to the
repo, so — unlike the 195 MB `brick_parts.db` — it ships to Render and fixes
variant linking in production too. The live probe stays as a fallback for figs
BrickLink catalogued after the last index build.

How to get the source file (manual — it sits behind a BrickLink web login, not
the OAuth API, so it can't be scripted with the app's creds):

    1. Log in to BrickLink, open  https://www.bricklink.com/catalogDownload.asp
    2. Section "Download Items", Item Type = Minifig, click Download.
    3. Save the tab-delimited file next to this script (default name below).

Usage:
    python3 build_minifig_index.py                       # ./Minifigs.txt -> ./minifig_variants.json
    python3 build_minifig_index.py --src DIR/file.txt --out PATH

Refresh monthly-ish (BrickLink adds new figs); re-download and re-run, then commit
the updated JSON.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(HERE, "Minifigs.txt")
DEFAULT_OUT = os.path.join(HERE, "minifig_variants.json")

# Allow long fields just in case (names can be lengthy).
csv.field_size_limit(10 * 1024 * 1024)

# A BrickLink minifig number: lowercase letter prefix + digits + optional suffix
# (e.g. sw0574, sw0574a, col123). The numeric base is everything up to the suffix.
_ID_RE = re.compile(r"^[a-z]+\d+[a-z]*$")


def _open_text(path):
    """Open the download tolerant of encoding — BrickLink exports vary (BOM/latin-1)."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                f.read(4096)
            return open(path, encoding=enc, newline="")
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort: replace undecodable bytes rather than crash.
    return open(path, encoding="utf-8", errors="replace", newline="")


def _find_columns(header):
    """Locate the Number and Name columns by header name (order varies by export).
    Returns (number_idx, name_idx). Raises if the header isn't recognizable."""
    norm = [h.strip().lower() for h in header]
    num_idx = name_idx = None
    for i, h in enumerate(norm):
        if num_idx is None and h in ("number", "item number", "no", "no."):
            num_idx = i
        elif name_idx is None and h in ("name", "item name"):
            name_idx = i
    if num_idx is None or name_idx is None:
        raise ValueError(
            f"could not find Number/Name columns in header: {header!r}. "
            "Expected a tab-delimited BrickLink catalog 'Items' export."
        )
    return num_idx, name_idx


def build(src, out):
    if not os.path.exists(src):
        sys.exit(
            f"source file not found: {src}\n"
            "Download the Minifig item list from "
            "https://www.bricklink.com/catalogDownload.asp (see this script's docstring)."
        )

    minifigs = {}
    skipped = 0
    with _open_text(src) as f:
        reader = csv.reader(f, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            sys.exit("source file is empty")
        num_idx, name_idx = _find_columns(header)
        for row in reader:
            if len(row) <= max(num_idx, name_idx):
                skipped += 1
                continue
            mid = row[num_idx].strip()
            name = row[name_idx].strip()
            if not mid or not _ID_RE.match(mid.lower()):
                skipped += 1
                continue
            minifigs[mid] = name

    if not minifigs:
        sys.exit("parsed 0 minifig ids — wrong file? expected the Minifig 'Items' export")

    # Group by numeric base purely for the build log / sanity check.
    families = {}
    for mid in minifigs:
        base = re.match(r"^([a-z]+\d+)[a-z]*$", mid.lower()).group(1)
        families.setdefault(base, []).append(mid)
    multi = sum(1 for ids in families.values() if len(ids) > 1)

    payload = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "BrickLink catalog download (Minifig items)",
        "count": len(minifigs),
        "families": len(families),
        # Flat id -> name; the app groups by base at load time (it owns the
        # canonical _minifig_base_id logic, so we don't duplicate it here).
        "minifigs": minifigs,
    }
    with open(out, "w") as f:
        json.dump(payload, f, sort_keys=True, separators=(",", ":"))

    size_mb = os.path.getsize(out) / (1024 * 1024)
    print(
        f"wrote {out}\n"
        f"  {len(minifigs):,} minifigs in {len(families):,} families "
        f"({multi:,} with >1 variant), {skipped:,} rows skipped\n"
        f"  {size_mb:.2f} MB"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=DEFAULT_SRC,
                    help=f"BrickLink Minifig items download (default: {DEFAULT_SRC})")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"output JSON (default: {DEFAULT_OUT})")
    args = ap.parse_args()
    build(args.src, args.out)


if __name__ == "__main__":
    main()
