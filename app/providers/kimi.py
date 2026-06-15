"""Kimi for Coding 用量查询。

端点（cc-switch 抓包所得，官方无公开文档）：
  GET https://api.kimi.com/coding/v1/usages
  Authorization: Bearer {key}

返回结构：
  {
    "limits": [
      {"detail": {"limit": 600, "remaining": 400, "resetTime": "..."}}   // 5 小时窗口
    ],
    "usage": {"limit": ..., "remaining": ..., "resetTime": "..."}        // 周限额
  }

注意：
  - resetTime 可能是字符串(ISO8601)或数字(秒/毫秒)，靠 < 1e12 判断
  - 官方 platform.kimi.com/docs/api/balance 查的是账户现金余额，不是 coding 用量，不用
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import AdapterError, ProviderResult, Tier, TierType, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "Kimi for Coding"
ENDPOINT = "https://api.kimi.com/coding/v1/usages"


async def query(api_key: str, **_config: Any) -> ProviderResult:
    raw: dict[str, Any] | None = None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        raw = await http_get(ENDPOINT, headers)
        return _parse(raw)
    except AdapterError as e:
        log.warning("Kimi 查询失败: %s", e)
        return _safe_result("kimi", DISPLAY_NAME, "window", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("Kimi 查询异常")
        return _safe_result("kimi", DISPLAY_NAME, "window", error=f"未知错误: {e}", raw=raw)


def _parse(raw: dict[str, Any]) -> ProviderResult:
    tiers: list[Tier] = []

    # 5 小时桶：limits[].detail
    limits = raw.get("limits") or []
    for item in limits:
        detail = item.get("detail") if isinstance(item, dict) else None
        if not detail:
            continue
        tier = _tier_from_detail(detail, TierType.FIVE_HOUR)
        if tier:
            tiers.append(tier)
            break  # 只取第一个有效的 5h 桶

    # 周桶：顶层 usage
    usage = raw.get("usage")
    if isinstance(usage, dict):
        tier = _tier_from_detail(usage, TierType.WEEKLY)
        if tier:
            tiers.append(tier)

    if not tiers:
        return _safe_result(
            "kimi", DISPLAY_NAME, "window",
            error="响应中未找到 usage / limits 配额信息", raw=raw,
        )

    # 透出套餐等级（membership.level 如 LEVEL_BASIC）
    user = raw.get("user") or {}
    membership = user.get("membership") or {}
    level = (membership.get("level") or "").replace("LEVEL_", "").lower() or None

    return _safe_result(
        "kimi", DISPLAY_NAME, "window",
        error=None, raw=raw, tiers=tiers, plan_level=level,
    )


def _tier_from_detail(detail: dict[str, Any], tier_type: TierType) -> Tier | None:
    """优先用显式的 used 字段（更精确）；缺失时回退到 limit-remaining。

    真实响应里 limit/used/remaining 都可能是字符串（如 "100"），_to_float 已处理。
    """
    limit = _to_float(detail.get("limit"))
    if limit is None or limit <= 0:
        return None

    used = _to_float(detail.get("used"))
    remaining = _to_float(detail.get("remaining"))

    if used is not None:
        # 直接用 used / limit（最精确）
        used_percent = max(0.0, min(100.0, used / limit * 100.0))
    elif remaining is not None:
        remaining = max(0.0, min(remaining, limit))
        used_percent = (limit - remaining) / limit * 100.0
    else:
        used_percent = 0.0

    return Tier(
        type=tier_type,
        used_percent=used_percent,
        remaining_percent=100.0 - used_percent,
        resets_at=_parse_time(detail.get("resetTime")),
    )


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_time(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    # 数字：秒或毫秒
    if isinstance(v, (int, float)):
        n = float(v)
        if n < 1_000_000_000_000:  # 秒
            n *= 1000
        try:
            return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    # 字符串：先试 ISO8601，再试纯数字字符串
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        n = float(s)
        if n < 1_000_000_000_000:
            n *= 1000
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
