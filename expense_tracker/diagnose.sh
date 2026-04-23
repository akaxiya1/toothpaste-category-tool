#!/usr/bin/env bash
# Quick health check: Python + expense_tracker deps + parser sanity.
# Usage: bash expense_tracker/diagnose.sh   (or chmod +x then ./diagnose.sh)
set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

if ! command -v python >/dev/null 2>&1; then
  echo "python: MISSING (install Python 3.10+)"; exit 1
fi

echo "== Python =="
python --version
echo

echo "== Platform =="
uname -a || true
echo

echo "== Permissions (inbox dir) =="
inbox="${EXPENSE_INBOX_DIR:-$HOME/.expense_tracker/inbox}"
mkdir -p "$inbox"
if [ -w "$inbox" ]; then
  echo "inbox OK: $inbox"
else
  echo "inbox NOT WRITABLE: $inbox (fix with: chmod u+w \"$inbox\")"
fi
echo

echo "== expense_tracker diagnostic =="
python -m expense_tracker.modules.diagnose
