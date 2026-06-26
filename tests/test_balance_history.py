"""余额型跨账户历史接口测试：/api/history/balance

验证：
- 无余额型账户时返回 has_balance_accounts=False
- 有账户无快照时返回空 series
- 多账户多天数据正确聚合（按天取最后一条）
- 前向填充缺失天（避免断线）
- window 型账户不混入
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "test123")

from app import crypto, db
from app.main import api_history_balance


def _cleanup():
    for a in db.list_accounts():
        db.delete_account(a["id"])


def test_no_balance_accounts():
    """只有窗口型账户时，返回 has_balance_accounts=False。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    db.create_account("glm", "GLM测试", enc)  # window 型
    r = api_history_balance(days=7)
    assert r["has_balance_accounts"] is False
    assert r["series"] == []
    _cleanup()
    print("[PASS] 无余额型账户 → has_balance_accounts=False")


def test_balance_account_no_snapshots():
    """有余额型账户但无快照 → 空 series，has_balance_accounts=True。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "我的DS", enc)
    r = api_history_balance(days=7)
    assert r["has_balance_accounts"] is True
    assert r["series"] == []
    assert r["keys"] == ["我的DS"]
    _cleanup()
    print("[PASS] 有账户无快照 → 空 series")


def test_single_balance_account_daily_aggregation():
    """单账户：同一天多条快照只取最后一条。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "我的DS", enc)
    # 同一天写 3 条，余额递减
    today = datetime.now().strftime("%Y-%m-%d")
    db.add_snapshot(aid, {"type": "balance", "balance": 100.0, "currency": "CNY"},
                    fetched_at=f"{today} 08:00:00")
    db.add_snapshot(aid, {"type": "balance", "balance": 80.0, "currency": "CNY"},
                    fetched_at=f"{today} 12:00:00")
    db.add_snapshot(aid, {"type": "balance", "balance": 60.0, "currency": "CNY"},
                    fetched_at=f"{today} 20:00:00")
    r = api_history_balance(days=7)
    assert r["has_balance_accounts"] is True
    assert len(r["series"]) == 1
    assert r["series"][0]["name"] == "我的DS"
    assert r["series"][0]["currency"] == "CNY"
    # 当天最后一条是 60.0
    last_val = r["series"][0]["data"][-1]
    assert last_val == 60.0
    _cleanup()
    print("[PASS] 单账户按天聚合（取最后一条 60.0）")


def test_forward_fill_missing_days():
    """账户在某天没快照时，沿用上一日余额（前向填充）。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "我的DS", enc)
    d1 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    d3 = datetime.now().strftime("%Y-%m-%d")
    # 第1天 50，第2天无数据，第3天 30
    db.add_snapshot(aid, {"type": "balance", "balance": 50.0, "currency": "CNY"},
                    fetched_at=f"{d1} 10:00:00")
    db.add_snapshot(aid, {"type": "balance", "balance": 30.0, "currency": "CNY"},
                    fetched_at=f"{d3} 10:00:00")
    r = api_history_balance(days=7)
    data = r["series"][0]["data"]
    dates = r["dates"]
    # d1 对应 50，d2 应被前向填充为 50，d3 为 30
    idx_d1 = dates.index(d1)
    idx_d3 = dates.index(d3)
    assert data[idx_d1] == 50.0
    assert data[idx_d3] == 30.0
    # d1 和 d3 之间的天应该都是 50（前向填充）
    for i in range(idx_d1 + 1, idx_d3):
        assert data[i] == 50.0, f"日期 {dates[i]} 应前向填充为 50.0，实际 {data[i]}"
    _cleanup()
    print("[PASS] 前向填充缺失天正确")


def test_multiple_balance_accounts():
    """多个余额型账户各自一条线。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid1 = db.create_account("deepseek", "DS1", enc)
    aid2 = db.create_account("deepseek", "DS2", enc)
    today = datetime.now().strftime("%Y-%m-%d")
    db.add_snapshot(aid1, {"type": "balance", "balance": 10.0, "currency": "CNY"},
                    fetched_at=f"{today} 10:00:00")
    db.add_snapshot(aid2, {"type": "balance", "balance": 20.0, "currency": "CNY"},
                    fetched_at=f"{today} 10:00:00")
    r = api_history_balance(days=7)
    assert len(r["series"]) == 2
    names = {s["name"] for s in r["series"]}
    assert names == {"DS1", "DS2"}
    _cleanup()
    print("[PASS] 多余额账户各自成线")


def test_window_accounts_excluded():
    """window 型账户不应混入余额图。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "DS", enc)      # balance
    db.create_account("glm", "GLM", enc)           # window
    r = api_history_balance(days=7)
    assert "GLM" not in r["keys"]
    assert "DS" in r["keys"]
    _cleanup()
    print("[PASS] window 型账户被正确排除")


def test_days_clamped():
    """days 参数限制在 1-90。"""
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "DS", enc)
    # 超出范围不报错（被 clamp）
    r1 = api_history_balance(days=0)
    r2 = api_history_balance(days=999)
    assert r1["has_balance_accounts"] is True
    assert r2["has_balance_accounts"] is True
    _cleanup()
    print("[PASS] days 参数 clamp 正常")


if __name__ == "__main__":
    test_no_balance_accounts()
    test_balance_account_no_snapshots()
    test_single_balance_account_daily_aggregation()
    test_forward_fill_missing_days()
    test_multiple_balance_accounts()
    test_window_accounts_excluded()
    test_days_clamped()
    print("\n=== 余额历史接口测试全部通过 ===")
