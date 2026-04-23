"""Optional background daemon that watches the OS clipboard.

Kept dependency-soft: imports of ``pyperclip`` happen lazily so the rest of
the package can be used (and tested) on systems without GUI bindings.

Usage::

    python -m expense_tracker.clipboard_monitor --endpoint http://127.0.0.1:8000/intake

When a transaction-shaped string lands on the clipboard, the daemon POSTs the
raw text to the local FastAPI app, which in turn parses + classifies it and
shows the confirmation popup.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

from .parser import looks_like_transaction


def _get_clipboard():
    try:
        import pyperclip  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on env
        raise SystemExit(
            "pyperclip is required for the clipboard daemon. Install with: pip install pyperclip"
        ) from exc
    return pyperclip


def _post(endpoint: str, text: str) -> Optional[dict]:
    payload = json.dumps({"text": text, "source": "clipboard"}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"[clipboard] POST failed: {exc}", file=sys.stderr)
        return None


def watch(endpoint: str, poll_interval: float = 0.8) -> None:
    pyperclip = _get_clipboard()
    last = ""
    print(f"[clipboard] watching... -> {endpoint}")
    while True:
        try:
            current = pyperclip.paste() or ""
        except Exception as exc:  # pragma: no cover
            print(f"[clipboard] read error: {exc}", file=sys.stderr)
            time.sleep(poll_interval * 3)
            continue
        if current and current != last and looks_like_transaction(current):
            last = current
            print(f"[clipboard] candidate ({len(current)} chars) -> POST")
            _post(endpoint, current)
        else:
            last = current or last
        time.sleep(poll_interval)


def main() -> None:
    p = argparse.ArgumentParser(description="Expense tracker clipboard daemon")
    p.add_argument("--endpoint", default="http://127.0.0.1:8000/intake")
    p.add_argument("--interval", type=float, default=0.8)
    args = p.parse_args()
    try:
        watch(args.endpoint, args.interval)
    except KeyboardInterrupt:
        print("\n[clipboard] bye")


if __name__ == "__main__":
    main()
