#!/usr/bin/env bash
# Basic dialectic run: writer + reviewer + user approval.
#
# What this shows:
#   - The default dance: Claude writes, Codex reviews, reviewer either approves
#     or asks for revisions; the writer can accept or defend each item.
#   - --dry-run keeps the run in AWAITING_APPROVAL so you can inspect the diff
#     before deciding to apply it for real.
#
# Run from a real git repo. Expect ~$1-3 with the default max-effort models.

set -euo pipefail

dialectic run \
    --prompt "Add a small helper function in src/utils.py that takes a list of ints and returns their mean, with type hints and a one-line docstring. Add a test." \
    --max-revisions 1 \
    --dry-run \
    -v
