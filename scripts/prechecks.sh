#!/bin/bash
# Pre-recording checks: suite green + V1 intact. Part 3 is a live run (manual).
set -e
cd "$(dirname "$0")/.."

echo "== 1. Suite =="
.venv/bin/python -m unittest discover -s tests

echo "== 2. V1 intact =="
# V1 is an annotated tag: rev-parse V1 alone yields the tag object, so
# dereference with ^{commit} to get the commit it points to.
V1_COMMIT=$(git rev-parse 'V1^{commit}')
MAIN_COMMIT=$(git rev-parse main)
test "$V1_COMMIT" = "$MAIN_COMMIT"
test "$V1_COMMIT" = "f993d3d1ff59033fd5fc02095fed430d618b15e8"
echo "V1 -> $V1_COMMIT == main"

echo "== 3. Fresh PO-001 check (manual) =="
echo "Run: .venv/bin/python src/app.py &"
echo "Then: curl -X POST http://127.0.0.1:8000/api/execute-procedure -H 'Content-Type: application/json' -d '{\"po_number\":\"PO-001\"}'"
echo "Expect: done in ~5-6 turns, sam/sam_verify audit blocks on every turn"
echo "Check: cat data/revision_history.json | grep -A5 '\"sam\"'"

echo "ALL PRECHECKS DEFINED — ready to record"
