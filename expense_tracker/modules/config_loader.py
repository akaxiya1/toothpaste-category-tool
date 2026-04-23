"""Configuration loader with mtime-based hot reload.

Dependencies are soft: tries PyYAML, falls back to a JSON file with the
same base name (``config.json``). If nothing loads we return an empty
dict and every feature stays off -- that's the point.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_CFG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _try_yaml(path: Path) -> Optional[dict]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _try_json(path: Path) -> Optional[dict]:
    json_path = path.with_suffix(".json")
    if not json_path.exists():
        return None
    with json_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CFG_PATH
    if p.exists():
        data = _try_yaml(p)
        if data is not None:
            return data
    js = _try_json(p)
    return js if js is not None else {}


def feature(cfg: dict, name: str, default: Any = False) -> Any:
    """Read a nested ``features.<name>`` entry. Supports dotted paths and
    booleans-or-dicts (e.g. ``time_decay: false`` or ``time_decay: {enabled: true, ...}``)."""
    node: Any = cfg.get("features", {})
    for part in name.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    if isinstance(node, dict) and "enabled" in node:
        return node if node.get("enabled") else False
    return node


class ConfigWatcher:
    """Poll a config file's mtime and fire a callback on change."""

    def __init__(self, path: Path | str, callback: Callable[[dict], None], interval: float = 2.0):
        self.path = Path(path)
        self.callback = callback
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_mtime = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            if mtime and mtime != self._last_mtime:
                self._last_mtime = mtime
                try:
                    self.callback(load(self.path))
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"[config] reload failed: {exc}")
            time.sleep(self.interval)


def check_once(path: Path | str | None = None) -> dict[str, Any]:
    """One-shot reload without starting a watcher thread."""
    return load(path)


def expand_path(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve()
