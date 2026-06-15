"""用联调时的真实响应样本做回归测试。

这些是 2026-06-15 联调时 4 家的真实返回结构，固化下来防止 parser 回归。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============ DeepSeek 真实样本 ============
DEEPSEEK_REAL = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "total_balance": "47.61",
            "granted_balance": "0.00",
            "topped_up_balance": "47.61",
        }
    ],
}


def test_deepseek_real():
    from app.providers import deepseek
    r = deepseek._parse(DEEPSEEK_REAL)
    assert r.raw_error is None
    assert r.balance == 47.61
    assert r.currency == "CNY"
    assert r.type == "balance"


# ============ GLM 真实样本（lite 套餐，只有 5h 窗口） ============
GLM_REAL = {
    "code": 200,
    "msg": "Operation successful",
    "data": {
        "limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 1, "nextResetTime": 1781546869977},
            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 100, "currentValue": 37,
             "remaining": 63, "percentage": 37, "nextResetTime": 1783419304994,
             "usageDetails": [{"modelCode": "search-prime", "usage": 27},
                              {"modelCode": "web-reader", "usage": 10},
                              {"modelCode": "zread", "usage": 0}]},
        ],
        "level": "lite",
    },
    "success": True,
}


def test_glm_real_lite_plan():
    """真实 lite 套餐：只有 1 个 5h 窗口 + 1 个 TIME_LIMIT（应被跳过）。"""
    from app.providers import glm
    from app.providers.base import TierType
    r = glm._parse(GLM_REAL)
    assert r.raw_error is None
    assert len(r.tiers) == 1  # TIME_LIMIT 被跳过
    assert r.tiers[0].type == TierType.FIVE_HOUR
    assert r.tiers[0].used_percent == 1.0
    assert r.plan_level == "lite"  # 套餐等级透出


# ============ Kimi 真实样本（detail 里有 used 字段） ============
KIMI_REAL = {
    "user": {"userId": "xxx", "region": "REGION_CN",
             "membership": {"level": "LEVEL_BASIC"}, "businessId": ""},
    "usage": {"limit": "100", "used": "38", "remaining": "62",
              "resetTime": "2026-06-21T02:14:35.888651Z"},
    "limits": [
        {"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
         "detail": {"limit": "100", "used": "6", "remaining": "94",
                    "resetTime": "2026-06-15T16:14:35.888651Z"}}
    ],
    "parallel": {"limit": "10"},
    "totalQuota": {"limit": "100", "remaining": "99"},
    "authentication": {"method": "METHOD_API_KEY", "scope": "FEATURE_CODING"},
    "subType": "TYPE_PURCHASE",
}


def test_kimi_real_with_used_field():
    """真实样本：detail 里有 used 字段，parser 应优先用它。数值是字符串。"""
    from app.providers import kimi
    from app.providers.base import TierType
    r = kimi._parse(KIMI_REAL)
    assert r.raw_error is None
    assert len(r.tiers) == 2
    five = next(t for t in r.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in r.tiers if t.type == TierType.WEEKLY)
    # used=6/limit=100 → 6%
    assert abs(five.used_percent - 6.0) < 0.01
    # used=38/limit=100 → 38%
    assert abs(weekly.used_percent - 38.0) < 0.01
    assert r.plan_level == "basic"  # LEVEL_BASIC → basic


# ============ MiniMax 真实样本（total_count=0，纯百分比计费） ============
MINIMAX_REAL = {
    "model_remains": [
        {
            "start_time": 1781524800000, "end_time": 1781539200000, "remains_time": 1938525,
            "current_interval_total_count": 0, "current_interval_usage_count": 0,
            "model_name": "general",
            "current_weekly_total_count": 0, "current_weekly_usage_count": 0,
            "weekly_start_time": 1781452800000, "weekly_end_time": 1782057600000,
            "weekly_remains_time": 520338525,
            "current_interval_status": 1, "current_interval_remaining_percent": 100,
            "current_weekly_status": 1, "current_weekly_remaining_percent": 99,
            "weekly_boost_permille": 1500,
        },
        {
            "start_time": 1781452800000, "end_time": 1781539200000, "remains_time": 1938525,
            "current_interval_total_count": 0, "current_interval_usage_count": 0,
            "model_name": "video",
            "current_weekly_total_count": 0, "current_weekly_usage_count": 0,
            "weekly_start_time": 1781452800000, "weekly_end_time": 1782057600000,
            "weekly_remains_time": 520338525,
            "current_interval_status": 3, "current_interval_remaining_percent": 100,
            "current_weekly_status": 3, "current_weekly_remaining_percent": 100,
        },
    ],
    "base_resp": {"status_code": 0, "status_msg": "success"},
}


def test_minimax_real_general_only():
    """真实样本：general 有效 + video 被跳过。total_count=0 不影响百分比解析。"""
    from app.providers import minimax
    from app.providers.base import TierType
    r = minimax._parse(MINIMAX_REAL)
    assert r.raw_error is None
    assert len(r.tiers) == 2
    five = next(t for t in r.tiers if t.type == TierType.FIVE_HOUR)
    weekly = next(t for t in r.tiers if t.type == TierType.WEEKLY)
    assert five.used_percent == 0.0     # remaining 100% → used 0%
    assert weekly.used_percent == 1.0   # remaining 99% → used 1%


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
