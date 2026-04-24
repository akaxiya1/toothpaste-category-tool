from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("TOOTHPASTE_TOOL_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = Path(os.getenv("TOOTHPASTE_TOOL_DB_PATH", DATA_DIR / "toothpaste_tool.sqlite3")).resolve()
IMPORT_DIR = DATA_DIR / "imports"
BACKUP_DIR = DATA_DIR / "backups"
TEMP_DIR = DATA_DIR / "temp"
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"
HOST = os.getenv("TOOTHPASTE_TOOL_HOST", "127.0.0.1")
PORT = int(os.getenv("TOOTHPASTE_TOOL_PORT", "8765"))


def ensure_directories() -> None:
    for path in [DATA_DIR, IMPORT_DIR, BACKUP_DIR, TEMP_DIR, STATIC_DIR, SAMPLES_DIR]:
        path.mkdir(parents=True, exist_ok=True)
