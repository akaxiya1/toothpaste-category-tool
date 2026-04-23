"""Pluggable sync backend. The default build ships a no-op sync to
preserve the Local-First guarantee. Wire up a real backend by
subclassing ``SyncBackend`` and registering it in ``config.yaml``.

The intent is that the SQLite file and ``rules/`` directory can be
periodically pushed to a WebDAV/Nextcloud server, but only when the
user explicitly opts in -- the module never phones home on its own.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SyncReport:
    pushed: int = 0
    pulled: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class SyncBackend(abc.ABC):
    @abc.abstractmethod
    def push(self, paths: list[Path]) -> SyncReport: ...

    @abc.abstractmethod
    def pull(self, target_dir: Path) -> SyncReport: ...


class NullSync(SyncBackend):
    """The default. Does nothing -- kept so callers can always rely on
    a valid object instead of branching on ``None``."""

    def push(self, paths: list[Path]) -> SyncReport:
        return SyncReport(pushed=0)

    def pull(self, target_dir: Path) -> SyncReport:
        return SyncReport(pulled=0)


def get_backend(name: Optional[str]) -> SyncBackend:
    """Factory. Only ``null`` is wired up in this build; extend here
    when you add a real WebDAV client (e.g. ``webdavclient3``)."""
    if not name or name.lower() in {"null", "off", "disabled"}:
        return NullSync()
    raise NotImplementedError(
        f"sync backend '{name}' not wired up; install the corresponding plugin"
    )
