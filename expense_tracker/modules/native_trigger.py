"""Native trigger bridge.

Problem: Python cannot directly subscribe to macOS Shortcuts automations,
Windows notification history, or Android's AccessibilityService. Each
platform requires a native helper that the user installs and grants OS
permissions to. We standardise on the **inbox dir**: every platform
helper writes a small JSON (or plain text) file there, and this
module polls the directory, parses candidate files through V1's
``parser`` / ``classifier`` pipeline, then deletes/archives them.

If the native bridge is disabled or its permissions fail, callers should
fall back to ``clipboard_monitor``. Both routes share the V1 dedup_hash
so double-capture is safe.

Permission cheatsheet:
- macOS: Shortcut "On Notification" -> Append File action. Grant the app
  Full Disk Access if the inbox lives outside ``~/Library/Application Support``.
- Windows: PowerShell scheduled task reading WinRT `UserNotificationListener`.
  First call prompts the user to allow "Notification access".
- Android: Tasker/Macrodroid -> AutoNotification -> Write File. Grant
  "Notification access" in Settings -> Apps -> Special Access.
- iOS: Shortcuts automation -> "Get Latest Screenshot / Text" -> Append
  to iCloud file synced to the inbox dir.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class TriggerEvent:
    text: str
    source: str                  # 'native:macos' / 'native:win' / 'native:android' / 'fallback:clipboard'
    path: Optional[Path] = None  # source file (for archival)


class InboxWatcher:
    """Poll ``inbox_dir`` for new files and enqueue ``TriggerEvent``s.

    Files with ``.json`` extension are expected to carry ``{"text": ...,
    "source": ...}``; any other extension is treated as a plain payload
    and defaults source to ``native``.
    """

    def __init__(
        self,
        inbox_dir: Path | str,
        sink: queue.Queue[TriggerEvent],
        poll_interval: float = 1.0,
        delete_after_process: bool = True,
        archive_dir: Optional[Path | str] = None,
    ):
        self.inbox_dir = Path(inbox_dir).expanduser()
        self.sink = sink
        self.poll_interval = max(0.1, poll_interval)
        self.delete_after_process = delete_after_process
        self.archive_dir = Path(archive_dir).expanduser() if archive_dir else None
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_dir:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_dir:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="native-inbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_once()
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[native] scan error: {exc}")
            time.sleep(self.poll_interval)

    def scan_once(self) -> int:
        """Expose a single scan for tests / manual runs. Returns processed count."""
        return self._scan_once()

    def _scan_once(self) -> int:
        if not self.inbox_dir.exists():
            return 0
        count = 0
        for path in sorted(self.inbox_dir.iterdir()):
            if not path.is_file():
                continue
            event = self._parse_file(path)
            if event is None:
                continue
            self.sink.put(event)
            count += 1
            self._retire(path)
        return count

    @staticmethod
    def _parse_file(path: Path) -> Optional[TriggerEvent]:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
            text = (payload.get("text") or "").strip()
            source = payload.get("source") or "native"
            if not text:
                return None
            return TriggerEvent(text=text, source=source, path=path)
        return TriggerEvent(text=raw, source="native", path=path)

    def _retire(self, path: Path) -> None:
        try:
            if self.archive_dir:
                target = self.archive_dir / f"{int(time.time()*1000)}-{path.name}"
                path.replace(target)
            elif self.delete_after_process:
                path.unlink(missing_ok=True)
        except OSError:
            pass


class TriggerRouter:
    """Fans events from the native inbox (primary) and clipboard
    (fallback) into a single callback. Deduplication is still handled by
    V1 ``dedup_hash`` at DB insert time, so we don't try to re-implement
    it here."""

    def __init__(self, on_event: Callable[[TriggerEvent], None]):
        self.on_event = on_event
        self.queue: queue.Queue[TriggerEvent] = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._consume, name="trigger-router", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.queue.put_nowait(TriggerEvent(text="", source="__stop__"))

    def emit(self, event: TriggerEvent) -> None:
        self.queue.put(event)

    def _consume(self) -> None:
        while not self._stop.is_set():
            event = self.queue.get()
            if event.source == "__stop__":
                return
            try:
                self.on_event(event)
            except Exception as exc:  # pragma: no cover
                print(f"[trigger] handler failed: {exc}")
