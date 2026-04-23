"""Pluggable notifier backends.

Goals:
- Ship a working WeChat path out of the box (Server Chan is the de-facto
  bridge for personal WeChat push: https://sct.ftqq.com/).
- Never require the network at import time; tests can inject a fake
  ``urlopen`` function.
- Let the user pick the channel via ``config.yaml`` and mix with the
  rest of V2 flags.

WeChat (Server Chan) setup:
1. Log in at https://sct.ftqq.com with GitHub.
2. Copy your ``SENDKEY`` (starts with ``SCT``).
3. Put it in ``config.yaml``:

     features:
       daily_digest:
         enabled: true
         time: "22:00"
         notifier: wechat
         wechat_sendkey: SCTxxxxxxxxxxxxxxxx

4. First message will prompt on your phone to confirm the binding.
"""

from __future__ import annotations

import abc
import json
import platform
import shlex
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

UrlOpener = Callable[[urllib.request.Request, float], "object"]


@dataclass
class NotificationResult:
    ok: bool
    status: int = 0
    detail: str = ""


class Notifier(abc.ABC):
    @abc.abstractmethod
    def send(self, title: str, body: str) -> NotificationResult: ...


class NullNotifier(Notifier):
    def send(self, title: str, body: str) -> NotificationResult:
        return NotificationResult(ok=True, status=204, detail="null-notifier")


class WeChatServerChanNotifier(Notifier):
    """Push to personal WeChat via Server Chan (sct.ftqq.com)."""

    endpoint_tpl = "https://sctapi.ftqq.com/{key}.send"

    def __init__(self, sendkey: str, timeout: float = 8.0,
                 opener: Optional[UrlOpener] = None):
        if not sendkey:
            raise ValueError("wechat_sendkey is required")
        self.sendkey = sendkey
        self.timeout = timeout
        self._opener = opener or (lambda req, to: urllib.request.urlopen(req, timeout=to))

    def send(self, title: str, body: str) -> NotificationResult:
        url = self.endpoint_tpl.format(key=self.sendkey)
        payload = urllib.parse.urlencode({
            "title": title[:32],          # Server Chan limits title length
            "desp": body,                 # supports Markdown
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            resp = self._opener(req, self.timeout)
            raw = resp.read().decode("utf-8", errors="replace") if hasattr(resp, "read") else ""
            status = getattr(resp, "status", 200)
            try:
                data = json.loads(raw)
                ok = data.get("code", 0) == 0
                detail = data.get("message") or raw[:200]
            except json.JSONDecodeError:
                ok = 200 <= status < 300
                detail = raw[:200]
            return NotificationResult(ok=ok, status=status, detail=detail)
        except Exception as exc:
            return NotificationResult(ok=False, status=0, detail=str(exc))


class WebhookNotifier(Notifier):
    """Generic JSON webhook (Feishu/DingTalk/WeCom/Slack).

    ``json_template`` receives ``{"title": ..., "body": ...}`` via
    ``str.format_map`` so users can shape the payload."""

    def __init__(self, url: str, json_template: Optional[str] = None,
                 timeout: float = 8.0, opener: Optional[UrlOpener] = None):
        if not url:
            raise ValueError("webhook url is required")
        self.url = url
        self.json_template = json_template or '{{"title": "{title}", "body": "{body}"}}'
        self.timeout = timeout
        self._opener = opener or (lambda req, to: urllib.request.urlopen(req, timeout=to))

    def send(self, title: str, body: str) -> NotificationResult:
        payload_str = self.json_template.format_map({
            "title": title.replace("\"", "'"),
            "body": body.replace("\"", "'").replace("\n", "\\n"),
        })
        try:
            json.loads(payload_str)          # reject malformed templates early
        except json.JSONDecodeError as exc:
            return NotificationResult(ok=False, detail=f"bad template: {exc}")
        req = urllib.request.Request(
            self.url, data=payload_str.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = self._opener(req, self.timeout)
            status = getattr(resp, "status", 200)
            return NotificationResult(ok=200 <= status < 300, status=status)
        except Exception as exc:
            return NotificationResult(ok=False, detail=str(exc))


class DesktopNotifier(Notifier):
    """Best-effort OS notification: osascript / notify-send / BurntToast."""

    def send(self, title: str, body: str) -> NotificationResult:
        system = platform.system()
        try:
            if system == "Darwin":
                script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
                subprocess.run(["osascript", "-e", script], check=False, timeout=5)
            elif system == "Linux":
                subprocess.run(["notify-send", title, body], check=False, timeout=5)
            elif system == "Windows":
                ps = (
                    f"New-BurntToastNotification -Text "
                    f"'{title.replace(chr(39), chr(39)*2)}', "
                    f"'{body.replace(chr(39), chr(39)*2)}'"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               check=False, timeout=5)
            else:
                return NotificationResult(ok=False, detail=f"unsupported os: {system}")
            return NotificationResult(ok=True, status=200)
        except FileNotFoundError as exc:
            return NotificationResult(ok=False, detail=f"binary missing: {exc}")
        except Exception as exc:
            return NotificationResult(ok=False, detail=str(exc))


def build(cfg_block: dict) -> Notifier:
    """Factory driven by ``config.yaml`` ``daily_digest`` sub-tree."""
    kind = (cfg_block or {}).get("notifier", "null")
    if kind in (None, "", "null", "off"):
        return NullNotifier()
    if kind == "wechat":
        return WeChatServerChanNotifier(cfg_block.get("wechat_sendkey", ""))
    if kind == "webhook":
        return WebhookNotifier(
            url=cfg_block.get("webhook_url", ""),
            json_template=cfg_block.get("webhook_template"),
        )
    if kind == "desktop":
        return DesktopNotifier()
    raise ValueError(f"unknown notifier kind: {kind!r}")
