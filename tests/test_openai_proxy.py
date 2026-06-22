"""自定义 API（OpenAI/Anthropic 兼容）adapter 测试。

覆盖：
- _base_root / _domain_root 的 base_url 规范化（用户填什么用什么，不臵测 /v1）
- _parse_hard_limit_usd / _parse_total_usage_usd（顶层 + data 包裹）
- _parse_user_self（quota ÷ 500000）
- query 的两路回退（路径1优先，失败转路径2）
- 两路都失败时【不报错】，返回 balance=None（账户照常存在）
- _require_base_url 缺失时报错
- api_format 区分（anthropic 不查余额）
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers import openai_proxy
from app.providers.base import AdapterError


# ============ _base_root（用户填什么用什么，不臵测 /v1）============

def test_base_root_bare_domain():
    """裸域名 → 原样返回（不再自动加 /v1）。"""
    assert openai_proxy._base_root("https://x.com") == "https://x.com"


def test_base_root_trailing_slash():
    """尾斜杠去掉。"""
    assert openai_proxy._base_root("https://x.com/") == "https://x.com"


def test_base_root_with_v1_preserved():
    """用户填了 /v1 → 保留（尊重用户）。"""
    assert openai_proxy._base_root("https://x.com/v1") == "https://x.com/v1"


def test_base_root_with_custom_path():
    """带自定义路径（如 /api/v1）→ 原样保留。"""
    assert openai_proxy._base_root("https://x.com/api/v1") == "https://x.com/api/v1"


def test_base_root_empty():
    """空 → 报错。"""
    try:
        openai_proxy._base_root("  ")
        assert False, "应抛 AdapterError"
    except AdapterError:
        pass


# ============ _domain_root（去末尾 /v1，供 user/self 用）============

def test_domain_root_strips_v1():
    assert openai_proxy._domain_root("https://x.com/v1") == "https://x.com"


def test_domain_root_strips_custom_path_v1():
    assert openai_proxy._domain_root("https://x.com/api/v1") == "https://x.com/api"


def test_domain_root_no_v1():
    """无 /v1 → 原样。"""
    assert openai_proxy._domain_root("https://x.com") == "https://x.com"


def test_domain_root_case_insensitive():
    assert openai_proxy._domain_root("https://x.com/V1") == "https://x.com"


# ============ _require_base_url ============

def test_require_base_url_missing():
    try:
        openai_proxy._require_base_url({})
        assert False, "应抛 AdapterError"
    except AdapterError as e:
        assert "base_url" in str(e)


def test_require_base_url_empty():
    try:
        openai_proxy._require_base_url({"base_url": "  "})
        assert False, "应抛 AdapterError"
    except AdapterError:
        pass


# ============ _parse_hard_limit_usd ============

def test_parse_hard_limit_top_level():
    raw = {"hard_limit_usd": 100.0}
    assert openai_proxy._parse_hard_limit_usd(raw) == 100.0


def test_parse_hard_limit_in_data():
    raw = {"data": {"hard_limit_usd": "50.5"}}
    assert openai_proxy._parse_hard_limit_usd(raw) == 50.5


def test_parse_hard_limit_missing():
    assert openai_proxy._parse_hard_limit_usd({"foo": "bar"}) is None


def test_parse_hard_limit_zero_or_negative():
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": 0}) is None
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": -1}) is None


def test_parse_hard_limit_invalid_type():
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": "abc"}) is None


# ============ _parse_total_usage_usd ============

def test_parse_usage_top_level():
    assert openai_proxy._parse_total_usage_usd({"total_usage": 1200}) == 12.0


def test_parse_usage_in_data():
    assert openai_proxy._parse_total_usage_usd({"data": {"total_usage": "500"}}) == 5.0


def test_parse_usage_missing():
    assert openai_proxy._parse_total_usage_usd({}) is None


# ============ _parse_user_self ============

def test_parse_user_self_quota():
    raw = {"data": {"quota": 5000000}}
    assert openai_proxy._parse_user_self(raw) == 10.0


def test_parse_user_self_no_data_wrapper():
    raw = {"quota": 250000}
    assert openai_proxy._parse_user_self(raw) == 0.5


def test_parse_user_self_missing_quota():
    assert openai_proxy._parse_user_self({"data": {"used_quota": 100}}) is None


def test_parse_user_self_invalid():
    assert openai_proxy._parse_user_self({"data": "not a dict"}) is None


# ============ query 路径回退（mock http_get）============

def _run_query_with_mocks(sub_resp, usage_resp, self_resp, sub_raises=None, self_raises=None, base_url="https://relay.example.com"):
    """用 mock 控制 http_get 返回，测 query 的回退逻辑。"""
    async def runner():
        async def fake_http_get(url, headers):
            if "subscription" in url:
                if sub_raises:
                    raise sub_raises
                return sub_resp
            if "usage" in url:
                return usage_resp
            if "user/self" in url:
                if self_raises:
                    raise self_raises
                return self_resp
            raise AdapterError(f"未预期的 url: {url}")

        with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
            return await openai_proxy.query("sk-test", base_url=base_url)
    return asyncio.run(runner())


def test_query_path1_subscription_success():
    """路径1：subscription + usage 都成功 → 余额 = limit - used"""
    result = _run_query_with_mocks(
        sub_resp={"hard_limit_usd": 100.0},
        usage_resp={"total_usage": 1200},  # 12 美元
        self_resp=None,
    )
    assert result.raw_error is None
    assert result.balance == 88.0  # 100 - 12
    assert result.currency == "USD"


def test_query_path1_usage_fails_treats_as_zero():
    """路径1：subscription 成功，usage 接口报错 → 按已用 0。"""
    async def fake_http_get(url, headers):
        if "subscription" in url:
            return {"hard_limit_usd": 50.0}
        if "usage" in url:
            raise AdapterError("404")
        raise AdapterError("unexpected")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.raw_error is None
    assert result.balance == 50.0


def test_query_path2_fallback_to_user_self():
    """路径1 失败 → 回退路径2 user/self"""
    async def fake_http_get(url, headers):
        if "subscription" in url:
            raise AdapterError("404")
        if "user/self" in url:
            return {"data": {"quota": 2500000}}  # 5 美元
        raise AdapterError("unexpected")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.raw_error is None
    assert result.balance == 5.0
    assert result.currency == "USD"


def test_query_both_paths_fail_silent():
    """两路都失败 → 不报错，balance=None（账户照常存在供模型拉取）。"""
    async def fake_http_get(url, headers):
        raise AdapterError("404")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.raw_error is None  # 关键：不再报错
    assert result.balance is None
    assert result.currency is None


def test_query_no_base_url():
    """没配 base_url → 报错提示"""
    result = asyncio.run(openai_proxy.query("sk-test"))
    assert result.raw_error is not None
    assert "base_url" in result.raw_error


def test_query_balance_never_negative():
    """used > limit 时余额不出现负数"""
    async def fake_http_get(url, headers):
        if "subscription" in url:
            return {"hard_limit_usd": 10.0}
        if "usage" in url:
            return {"total_usage": 5000}  # 50 美元 > 10 limit
        raise AdapterError("unexpected")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.balance == 0.0  # max(0, 10-50) = 0


# ============ window 型（Token Plan）============

def test_account_type_defaults_to_balance():
    """无 account_type 字段 → 默认 balance（向后兼容老账户）。"""
    assert openai_proxy._account_type({}) == "balance"
    assert openai_proxy._account_type({"account_type": ""}) == "balance"


def test_account_type_invalid_falls_back():
    """非法值 → 回退 balance。"""
    assert openai_proxy._account_type({"account_type": "something"}) == "balance"


def test_window_query_no_quota_url():
    """window 型但没填 quota_url → 提示需填 URL（不报错）。"""
    result = asyncio.run(openai_proxy.query(
        "sk-test", base_url="https://x.com/v1", account_type="window",
    ))
    assert result.type == "window"
    assert result.raw_error is not None
    assert "quota_url" in result.raw_error


def test_window_query_glm_format_success():
    """window 型 + GLM 格式响应 → 正确解析出 5h/每周窗口进度条。"""
    glm_response = {
        "success": True,
        "data": {
            "level": "pro",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "percentage": 12.5, "nextResetTime": 1719000000000},
                {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 45.0, "nextResetTime": 1719600000000},
            ],
        },
    }

    async def fake_http_get(url, headers):
        return glm_response

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query(
            "sk-test", base_url="https://x.com/v1",
            account_type="window", quota_url="https://x.com/api/monitor/usage/quota/limit",
        ))
    assert result.type == "window"
    assert result.raw_error is None
    assert result.plan_level == "pro"
    assert len(result.tiers) == 2
    # 5h 窗口 12.5%
    five_h = [t for t in result.tiers if t.type.value == "five_hour"][0]
    assert five_h.used_percent == 12.5
    assert five_h.remaining_percent == 87.5
    # 每周窗口 45%
    weekly = [t for t in result.tiers if t.type.value == "weekly"][0]
    assert weekly.used_percent == 45.0
    assert weekly.resets_at is not None


def test_window_query_unit_as_string():
    """unit 字段兼容字符串（部分中转站返回 "3"/"6" 而非 int）。"""
    response = {
        "success": True,
        "data": {"limits": [
            {"type": "TOKENS_LIMIT", "unit": "3", "percentage": 20.0},
            {"type": "TOKENS_LIMIT", "unit": "6", "percentage": 60.0},
        ]},
    }
    async def fake_http_get(url, headers):
        return response
    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query(
            "sk-test", base_url="https://x.com/v1",
            account_type="window", quota_url="https://x.com/q",
        ))
    assert result.raw_error is None
    assert len(result.tiers) == 2
    assert result.tiers[0].type.value == "five_hour"
    assert result.tiers[1].type.value == "weekly"


def test_window_query_no_tokens_limit():
    """window 型 + 响应无 TOKENS_LIMIT → 提示端点 URL 可能不对。"""
    response = {"success": True, "data": {"limits": []}}

    async def fake_http_get(url, headers):
        return response

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query(
            "sk-test", base_url="https://x.com/v1",
            account_type="window", quota_url="https://x.com/wrong",
        ))
    assert result.type == "window"
    assert result.raw_error is not None
    assert "TOKENS_LIMIT" in result.raw_error


def test_window_query_business_error():
    """window 型 + success:false → 显示业务错误。"""
    response = {"success": False, "msg": "key 已过期"}

    async def fake_http_get(url, headers):
        return response

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query(
            "sk-test", base_url="https://x.com/v1",
            account_type="window", quota_url="https://x.com/q",
        ))
    assert result.type == "window"
    assert "过期" in result.raw_error


def test_window_query_http_error():
    """window 型 + HTTP 错误 → 显示错误。"""
    async def fake_http_get(url, headers):
        raise AdapterError("HTTP 401")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query(
            "sk-test", base_url="https://x.com/v1",
            account_type="window", quota_url="https://x.com/q",
        ))
    assert result.type == "window"
    assert result.raw_error is not None
    assert "401" in result.raw_error


# ============ api_format 区分 ============

def test_query_anthropic_format_no_balance_query():
    """anthropic 格式 → 不查余额，返回提示（非异常）。"""
    result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com", api_format="anthropic"))
    assert result.balance is None
    assert result.raw_error is not None  # 提示文案
    assert "Anthropic" in result.raw_error


def test_api_format_defaults_to_openai():
    """无 api_format 字段 → 默认 openai（向后兼容老账户）。"""
    assert openai_proxy._api_format({}) == "openai"
    assert openai_proxy._api_format({"api_format": ""}) == "openai"


def test_api_format_invalid_falls_back():
    """非法值 → 回退 openai。"""
    assert openai_proxy._api_format({"api_format": "something"}) == "openai"


# ============ list_models 端点拼接 ============

def test_list_models_uses_base_directly():
    """list_models 直接用 base_url 拼 /models，不臵测 /v1。"""
    captured_url = []

    async def fake_fetch(url, headers):
        captured_url.append(url)
        return []

    with patch("app.providers.base.fetch_models_openai_compat", new=fake_fetch):
        asyncio.run(openai_proxy.list_models("sk-test", base_url="https://x.com/custom/api"))
    assert captured_url == ["https://x.com/custom/api/models"]


if __name__ == "__main__":
    funcs = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    passed, failed = 0, []
    for name, fn in funcs:
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e!r}")
            import traceback
            traceback.print_exc()
            failed.append(name)
    print(f"\n=== {passed}/{len(funcs)} passed ===")
    sys.exit(0 if not failed else 1)
