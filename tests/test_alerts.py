"""阶段4 测试：告警判定 + 防轰炸 + 每日报告（mock 通知发送）。

不真实发送通知，用 monkey-patch 把 notify.send 替换成记录调用的假函数。
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "test123")

from app import alerts, crypto, db, notify


# 记录 notify.send 的调用，避免真实推送
_sent: list[tuple[str, str]] = []


def _fake_send(title, content):
    _sent.append((title, content))
    return {"serverchan": {"ok": True}}


def _setup():
    """每个测试前：清库 + 注入 fake notify。"""
    db.init_db()
    _sent.clear()
    alerts.notify.send = _fake_send  # type: ignore[attr-defined]
    notify.send = _fake_send  # type: ignore[attr-defined]


def _cleanup_accounts():
    """清掉所有账户和通知日志。"""
    for a in db.list_accounts():
        db.delete_account(a["id"])
    with db.get_conn() as conn:
        conn.execute("DELETE FROM notify_logs")


def test_balance_alert_triggered():
    """余额 ≤ 阈值 → 触发告警。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None}
    msg = alerts.check_and_alert(acc, result)
    assert msg is not None
    assert "余额仅 5.00" in msg
    assert len(_sent) == 1
    db.delete_account(aid)
    print(f"[PASS] 余额告警触发（5元 ≤ 阈值10）")


def test_balance_alert_not_triggered():
    """余额 > 阈值 → 不告警。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 100.0, "currency": "CNY", "raw_error": None}
    msg = alerts.check_and_alert(acc, result)
    assert msg is None
    assert len(_sent) == 0
    db.delete_account(aid)
    print("[PASS] 余额充足不告警")


def test_window_alert_triggered():
    """窗口已用% ≥ 阈值 → 告警。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("glm", "GLM", enc)
    acc = db.get_account(aid)
    result = {
        "type": "window", "raw_error": None,
        "tiers": [{"type": "five_hour", "used_percent": 95.0, "remaining_percent": 5.0}],
    }
    msg = alerts.check_and_alert(acc, result)
    assert msg is not None
    assert "95.0%" in msg
    db.delete_account(aid)
    print("[PASS] 窗口高占用告警触发")


def test_window_alert_not_triggered():
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("glm", "GLM", enc)
    acc = db.get_account(aid)
    result = {
        "type": "window", "raw_error": None,
        "tiers": [{"type": "five_hour", "used_percent": 50.0, "remaining_percent": 50.0}],
    }
    msg = alerts.check_and_alert(acc, result)
    assert msg is None
    db.delete_account(aid)
    print("[PASS] 窗口低占用不告警")


def test_error_result_no_alert():
    """查询失败（raw_error）不告警。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 0, "raw_error": "401"}
    msg = alerts.check_and_alert(acc, result)
    assert msg is None
    db.delete_account(aid)
    print("[PASS] 查询失败不告警（避免 key 失效狂发）")


def test_cooldown_prevents_spam():
    """防轰炸：6 小时内同账户同告警只发一次。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None}
    # 第一次：应发送
    msg1 = alerts.check_and_alert(acc, result)
    assert msg1 is not None
    assert len(_sent) == 1
    # 第二次（冷却中）：不应发送
    msg2 = alerts.check_and_alert(acc, result)
    assert msg2 is None
    assert len(_sent) == 1  # 没多发
    db.delete_account(aid)
    print("[PASS] 防轰炸：冷却期内重复告警被抑制")


def test_custom_threshold_per_account():
    """每账户自定义阈值：account.config_json 里的阈值覆盖默认。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    # 该账户自定义：余额阈值 50 元
    aid = db.create_account("deepseek", "DS", enc, {"alert_balance_threshold": 50})
    acc = db.get_account(aid)
    # 余额 30 元 > 默认阈值(10) 但 < 自定义阈值(50) → 应触发
    result = {"type": "balance", "balance": 30.0, "currency": "CNY", "raw_error": None}
    msg = alerts.check_and_alert(acc, result)
    assert msg is not None
    assert "30.00" in msg
    db.delete_account(aid)
    print("[PASS] 每账户自定义阈值生效")


async def test_daily_report():
    """每日报告：汇总各账户状态。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid1 = db.create_account("deepseek", "我的DS", enc)
    aid2 = db.create_account("glm", "我的GLM", enc)
    db.add_snapshot(aid1, {"type": "balance", "balance": 47.61, "currency": "CNY", "raw_error": None})
    db.add_snapshot(aid2, {"type": "window", "raw_error": None,
                           "tiers": [{"type": "five_hour", "used_percent": 1.0, "remaining_percent": 99.0}]})
    await alerts.daily_report()
    assert len(_sent) == 1
    title, content = _sent[0]
    assert "日报" in title
    assert "我的DS" in content
    assert "47.61" in content
    assert "我的GLM" in content
    assert "合计余额" in content
    db.delete_account(aid1)
    db.delete_account(aid2)
    print("[PASS] 每日报告生成正确（含余额合计）")


def test_notify_config_api_keys():
    """验证通知配置 key 白名单完整。"""
    from app.main import _NOTIFY_KEYS
    assert "notify_serverchan_key" in _NOTIFY_KEYS
    assert "notify_smtp_password" in _NOTIFY_KEYS
    assert "alert_balance_threshold" in _NOTIFY_KEYS
    print("[PASS] 通知配置 key 白名单完整")


if __name__ == "__main__":
    test_balance_alert_triggered()
    test_balance_alert_not_triggered()
    test_window_alert_triggered()
    test_window_alert_not_triggered()
    test_error_result_no_alert()
    test_cooldown_prevents_spam()
    test_custom_threshold_per_account()
    test_notify_config_api_keys()
    asyncio.run(test_daily_report())
    _cleanup_accounts()
    print("\n=== 阶段4 全部通过 ===")
