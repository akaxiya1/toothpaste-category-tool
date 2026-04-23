from expense_tracker.parser import parse, looks_like_transaction


def test_wechat_expense():
    p = parse("微信支付 -15.00元 商户：瑞幸咖啡")
    assert p.amount == 15.0
    assert p.merchant == "瑞幸咖啡"
    assert p.account == "微信"
    assert p.direction == "expense"
    assert p.confidence >= 0.7


def test_alipay_with_yuan_symbol():
    p = parse("支付宝 -￥35.50 商户：肯德基")
    assert p.amount == 35.5
    assert p.merchant == "肯德基"
    assert p.account == "支付宝"
    assert p.direction == "expense"


def test_refund_marked_correctly():
    p = parse("微信支付 +15.00元 退款：瑞幸咖啡")
    assert p.direction == "refund"
    assert p.amount == 15.0


def test_income_to_account():
    p = parse("微信收款到账 100.00元")
    assert p.direction == "income"
    assert p.amount == 100.0


def test_bank_card_message():
    p = parse("您尾号1234的招行卡发生消费￥98.50，商户：京东")
    assert p.amount == 98.5
    assert p.account == "银行卡"
    # merchant label captured
    assert p.merchant == "京东"


def test_extract_time():
    p = parse("微信支付 -8.50元 商户：星巴克 时间：2026-04-23 12:34")
    assert p.occurred_at and p.occurred_at.startswith("2026-04-23T12:34")


def test_no_amount_returns_invalid():
    p = parse("hello world")
    assert not p.is_valid
    assert p.reason == "no-amount"


def test_empty_input():
    p = parse("")
    assert not p.is_valid
    assert p.reason == "empty"


def test_looks_like_transaction_filter():
    assert looks_like_transaction("微信支付 -15.00元 商户：瑞幸")
    assert not looks_like_transaction("会议纪要：明天 10 点开会")
    assert not looks_like_transaction("")


def test_amount_picks_currency_marked():
    # Should prefer the currency-symbol amount over a stray number
    p = parse("订单号 1234567 支付宝 -¥9.90 商家：蜜雪冰城")
    assert p.amount == 9.9
