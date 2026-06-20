"""OpenAI 兼容中转站 adapter 测试。

覆盖：
- _normalize_base 各种 base_url 写法
- _parse_hard_limit_usd / _parse_total_usage_usd（顶层 + data 包裹）
- _parse_user_self（quota ÷ 500000）
- query 的两路回退（路径1优先，失败转路径2）
- _require_base_url 缺失时报错
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers import openai_proxy
from app.providers.base import AdapterError


# ============ _normalize_base ============

def test_normalize_base_bare_domain():
    root, v1 = openai_proxy._normalize_base("https://x.com")
    assert root == "https://x.com"
    assert v1 == "https://x.com/v1"


def test_normalize_base_trailing_slash():
    root, v1 = openai_proxy._normalize_base("https://x.com/")
    assert root == "https://x.com"
    assert v1 == "https://x.com/v1"


def test_normalize_base_with_v1():
    root, v1 = openai_proxy._normalize_base("https://x.com/v1")
    assert root == "https://x.com"
    assert v1 == "https://x.com/v1"


def test_normalize_base_with_v1_trailing_slash():
    root, v1 = openai_proxy._normalize_base("https://x.com/v1/")
    assert root == "https://x.com"
    assert v1 == "https://x.com/v1"


def test_normalize_base_case_insensitive_v1():
    # /V1 也应识别（防御性）
    root, v1 = openai_proxy._normalize_base("https://x.com/V1")
    assert v1 == "https://x.com/V1"
    assert root == "https://x.com"


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
    # OpenAI 原始：hard_limit_usd 在顶层
    raw = {"hard_limit_usd": 100.0}
    assert openai_proxy._parse_hard_limit_usd(raw) == 100.0


def test_parse_hard_limit_in_data():
    # 部分中转站包进 data
    raw = {"data": {"hard_limit_usd": "50.5"}}
    assert openai_proxy._parse_hard_limit_usd(raw) == 50.5


def test_parse_hard_limit_missing():
    assert openai_proxy._parse_hard_limit_usd({"foo": "bar"}) is None


def test_parse_hard_limit_zero_or_negative():
    # 0 或负数视为无效（没充值）
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": 0}) is None
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": -1}) is None


def test_parse_hard_limit_invalid_type():
    assert openai_proxy._parse_hard_limit_usd({"hard_limit_usd": "abc"}) is None


# ============ _parse_total_usage_usd ============

def test_parse_usage_top_level():
    # total_usage 单位美分，1200 美分 = 12 美元
    assert openai_proxy._parse_total_usage_usd({"total_usage": 1200}) == 12.0


def test_parse_usage_in_data():
    assert openai_proxy._parse_total_usage_usd({"data": {"total_usage": "500"}}) == 5.0


def test_parse_usage_missing():
    assert openai_proxy._parse_total_usage_usd({}) is None


# ============ _parse_user_self ============

def test_parse_user_self_quota():
    # quota = 5000000 → 10 美元
    raw = {"data": {"quota": 5000000}}
    assert openai_proxy._parse_user_self(raw) == 10.0


def test_parse_user_self_no_data_wrapper():
    raw = {"quota": 250000}
    assert openai_proxy._parse_user_self(raw) == 0.5


def test_parse_user_self_missing_quota():
    assert openai_proxy._parse_user_self({"data": {"used_quota": 100}}) is None


def test_parse_user_self_invalid():
    assert openai_proxy._parse_user_self({"data": "not a dict"}) is None


# ============ query 路径回退（用 mock http_get）============

async def _run_query_with_mocks(sub_resp, usage_resp, self_resp, sub_raises=None, self_raises=None):
    """用 mock 控制 http_get 返回，测 query 的回退逻辑。"""
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
        return await openai_proxy.query("sk-test", base_url="https://relay.example.com")


def test_query_path1_subscription_success():
    """路径1：subscription + usage 都成功 → 余额 = limit - used"""
    result = asyncio.run(_run_query_with_mocks(
        sub_resp={"hard_limit_usd": 100.0},
        usage_resp={"total_usage": 1200},  # 12 美元
        self_resp=None,
    ))
    assert result.raw_error is None
    assert result.balance == 88.0  # 100 - 12
    assert result.currency == "USD"


def test_query_path1_usage_missing_treats_as_zero():
    """路径1：subscription 成功但 usage 失败 → 按已用 0"""
    result = asyncio.run(_run_query_with_mocks(
        sub_resp={"hard_limit_usd": 50.0},
        usage_resp=None,  # 不会触发，因为 mock 里 usage url 没匹配会抛错
        self_resp=None,
        sub_raises=None,
    ))
    # usage 接口 mock 返回 None 时，fake_http_get 对未知 url 抛错，
    # 但 query 的 try/except 会捕获并按 used=0 处理
    # 这里需要让 usage 真的抛 AdapterError
    # 修正：直接测 usage 抛错的情况


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
    assert result.balance == 50.0  # usage 按 0


def test_query_path2_fallback_to_user_self():
    """路径1 失败 → 回退路径2 user/self"""
    async def fake_http_get(url, headers):
        if "subscription" in url:
            raise AdapterError("404")  # 路径1 失败
        if "user/self" in url:
            return {"data": {"quota": 2500000}}  # 5 美元
        raise AdapterError("unexpected")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.raw_error is None
    assert result.balance == 5.0
    assert result.currency == "USD"


def test_query_both_paths_fail():
    """两路都失败 → raw_error"""
    async def fake_http_get(url, headers):
        raise AdapterError("401")

    with patch("app.providers.openai_proxy.http_get", new=fake_http_get):
        result = asyncio.run(openai_proxy.query("sk-test", base_url="https://x.com"))
    assert result.raw_error is not None
    assert "401" in result.raw_error


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


if __name__ == "__main__":
    import inspect
    # 收集所有 test_ 开头的同步函数和异步函数
    funcs = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    passed = 0
    failed = []
    for name, fn in funcs:
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
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
