"""阶段4 测试：告警判定（edge trigger）+ 防轰炸 + 每日报告（mock 通知发送）。

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


# ============ edge trigger（状态变化触发）============

def test_balance_alert_first_trigger():
    """余额 ≤ 阈值，首次（无历史状态）→ 触发。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None}
    msg = alerts.check_and_alert(acc, result)
    assert msg is not None
    assert "5.00" in msg
    assert len(_sent) == 1
    # 状态已记录为 triggered
    assert db.get_last_alert_state(aid) is True
    db.delete_account(aid)
    print("[PASS] 余额首次超阈值触发告警")


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
    assert db.get_last_alert_state(aid) is False
    db.delete_account(aid)
    print("[PASS] 余额充足不告警")


def test_edge_trigger_persistent_state_no_resend():
    """edge trigger：持续超阈值（第二次刷新）→ 不重发（省额度）。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None}
    # 第一次：首次突破 → 发
    msg1 = alerts.check_and_alert(acc, result)
    assert msg1 is not None
    assert len(_sent) == 1
    # 第二次（仍超阈值）：edge trigger 抑制，不重发
    msg2 = alerts.check_and_alert(acc, result)
    assert msg2 is None
    assert len(_sent) == 1  # 没多发
    db.delete_account(aid)
    print("[PASS] edge trigger：持续状态不重发（核心省额度逻辑）")


def test_edge_trigger_recovery_then_retrigger():
    """edge trigger：回落→回升再次突破 → 重新触发。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    # 第一次：超阈值 → 发
    alerts.check_and_alert(acc, {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None})
    assert len(_sent) == 1
    # 第二次：回落到正常 → 不发，状态更新为未触发
    alerts.check_and_alert(acc, {"type": "balance", "balance": 50.0, "currency": "CNY", "raw_error": None})
    assert len(_sent) == 1
    assert db.get_last_alert_state(aid) is False
    # 第三次：再次超阈值 → 新跳变，重新发
    alerts.check_and_alert(acc, {"type": "balance", "balance": 3.0, "currency": "CNY", "raw_error": None})
    assert len(_sent) == 2
    db.delete_account(aid)
    print("[PASS] edge trigger：回落后再突破重新触发")


def test_send_failure_does_not_set_state():
    """发送失败时状态不变，下次刷新仍重试（避免永久吞告警）。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    result = {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None}

    # mock 所有渠道失败
    def failing_send(title, content):
        return {"serverchan": {"ok": False, "error": "网络错误"}}
    alerts.notify.send = failing_send  # type: ignore[attr-defined]

    # 第一次：超阈值但发送失败 → 状态不应被标记为 triggered
    msg = alerts.check_and_alert(acc, result)
    assert msg is None  # 发送失败返回 None
    assert db.get_last_alert_state(aid) is not True  # 未标记为已触发

    # 恢复正常发送，第二次：仍超阈值，应重新尝试发送
    alerts.notify.send = _fake_send  # type: ignore[attr-defined]
    msg2 = alerts.check_and_alert(acc, result)
    assert msg2 is not None  # 重试成功
    assert len(_sent) == 1
    assert db.get_last_alert_state(aid) is True  # 成功后才标记
    db.delete_account(aid)
    print("[PASS] 发送失败不标记状态，下次重试成功才标记")


def test_window_alert_triggered():
    """窗口已用% ≥ 阈值，首次 → 告警（含进度条）。"""
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
    assert "▰" in msg  # 进度条
    db.delete_account(aid)
    print("[PASS] 窗口高占用告警触发（含进度条）")


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


def test_error_result_no_alert_and_reset_state():
    """查询失败不告警，且重置状态（下次恢复需重新跳变）。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc)
    acc = db.get_account(aid)
    # 先触发一次告警
    alerts.check_and_alert(acc, {"type": "balance", "balance": 5.0, "currency": "CNY", "raw_error": None})
    assert len(_sent) == 1
    # 查询失败：不告警，状态重置
    msg = alerts.check_and_alert(acc, {"type": "balance", "balance": 0, "raw_error": "401"})
    assert msg is None
    assert db.get_last_alert_state(aid) is False  # 重置
    db.delete_account(aid)
    print("[PASS] 查询失败不告警且重置状态")


