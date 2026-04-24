import os
import stat
from pathlib import Path

import pytest

from expense_tracker.modules import auth as auth_mod


def test_resolve_token_disabled(tmp_path):
    assert auth_mod.resolve_token({"enabled": False}, tmp_path / "data.db") is None
    assert auth_mod.resolve_token({}, tmp_path / "data.db") is None


def test_resolve_token_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSE_INTAKE_TOKEN", "ENV-TOKEN")
    cfg = {"enabled": True, "token": "EXPLICIT"}
    assert auth_mod.resolve_token(cfg, tmp_path / "data.db") == "EXPLICIT"


def test_resolve_token_env_when_no_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSE_INTAKE_TOKEN", "ENV-TOKEN")
    cfg = {"enabled": True, "token": ""}
    assert auth_mod.resolve_token(cfg, tmp_path / "data.db") == "ENV-TOKEN"


def test_resolve_token_generates_and_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPENSE_INTAKE_TOKEN", raising=False)
    db_path = tmp_path / "x" / "data.db"
    db_path.parent.mkdir(parents=True)
    cfg = {"enabled": True, "token": ""}
    t1 = auth_mod.resolve_token(cfg, db_path)
    assert t1 and len(t1) >= 16
    # Second call returns the SAME token from disk -- not a fresh one.
    t2 = auth_mod.resolve_token(cfg, db_path)
    assert t1 == t2
    # Permissions are 0600 (owner-only) on POSIX.
    if os.name == "posix":
        mode = stat.S_IMODE(Path(db_path.parent / ".token").stat().st_mode)
        assert mode == 0o600


def test_dependency_no_token_means_noop():
    dep = auth_mod.make_dependency(None)
    # Calling with no Request shouldn't raise; we just emulate by checking
    # the inner function returns None when expected_token is None.
    import asyncio

    class _Req:  # minimal stub
        client = None

    assert asyncio.run(dep(_Req(), None)) is None


def test_dependency_rejects_missing_header():
    dep = auth_mod.make_dependency("EXPECTED", allow_localhost=False)
    import asyncio
    from fastapi import HTTPException

    class _Req:
        client = type("c", (), {"host": "10.0.0.5"})()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(_Req(), None))
    assert exc.value.status_code == 401


def test_dependency_accepts_correct_header():
    dep = auth_mod.make_dependency("EXPECTED", allow_localhost=False)
    import asyncio

    class _Req:
        client = type("c", (), {"host": "10.0.0.5"})()

    assert asyncio.run(dep(_Req(), "EXPECTED")) is None


def test_dependency_localhost_bypass():
    dep = auth_mod.make_dependency("EXPECTED", allow_localhost=True)
    import asyncio

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()

    # No header but loopback -> allowed.
    assert asyncio.run(dep(_Req(), None)) is None
