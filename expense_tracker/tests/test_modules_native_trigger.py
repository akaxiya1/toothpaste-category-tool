import json
import queue

from expense_tracker.modules.native_trigger import InboxWatcher, TriggerEvent, TriggerRouter


def test_inbox_reads_json_and_plain(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.json").write_text(json.dumps({"text": "微信支付 -15 元 商户：瑞幸"}), encoding="utf-8")
    (inbox / "b.txt").write_text("微信支付 -8 元 商户：星巴克", encoding="utf-8")

    q: queue.Queue[TriggerEvent] = queue.Queue()
    watcher = InboxWatcher(inbox, q, delete_after_process=True)
    processed = watcher.scan_once()
    assert processed == 2

    seen = [q.get_nowait() for _ in range(2)]
    texts = {e.text for e in seen}
    assert "微信支付 -15 元 商户：瑞幸" in texts
    assert "微信支付 -8 元 商户：星巴克" in texts
    # files were cleaned up
    assert list(inbox.iterdir()) == []


def test_inbox_archives_instead_of_deleting(tmp_path):
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    inbox.mkdir()
    (inbox / "a.txt").write_text("微信支付 -1.00 元 商户：X", encoding="utf-8")

    q: queue.Queue[TriggerEvent] = queue.Queue()
    watcher = InboxWatcher(inbox, q, delete_after_process=True, archive_dir=archive)
    watcher.scan_once()
    assert list(inbox.iterdir()) == []
    assert len(list(archive.iterdir())) == 1


def test_router_dispatches_events():
    import time

    seen = []

    def handler(ev: TriggerEvent) -> None:
        seen.append(ev.text)

    router = TriggerRouter(on_event=handler)
    router.start()
    router.emit(TriggerEvent(text="hello", source="native"))
    # Give the consumer a moment, then stop cleanly.
    for _ in range(20):
        if seen:
            break
        time.sleep(0.05)
    router.stop()
    assert "hello" in seen