def test_custom_threshold_per_account():
    """每账户自定义阈值：account.config_json 里的阈值覆盖默认。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "DS", enc, {"alert_balance_threshold": 50})
    acc = db.get_account(aid)
    # 余额 30 > 默认阈值(10) 但 < 自定义阈值(50) → 触发
    result = {"type": "balance", "balance": 30.0, "currency": "CNY", "raw_error": None}
    msg = alerts.check_and_alert(acc, result)
    assert msg is not None
    assert "30.00" in msg
    db.delete_account(aid)
    print("[PASS] 每账户自定义阈值生效")


# ============ 进度条 ============

def test_progress_bar():
    assert alerts._progress_bar(0) == "▱▱▱▱▱▱▱▱▱▱"
    assert alerts._progress_bar(50) == "▰▰▰▰▰▱▱▱▱▱"
    assert alerts._progress_bar(92) == "▰▰▰▰▰▰▰▰▰▱"
    assert alerts._progress_bar(100) == "▰▰▰▰▰▰▰▰▰▰"
    # 边界：超出 100 截断
    assert alerts._progress_bar(150) == "▰▰▰▰▰▰▰▰▰▰"
    assert alerts._progress_bar(-5) == "▱▱▱▱▱▱▱▱▱▱"
    print("[PASS] 进度条各档正确")


# ============ 每日报告 ============

async def _daily_report_async():
    """每日报告：汇总各账户状态 + 异常标记 + 告警中区块。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid1 = db.create_account("deepseek", "我的DS", enc)
    aid2 = db.create_account("glm", "我的GLM", enc)
    aid3 = db.create_account("openai_proxy", "三方中转", enc)
    db.add_snapshot(aid1, {"type": "balance", "balance": 47.61, "currency": "CNY", "raw_error": None})
    db.add_snapshot(aid2, {"type": "window", "raw_error": None,
                           "tiers": [{"type": "five_hour", "used_percent": 1.0, "remaining_percent": 99.0}]})
    db.add_snapshot(aid3, {"type": "balance", "raw_error": "API Key 无效"})
    await alerts.daily_report()
    assert len(_sent) == 1
    title, content = _sent[0]
    assert "日报" in title
    assert "我的DS" in content
    assert "47.61" in content
    assert "我的GLM" in content
    assert "合计余额" in content
    assert "🔴" in content  # 异常账户标记
    assert "API Key 无效" in content
    db.delete_account(aid1)
    db.delete_account(aid2)
    db.delete_account(aid3)
    print("[PASS] 每日报告含异常标记 🔴")


def test_daily_report():
    asyncio.run(_daily_report_async())


async def _daily_report_with_alerting_async():
    """日报的「告警中」区块：列出当前超阈值的账户。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid1 = db.create_account("glm", "我的GLM", enc)
    # GLM 周窗口 91% ≥ 90 阈值
    db.add_snapshot(aid1, {"type": "window", "raw_error": None,
                           "tiers": [{"type": "five_hour", "used_percent": 10, "remaining_percent": 90},
                                     {"type": "weekly", "used_percent": 91, "remaining_percent": 9}]})
    await alerts.daily_report()
    content = _sent[0][1]
    assert "告警中" in content
    assert "我的GLM" in content
    assert "91%" in content
    db.delete_account(aid1)
    print("[PASS] 日报「告警中」区块正确汇总超阈值账户")


def test_daily_report_alerting():
    asyncio.run(_daily_report_with_alerting_async())


def test_notify_config_api_keys():
    """验证通知配置 key 白名单完整。"""
    from app.main import _NOTIFY_KEYS
    assert "notify_serverchan_key" in _NOTIFY_KEYS
    assert "notify_smtp_password" in _NOTIFY_KEYS
    assert "alert_balance_threshold" in _NOTIFY_KEYS
    print("[PASS] 通知配置 key 白名单完整")


if __name__ == "__main__":
    test_progress_bar()
    test_balance_alert_first_trigger()
    test_balance_alert_not_triggered()
    test_edge_trigger_persistent_state_no_resend()
    test_edge_trigger_recovery_then_retrigger()
    test_send_failure_does_not_set_state()
    test_window_alert_triggered()
    test_window_alert_not_triggered()
    test_error_result_no_alert_and_reset_state()
    test_custom_threshold_per_account()
    test_notify_config_api_keys()
    test_daily_report()
    test_daily_report_alerting()
    _cleanup_accounts()
    print("\n=== 阶段4 全部通过 ===")
