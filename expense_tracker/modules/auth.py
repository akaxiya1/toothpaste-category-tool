"""Lightweight static-token auth for write endpoints.

Intended for a single-user, home-network deployment where uvicorn binds
to ``0.0.0.0`` so the iPhone Shortcut can reach the Mac. Goals:

- Only write endpoints require the token (``/intake``, ``/transactions``
  etc.). Read-only endpoints stay open so the local UI doesn't need
  the token for every page load.
- Requests from the local loopback address bypass the check, so the
  Web UI served at ``http://127.0.0.1:8000`` keeps working without
  fiddling.
- Token is kept out of the config file by default: if
  ``features.auth.token`` is empty we generate a random one on first
  boot and write it to ``<data_dir>/.token`` (mode 0600). The user
  reads that file once and pastes it into the Shortcut; we never log
  it again.

The module is intentionally tiny: no JWT, no session, no TLS. That's
fine for Wi-Fi-local traffic between a phone you own and a Mac you
own. Anything beyond that should use a real reverse proxy.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, Request


def _token_file(db_path: Path) -> Path:
    return db_path.parent / ".token"


def resolve_token(cfg_block: dict, db_path: Path) -> Optional[str]:
    """Return the effective token (or ``None`` if auth is off).

    Precedence: explicit ``cfg_block.token`` > env var ``EXPENSE_INTAKE_TOKEN``
    > a persisted random token at ``.token``. When none of those exist
    and auth is enabled, a new token is generated, written to disk with
    0600 permissions, and returned.
    """
    if not cfg_block or not cfg_block.get("enabled"):
        return None

    configured = (cfg_block.get("token") or "").strip()
    if configured:
        return configured

    env_token = os.environ.get("EXPENSE_INTAKE_TOKEN", "").strip()
    if env_token:
        return env_token

    tf = _token_file(db_path)
    if tf.exists():
        try:
            stored = tf.read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        if stored:
            return stored

    # First run: mint and persist.
    tf.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    tf.write_text(token, encoding="utf-8")
    try:
        os.chmod(tf, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    # Print once so the user sees it in the console on first launch.
    print(f"[auth] generated new intake token at {tf} (mode 0600)")
    return token


def make_dependency(expected_token: Optional[str], allow_localhost: bool = True):
    """Return a FastAPI dependency that enforces the token header.

    - When ``expected_token`` is ``None``, the dependency is a no-op.
    - When ``allow_localhost`` is true, requests from 127.0.0.1 / ::1
      bypass the check (so the local UI keeps working).
    """

    async def _require(
        request: Request,
        x_intake_token: Optional[str] = Header(default=None, alias="X-Intake-Token"),
    ) -> None:
        if expected_token is None:
            return
        if allow_localhost and request.client is not None and request.client.host in {
            "127.0.0.1", "::1", "localhost",
        }:
            return
        if not x_intake_token or not secrets.compare_digest(x_intake_token, expected_token):
            raise HTTPException(status_code=401, detail="missing or invalid X-Intake-Token")

    return _require
