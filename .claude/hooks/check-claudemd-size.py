#!/usr/bin/env python3
"""PostToolUse guardrail: warn when CLAUDE.md grows past a soft char limit.

The harness reloads CLAUDE.md into context every turn and warns natively at
40k chars. This fires earlier (32k) and only after edits *to CLAUDE.md*, so the
file is kept lean before it becomes a performance problem. Changelog-style notes
belong in CHANGELOG.md; machine-setup/deploy detail belongs in SETUP.md.
"""
import json
import os
import sys

SOFT_LIMIT = 32_000  # chars; native hard warning is at 40k

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

fp = (data.get("tool_input") or {}).get("file_path", "") or ""
if os.path.basename(fp) != "CLAUDE.md":
    sys.exit(0)

try:
    with open(fp, encoding="utf-8") as fh:
        n = len(fh.read())
except OSError:
    sys.exit(0)

if n > SOFT_LIMIT:
    print(
        f"⚠️ CLAUDE.md is now ~{n:,} chars (> {SOFT_LIMIT:,} soft limit; the harness "
        f"hard-warns at 40k). It is reloaded into context every turn, so keep it lean: "
        f"move changelog-style notes to CHANGELOG.md and machine-setup/deploy detail to "
        f"SETUP.md rather than growing this file.",
        file=sys.stderr,
    )
    sys.exit(2)  # exit 2 surfaces stderr back to Claude as feedback

sys.exit(0)
