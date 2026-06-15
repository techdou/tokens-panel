"""统一数据模型 + adapter 接口。

两类额度：
  - balance 型（如 DeepSeek）：返回剩余金额
  - window 型（如 GLM/Kimi/MiniMax coding plan）：返回 5 小时桶 / 每周桶的已用百分比

所有 adapter 实现统一的 `query(api_key, **config) -> ProviderResult`，
认证细节、字段解析、各家坑都封装在 adapter 内部。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel


class TierType(str, Enum):
    FIVE_HOUR = "five_hour"
    WEEKLY = "weekly"


class Tier(BaseModel):
    """window 型的一个配额桶。"""
    type: TierType
    used_percent: float          # 已用百分比 0-100
    remaining_percent: float     # 剩余百分比 0-100（= 100 - used_percent，方便前端取用）
    resets_at: datetime | None = None  # 重置时间（UTC）
    level: str | None = None     # 套餐等级（如 GLM 的 pro），仅展示用


class ProviderResult(BaseModel):
    provider: str                       # 内部 key：deepseek / glm / kimi / minimax
    display_name: str                   # 展示名
    type: Literal["balance", "window"]

    # balance 型
    balance: float | None = None        # 剩余金额
    currency: str | None = None         # CNY / USD

    # window 型
    tiers: list[Tier] | None = None

    # 通用
    plan_level: str | None = None       # 套餐等级（如 GLM 的 lite/pro），仅展示用
    fetched_at: datetime
    raw_error: str | None = None        # 非空表示这次查询失败
    raw_response: dict[str, Any] | None = None  # 原始响应，调试用（可由设置关掉）


@runtime_checkable
class Adapter(Protocol):
    """每个 adapter 模块需提供的函数签名。"""

    async def query(self, api_key: str, **config: Any) -> ProviderResult: ...


class AdapterError(Exception):
    """adapter 内部抛出的、可展示给前端的业务错误。"""


# ---- 通用工具 ----

HTTP_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
HTTP_HEADERS_DEFAULT = {"User-Agent": "tokens-dashboard/0.1 (+self-hosted)"}


async def http_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """统一 GET + JSON 解析 + 错误处理。adapter 调它即可。"""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=HTTP_HEADERS_DEFAULT) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401:
            raise AdapterError("API Key 无效或已过期（401）")
        if resp.status_code == 429:
            raise AdapterError("请求过于频繁，被限流（429）")
        if resp.status_code >= 400:
            # 截断超长错误体
            body = resp.text[:300]
            raise AdapterError(f"HTTP {resp.status_code}: {body}")
        try:
            return resp.json()
        except ValueError as e:
            raise AdapterError(f"响应不是合法 JSON: {e}; body={resp.text[:200]}") from e


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_result(
    provider: str,
    display_name: str,
    result_type: Literal["balance", "window"],
    *,
    error: str | None,
    raw: dict[str, Any] | None,
    balance: float | None = None,
    currency: str | None = None,
    tiers: list[Tier] | None = None,
    plan_level: str | None = None,
) -> ProviderResult:
    """构造 ProviderResult 的统一出口（成功/失败都走这里，保证结构一致）。"""
    return ProviderResult(
        provider=provider,
        display_name=display_name,
        type=result_type,
        balance=balance,
        currency=currency,
        tiers=tiers,
        plan_level=plan_level,
        fetched_at=now_utc(),
        raw_error=error,
        raw_response=raw,
    )
