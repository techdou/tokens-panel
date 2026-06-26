"""/api/history 河流图端点的数据处理逻辑测试。

覆盖：
- 按分钟桶聚合（去掉秒级差异，避免稀疏跳变）
- 前向填充（账户在某桶无快照时沿用上一个桶的值，不归零）
- has_window_accounts 标志（区分「无窗口型账户」与「有但无快照」）
- 余额型账户不纳入河流图
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "test123")

from app import crypto, db


def _setup():
    db.init_db()
    # 清掉所有账户
    for a in db.list_accounts():
        db.delete_account(a["id"])


def test_no_window_accounts():
    """只有余额型账户 → has_window_accounts=False。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "我的DS", enc)  # balance 型

    from app.main import api_history_all
    r = api_history_all(days=7)
    assert r["has_window_accounts"] is False
    assert r["keys"] == []
    assert r["series"] == []
    print("[PASS] 无窗口型账户 → has_window_accounts=False")
    _setup()


def test_window_account_no_snapshots():
    """有窗口型账户但无快照 → has_window_accounts=True, series=[]。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    db.create_account("glm", "我的GLM", enc)  # window 型，无快照

    from app.main import api_history_all
    r = api_history_all(days=7)
    assert r["has_window_accounts"] is True
    assert r["keys"] == ["我的GLM"]
    assert r["series"] == []
    print("[PASS] 窗口型账户无快照 → has_window_accounts=True, series=[]")
    _setup()


def test_forward_fill_no_zero_drops():
    """前向填充：账户在某分钟桶无快照 → 沿用上一桶值，不归零。

    模拟：GLM 在 10:00 有值 30%，10:01 这个桶没有 → 应填 30%（不是 0）。
    """
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("glm", "我的GLM", enc)

    # 两个时间点：10:00:05 和 10:01:30（不同桶，但 10:01 桶只有这个账户）
    db.add_snapshot(aid, {
        "type": "window", "raw_error": None,
        "tiers": [{"type": "five_hour", "used_percent": 30.0, "remaining_percent": 70.0}]
    }, fetched_at=(datetime.now().replace(hour=10, minute=0, second=5, microsecond=0)).strftime("%Y-%m-%d %H:%M:%S"))

    from app.main import api_history_all
    r = api_history_all(days=1)
    assert r["has_window_accounts"] is True
    assert len(r["series"]) >= 1
    # 所有时间点的值都应 >= 30（前向填充，不会归零）
    for row in r["series"]:
        assert row["我的GLM"] >= 30.0, f"前向填充失效，出现归零: {row}"
    print(f"[PASS] 前向填充：{len(r['series'])} 个时间点均 >= 30%，无跳变归零")
    _setup()


def test_minute_bucket_collapse():
    """同分钟内的多条快照归约到一个桶（去秒级差异）。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("glm", "我的GLM", enc)

    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    # 同一分钟内 3 条快照（10:00:01 / 10:00:30 / 10:00:59）
    for sec, val in [(1, 10.0), (30, 20.0), (59, 30.0)]:
        ts = (base + timedelta(seconds=sec)).strftime("%Y-%m-%d %H:%M:%S")
        db.add_snapshot(aid, {
            "type": "window", "raw_error": None,
            "tiers": [{"type": "five_hour", "used_percent": val, "remaining_percent": 100 - val}]
        }, fetched_at=ts)

    from app.main import api_history_all
    r = api_history_all(days=1)
    # 同分钟只应产生 1 个桶
    assert len(r["series"]) == 1, f"同分钟应归约成 1 桶，实际 {len(r['series'])}"
    # 桶内取最新（后写覆盖）
    assert r["series"][0]["我的GLM"] == 30.0
    print(f"[PASS] 分钟桶聚合：同分钟 3 条 → 1 桶，取最新值 30%")
    _setup()


def test_balance_account_excluded():
    """余额型账户不出现在河流图 keys 里。"""
    _setup()
    enc = crypto.encrypt("sk-test")
    db.create_account("deepseek", "我的DS", enc)  # balance 型
    db.create_account("glm", "我的GLM", enc)       # window 型

    from app.main import api_history_all
    r = api_history_all(days=7)
    assert "我的GLM" in r["keys"]
    assert "我的DS" not in r["keys"]
    print("[PASS] 余额型账户被排除在河流图外")
    _setup()


if __name__ == "__main__":
    test_no_window_accounts()
    test_window_account_no_snapshots()
    test_forward_fill_no_zero_drops()
    test_minute_bucket_collapse()
    test_balance_account_excluded()
    print("\n=== /api/history 河流图端点测试全部通过 ===")
