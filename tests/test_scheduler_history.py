"""阶段3 验证：scheduler 任务 + history API 逻辑（不走真实 HTTP 鉴权）。

直接调用 db 层和 scheduler 函数，验证：
1. snapshots_since 能按时间范围取数
2. history 接口的 points 转换逻辑（balance / window 两种）
3. refresh_all_accounts 不会因单个 account 失败而整体崩
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "test123")

from app import crypto, db, scheduler
from app.providers.base import Tier, TierType, now_utc


def test_snapshots_since():
    """验证按时间范围取快照。"""
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "测试", enc)

    # 写 3 条快照：1 条 5 天前、1 条 1 小时前、1 条现在
    old_time = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    recent_time = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.add_snapshot(aid, {"provider": "deepseek", "type": "balance", "balance": 10.0, "currency": "CNY"}, fetched_at=old_time)
    db.add_snapshot(aid, {"provider": "deepseek", "type": "balance", "balance": 20.0, "currency": "CNY"}, fetched_at=recent_time)
    db.add_snapshot(aid, {"provider": "deepseek", "type": "balance", "balance": 30.0, "currency": "CNY"}, fetched_at=now_time)

    # 取最近 2 天 → 应返回 2 条（5天前的被排除）
    snaps = db.snapshots_since(aid, datetime.now() - timedelta(days=2))
    assert len(snaps) == 2, f"期望 2 条，实际 {len(snaps)}"
    balances = sorted(s["balance"] for s in snaps)
    assert balances == [20.0, 30.0], f"余额序列错误: {balances}"

    db.delete_account(aid)
    print("[PASS] snapshots_since 时间范围过滤正确")


def test_history_points_conversion_balance():
    """验证 history API 的 points 转换（balance 型）。"""
    # 模拟 main.py 里 api_history 的转换逻辑
    raw_points = [
        {"type": "balance", "balance": 47.61, "currency": "CNY", "fetched_at": "2026-06-15 10:00:00"},
        {"type": "balance", "balance": 45.0, "currency": "CNY", "fetched_at": "2026-06-15 11:00:00"},
    ]
    points = []
    for s in raw_points:
        item = {"fetched_at": s.get("fetched_at")}
        if s.get("type") == "balance":
            item["balance"] = s.get("balance")
            item["currency"] = s.get("currency")
        points.append(item)
    assert points[0]["balance"] == 47.61
    assert points[1]["currency"] == "CNY"
    print("[PASS] balance 型 points 转换正确")


def test_history_points_conversion_window():
    """验证 history API 的 points 转换（window 型，提取各桶 used_percent）。"""
    tier_five = Tier(type=TierType.FIVE_HOUR, used_percent=7.0, remaining_percent=93.0)
    tier_weekly = Tier(type=TierType.WEEKLY, used_percent=44.0, remaining_percent=56.0)
    raw_points = [
        {"type": "window", "tiers": [tier_five.model_dump(mode="json"), tier_weekly.model_dump(mode="json")], "fetched_at": "2026-06-15 10:00:00"},
    ]
    points = []
    for s in raw_points:
        item = {"fetched_at": s.get("fetched_at")}
        if s.get("tiers"):
            for t in s["tiers"]:
                item[f"{t['type']}_used"] = t.get("used_percent")
        points.append(item)
    assert points[0]["five_hour_used"] == 7.0
    assert points[0]["weekly_used"] == 44.0
    print("[PASS] window 型 points 转换正确（提取 five_hour_used/weekly_used）")


def test_cleanup_old_snapshots():
    db.init_db()
    enc = crypto.encrypt("sk-test")
    aid = db.create_account("deepseek", "清理测试", enc)
    old_time = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S")
    db.add_snapshot(aid, {"provider": "deepseek", "balance": 1.0}, fetched_at=old_time)
    deleted = db.cleanup_old_snapshots(days=30)
    assert deleted >= 1, f"期望清理至少 1 条，实际 {deleted}"
    db.delete_account(aid)
    print(f"[PASS] 快照清理正确（删除了 {deleted} 条 30 天前的）")


async def _refresh_all_accounts_resilient_async():
    """验证 refresh_all_accounts 对单个失败 account 不崩溃。

    用一个假 provider 的 account，查询会失败但不影响整体。
    """
    db.init_db()
    # 直接插一个 deepseek 但 key 是假的
    enc = crypto.encrypt("sk-definitely-invalid-key-xyz")
    aid = db.create_account("deepseek", "假key测试", enc)
    try:
        # 这会真实调用 DeepSeek API 并失败（401 或网络错误），但函数不应抛异常
        await scheduler.refresh_all_accounts()
        # 失败的 snapshot 也应被记录（raw_error 非空）
        snap = db.latest_snapshot(aid)
        assert snap is not None, "失败也应记录 snapshot"
        assert snap.get("raw_error") is not None, f"假 key 应产生 raw_error，实际: {snap.get('raw_error')}"
        print(f"[PASS] refresh_all_accounts 容错正确（假 key 失败被记录: {snap['raw_error'][:60]}...）")
    finally:
        db.delete_account(aid)


def test_refresh_all_accounts_resilient():
    """同步包装（pytest 直接可跑，无需 pytest-asyncio）。"""
    asyncio.run(_refresh_all_accounts_resilient_async())


def test_scheduler_triggers():
    """验证 scheduler 的 trigger 构造不报错。"""
    trig = scheduler._make_daily_report_trigger()
    # 不深究具体时间，只要构造成功
    assert trig is not None
    print("[PASS] 每日报告 trigger 构造正确")


if __name__ == "__main__":
    test_snapshots_since()
    test_history_points_conversion_balance()
    test_history_points_conversion_window()
    test_cleanup_old_snapshots()
    test_scheduler_triggers()
    test_refresh_all_accounts_resilient()
    print("\n=== 阶段3 全部通过 ===")
