"""三家窗口型 adapter 的 parser 单元测试。

样本来自 cc-switch 调研报告里记录的真实响应结构 + 各种边界情况。
覆盖：新老套餐、单/双桶、错误响应、字段缺失、时间戳格式差异。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============ GLM ============

def test_glm_new_plan_two_buckets():
    """新套餐：5h + 每周 两个 TOKENS_LIMIT，靠 unit 区分。"""
    from app.providers import glm
    from app.providers.base import TierType
    result = glm._parse({
        "code": 200, "msg": "操作成功", "success": True,
        "data": {
            "limits": [
                {"type": "TOKENS_LIMIT", "percentage": 7, "nextResetTime": 1774967594803, "unit": 3, "number": 5},
                {"type": "TOKENS_LIMIT", "percentage": 44, "nextResetTime": 1775226794803, "unit": 6, "number": 7},
            ],
            "level": "pro",
        },
    })
    assert result.raw_error is None
    assert len(result.tiers) == 2
    five = next(t for t in result.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in result.tiers if t.type == TierType.WEEKLY)
    assert five.used_percent == 7.0
    assert five.remaining_percent == 93.0
    assert weekly.used_percent == 44.0
    assert weekly.level == "pro"
    assert five.resets_at is not None


def test_glm_old_plan_single_bucket():
    """老套餐：只有 1 条 TOKENS_LIMIT，且可能无 unit 字段。"""
    from app.providers import glm
    from app.providers.base import TierType
    result = glm._parse({
        "success": True, "data": {
            "limits": [
                {"type": "TOKENS_LIMIT", "percentage": 20, "nextResetTime": 1774967594803},
            ],
            "level": "pro",
        },
    })
    assert result.raw_error is None
    assert len(result.tiers) == 1
    assert result.tiers[0].type == TierType.FIVE_HOUR  # 老套餐无 unit 默认当 5h
    assert result.tiers[0].used_percent == 20.0


def test_glm_unit_disambiguation_critical():
    """⚠️ 关键坑：周期末尾每周窗口可能比 5h 更早重置，
    必须按 unit 而非 nextResetTime 区分。本例构造这种场景。"""
    from app.providers import glm
    from app.providers.base import TierType
    result = glm._parse({
        "success": True, "data": {
            "limits": [
                # 注意：5h 窗口的 nextResetTime 比 weekly 大！按时间排序会标反
                {"type": "TOKENS_LIMIT", "percentage": 80, "nextResetTime": 1775300000000, "unit": 3},
                {"type": "TOKENS_LIMIT", "percentage": 30, "nextResetTime": 1775200000000, "unit": 6},
            ],
        },
    })
    five = next(t for t in result.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in result.tiers if t.type == TierType.WEEKLY)
    assert five.used_percent == 80.0   # unit=3 → 5h
    assert weekly.used_percent == 30.0  # unit=6 → weekly


def test_glm_business_error():
    from app.providers import glm
    result = glm._parse({"success": False, "msg": "API Key 无效"})
    assert result.raw_error == "API Key 无效"
    assert result.tiers is None or result.tiers == []


def test_glm_no_limits():
    from app.providers import glm
    result = glm._parse({"success": True, "data": {"limits": []}})
    assert result.raw_error is not None


def test_glm_skip_non_tokens_limit():
    """跳过 TIME_LIMIT 等非配额项。"""
    from app.providers import glm
    from app.providers.base import TierType
    result = glm._parse({
        "success": True, "data": {
            "limits": [
                {"type": "TIME_LIMIT", "percentage": 50, "usage": 1000, "currentValue": 72, "remaining": 928},
                {"type": "TOKENS_LIMIT", "percentage": 10, "unit": 3},
            ],
        },
    })
    assert result.raw_error is None
    assert len(result.tiers) == 1
    assert result.tiers[0].type == TierType.FIVE_HOUR


# ============ Kimi ============

def test_kimi_two_buckets():
    from app.providers import kimi
    from app.providers.base import TierType
    result = kimi._parse({
        "limits": [
            {"detail": {"limit": 600, "remaining": 400, "resetTime": "2026-06-15T20:00:00Z"}},
        ],
        "usage": {"limit": 3000, "remaining": 2500, "resetTime": 1775226794},
    })
    assert result.raw_error is None
    assert len(result.tiers) == 2
    five = next(t for t in result.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in result.tiers if t.type == TierType.WEEKLY)
    # 5h: used = (600-400)/600 = 33.33%
    assert abs(five.used_percent - 33.333) < 0.1
    # weekly: used = (3000-2500)/3000 = 16.67%
    assert abs(weekly.used_percent - 16.667) < 0.1


def test_kimi_resettime_mixed_formats():
    """resetTime 一个 ISO8601 字符串、一个秒级数字。"""
    from app.providers import kimi
    result = kimi._parse({
        "limits": [{"detail": {"limit": 100, "remaining": 50, "resetTime": "2026-06-15T20:00:00Z"}}],
        "usage": {"limit": 1000, "remaining": 800, "resetTime": 1775226794},
    })
    for t in result.tiers:
        assert t.resets_at is not None


def test_kimi_only_five_hour():
    from app.providers import kimi
    from app.providers.base import TierType
    result = kimi._parse({
        "limits": [{"detail": {"limit": 100, "remaining": 90}}],
        # 没有 usage
    })
    assert result.raw_error is None
    assert len(result.tiers) == 1
    assert result.tiers[0].type == TierType.FIVE_HOUR


def test_kimi_empty():
    from app.providers import kimi
    result = kimi._parse({})
    assert result.raw_error is not None


def test_kimi_zero_remaining():
    from app.providers import kimi
    result = kimi._parse({
        "limits": [{"detail": {"limit": 100, "remaining": 0}}],
    })
    assert result.tiers[0].used_percent == 100.0
    assert result.tiers[0].remaining_percent == 0.0


# ============ MiniMax ============

def test_minimax_new_fields_two_buckets():
    """新版百分比字段 + 周桶 status==1。"""
    from app.providers import minimax
    from app.providers.base import TierType
    result = minimax._parse({
        "model_remains": [
            {
                "model_name": "general",
                "current_interval_remaining_percent": 98.0,
                "current_weekly_remaining_percent": 95.0,
                "current_interval_status": 1,
                "current_weekly_status": 1,
                "end_time": 1780329600000,
                "weekly_end_time": 1780848000000,
            },
            {
                "model_name": "video",
                "current_interval_remaining_percent": 99.0,
            },
        ],
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })
    assert result.raw_error is None
    assert len(result.tiers) == 2
    five = next(t for t in result.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in result.tiers if t.type == TierType.WEEKLY)
    assert five.used_percent == 2.0     # 100 - 98
    assert weekly.used_percent == 5.0   # 100 - 95


def test_minimax_skip_video():
    """model_remains 里含 video，必须被跳过。"""
    from app.providers import minimax
    result = minimax._parse({
        "model_remains": [
            {"model_name": "video", "current_interval_remaining_percent": 50.0},
            {"model_name": "general", "current_interval_remaining_percent": 80.0, "current_weekly_status": 3},
        ],
        "base_resp": {"status_code": 0},
    })
    assert result.raw_error is None
    # weekly_status==3 → 无周桶，只有 5h
    assert len(result.tiers) == 1
    assert result.tiers[0].used_percent == 20.0  # 100-80


def test_minimax_weekly_status_3_no_weekly():
    """current_weekly_status==3 表示无周限额，不应展示。"""
    from app.providers import minimax
    from app.providers.base import TierType
    result = minimax._parse({
        "model_remains": [{
            "model_name": "general",
            "current_interval_remaining_percent": 70.0,
            "current_weekly_remaining_percent": 60.0,  # 即使有值，status!=1 也不展示
            "current_weekly_status": 3,
        }],
        "base_resp": {"status_code": 0},
    })
    assert len(result.tiers) == 1
    assert result.tiers[0].type == TierType.FIVE_HOUR


def test_minimax_business_error():
    from app.providers import minimax
    result = minimax._parse({
        "model_remains": [],
        "base_resp": {"status_code": 1001, "status_msg": "无效的 API Key"},
    })
    assert result.raw_error == "无效的 API Key"


def test_minimax_old_fields_fallback():
    """旧版绝对计数字段兼容（百分比字段缺失时回退）。
    字段名 usage_count 实际存 remaining。"""
    from app.providers import minimax
    result = minimax._parse({
        "model_remains": [{
            "model_name": "general",
            "current_interval_usage_count": 80,   # 实际是 remaining
            "current_interval_total_count": 100,
        }],
        "base_resp": {"status_code": 0},
    })
    assert result.raw_error is None
    assert len(result.tiers) == 1
    # remaining 80/100 = 80% → used 20%
    assert abs(result.tiers[0].used_percent - 20.0) < 0.1


def test_minimax_no_general():
    from app.providers import minimax
    result = minimax._parse({
        "model_remains": [{"model_name": "video", "current_interval_remaining_percent": 50.0}],
        "base_resp": {"status_code": 0},
    })
    assert result.raw_error is not None  # 只有 video，无 general


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {fn.__name__}: {e!r}")
            import traceback
            traceback.print_exc()
    print(f"\n=== {passed}/{len(funcs)} passed ===")
    sys.exit(0 if passed == len(funcs) else 1)
