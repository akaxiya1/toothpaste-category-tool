"""One-shot diagnostic dump: permissions, dependencies, parser sanity."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from ..db import DBManager, DEFAULT_DB_PATH
from ..parser import parse
from . import config_loader


def _check_import(name: str) -> dict:
    spec = importlib.util.find_spec(name)
    return {"name": name, "installed": spec is not None}


def collect() -> dict:
    db_path = Path(os.environ.get("EXPENSE_DB_PATH") or DEFAULT_DB_PATH)
    cfg = config_loader.load()

    samples = [
        "微信支付 -15.00元 商户：瑞幸咖啡",
        "您尾号1234的招行卡发生消费￥98.50，商户：京东",
        "支付宝自动续费 -25.00元 商户：iCloud",
    ]
    parse_results = []
    for s in samples:
        p = parse(s)
        parse_results.append({
            "input": s,
            "amount": p.amount, "direction": p.direction,
            "merchant": p.merchant, "account": p.account,
            "confidence": p.confidence, "reason": p.reason,
        })

    report = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
        },
        "paths": {
            "db_path": str(db_path),
            "db_exists": db_path.exists(),
            "db_writable": os.access(db_path.parent, os.W_OK) if db_path.parent.exists() else None,
            "cwd": os.getcwd(),
            "config_yaml": str(config_loader.DEFAULT_CFG_PATH),
            "config_loaded": bool(cfg),
        },
        "dependencies": [
            _check_import(n) for n in [
                "fastapi", "pydantic", "uvicorn", "pyperclip",
                "yaml", "pysqlcipher3", "httpx", "pytest",
            ]
        ],
        "features": cfg.get("features", {}),
        "parser_sanity": parse_results,
        "executables": {
            "python": shutil.which("python"),
            "uvicorn": shutil.which("uvicorn"),
        },
    }

    # Exercise DB to confirm it can open (and migrations apply)
    try:
        DBManager(db_path)
        report["paths"]["db_open"] = True
    except Exception as exc:
        report["paths"]["db_open"] = False
        report["paths"]["db_error"] = str(exc)

    return report


def main() -> None:
    print(json.dumps(collect(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
