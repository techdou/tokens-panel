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
    """有余额型账户但无快照 → 返回完整连续日期范围，series 含账户但 data 全 None。

    修复后行为：日期范围必须是连续 [起始日..今天]，即使无快照也不返回空 dates，
    否则图表只显示有数据的天（用户报告的"只显示一天"bug）。
    """
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "我的DS", enc)
    r = api_history_balance(days=7)
    assert r["has_balance_accounts"] is True
    assert r["keys"] == ["我的DS"]
    # dates 必须是连续的（7天范围 = 起始日到今天，至少 7 个点）
    assert len(r["dates"]) >= 7
    # series 含该账户，但 data 全 None（无快照）
    assert len(r["series"]) == 1
    assert r["series"][0]["name"] == "我的DS"
    assert all(v is None for v in r["series"][0]["data"])
    _cleanup()
    print(f"[PASS] 有账户无快照 → 完整日期范围({len(r['dates'])}天)，series data 全 None")


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


def test_continuous_date_range_sparse_snapshots():
    """关键回归：稀疏快照也必须返回连续完整日期范围，不能只显示有快照的天。

    复现用户报告的"只显示一天"bug：账户今天才加，只有 1 个快照，
    旧逻辑 dates 只含今天 → 图表只显示一天。修复后 dates 应是完整 [起始日..今天]。
    """
    _cleanup()
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "我的DS", enc)
    # 只有今天 1 条快照（模拟账户刚添加）
    today = datetime.now().strftime("%Y-%m-%d")
    db.add_snapshot(aid, {"type": "balance", "balance": 50.0, "currency": "CNY"},
                    fetched_at=f"{today} 10:00:00")
    r = api_history_balance(days=7)
    # dates 必须是连续 7+1 天（不是只有今天 1 天）
    assert len(r["dates"]) >= 7, f"日期范围应连续 ≥7 天，实际只有 {len(r['dates'])} 天"
    # 验证日期连续性（相邻日期差 1 天）
    from datetime import date as date_cls
    d_list = [date_cls.fromisoformat(d) for d in r["dates"]]
    for i in range(1, len(d_list)):
        assert (d_list[i] - d_list[i-1]).days == 1, f"日期不连续：{d_list[i-1]} → {d_list[i]}"
    # 今天的值是 50，之前的天是 None（账户当时不存在）
    assert r["series"][0]["data"][-1] == 50.0
    _cleanup()
    print(f"[PASS] 稀疏快照（仅今天）仍返回连续 {len(r['dates'])} 天日期范围")


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
    test_continuous_date_range_sparse_snapshots()
    test_multiple_balance_accounts()
    test_window_accounts_excluded()
    test_days_clamped()
    print("\n=== 余额历史接口测试全部通过 ===")
