import io
import json

from expense_tracker.modules.notifier import (
    NullNotifier,
    WebhookNotifier,
    WeChatServerChanNotifier,
    build,
)


class _FakeResp:
    def __init__(self, status=200, body=b'{"code":0,"message":"ok"}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body


def test_null_notifier_returns_ok():
    r = NullNotifier().send("t", "b")
    assert r.ok and r.status == 204


def test_wechat_success():
    captured = {}
    def fake_opener(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _FakeResp()
    n = WeChatServerChanNotifier(sendkey="SCTxxx", opener=fake_opener)
    r = n.send("今日账单 ¥142", "分类 Top 餐饮 ¥62")
    assert r.ok
    assert "SCTxxx" in captured["url"]
    assert b"title=" in captured["data"] and b"desp=" in captured["data"]


def test_wechat_server_error():
    def opener(req, timeout):
        return _FakeResp(status=200, body=b'{"code":400,"message":"bad key"}')
    n = WeChatServerChanNotifier(sendkey="SCTxxx", opener=opener)
    r = n.send("t", "b")
    assert not r.ok
    assert "bad key" in r.detail


def test_webhook_uses_template():
    captured = {}
    def opener(req, timeout):
        captured["data"] = req.data
        return _FakeResp(status=200)
    n = WebhookNotifier(url="http://example.com/hook",
                        json_template='{{"msg_type":"text","content":{{"text":"{title}\\n{body}"}}}}',
                        opener=opener)
    r = n.send("hi", "line")
    assert r.ok
    payload = json.loads(captured["data"])
    assert payload["msg_type"] == "text"
    assert "hi" in payload["content"]["text"]


def test_build_factory_null():
    assert isinstance(build({"notifier": "null"}), NullNotifier)
    assert isinstance(build({}), NullNotifier)


def test_build_factory_wechat_requires_key():
    import pytest
    with pytest.raises(ValueError):
        build({"notifier": "wechat", "wechat_sendkey": ""})
