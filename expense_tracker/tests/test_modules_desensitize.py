from expense_tracker.modules.desensitize import clean, contains_pii


def test_masks_bank_card():
    assert clean("尾号 6225 8812 3456 7890 消费") != "尾号 6225 8812 3456 7890 消费"
    out = clean("卡号 6225881234567890 已扣款")
    assert "6225881234567890" not in out
    assert "6225" in out and "7890" in out


def test_masks_cn_mobile():
    out = clean("联系：13812345678 确认")
    assert "13812345678" not in out
    assert "138" in out and "5678" in out


def test_masks_order_number():
    out = clean("订单号：TX20260423ABCD1234 已支付")
    assert "TX20260423ABCD1234" not in out
    assert "订单号" in out and "****" in out


def test_masks_email():
    out = clean("bill-to alice@example.com")
    assert "alice@example.com" not in out
    assert "@example.com" in out


def test_empty_safe():
    assert clean("") == ""
    assert clean(None) == ""


def test_contains_pii_flag():
    assert contains_pii("联系 13812345678")
    assert not contains_pii("瑞幸咖啡 -15 元")
