"""智谱 GLM Coding Plan 用量查询。

⚠️ 关键坑（来自 cc-switch 生产代码 + 社区多方验证）：
  1. Authorization 头【不加 Bearer 前缀】，直接放 API Key
  2. 加 Accept-Language: en-US,en 影响返回文案语言
  3. limits[] 区分 5h/每周窗口【必须用 unit 字段】，不能按 nextResetTime 排序：
       unit=3 → 5 小时窗口
       unit=6 → 每周窗口
     （周期末尾每周窗口会比 5h 窗口更早重置，按时间排序会标反）
  4. 老套餐只有 1 条 TOKENS_LIMIT（无每周限额）
  5. percentage 直接是「已用百分比」(0-100)，remaining = 100 - percentage
  6. type 字段大小写不敏感（TOKENS_LIMIT / tokens_limit 都见过）
  7. body.success==false 时读 body.msg 为业务错误

端点（国内/国际）：
  国内: https://open.bigmodel.cn/api/monitor/usage/quota/limit
  国际(z.ai): https://api.z.ai/api/monitor/usage/quota/limit
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import AdapterError, ProviderResult, Tier, TierType, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "智谱 GLM Coding Plan"
ENDPOINT_CN = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
ENDPOINT_INTL = "https://api.z.ai/api/monitor/usage/quota/limit"

# unit 字段含义（来自 cc-switch issue #3036）
UNIT_FIVE_HOUR = 3
UNIT_WEEKLY = 6


async def query(api_key: str, **config: Any) -> ProviderResult:
    endpoint = ENDPOINT_INTL if str(config.get("region", "")).lower() in ("intl", "international", "zai") else ENDPOINT_CN
    raw: dict[str, Any] | None = None
    try:
        # ⚠️ 不加 Bearer 前缀！这是 GLM 最大的坑
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en",
        }
        raw = await http_get(endpoint, headers)
        return _parse(raw)
    except AdapterError as e:
        log.warning("GLM 查询失败: %s", e)
        return _safe_result("glm", DISPLAY_NAME, "window", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("GLM 查询异常")
        return _safe_result("glm", DISPLAY_NAME, "window", error=f"未知错误: {e}", raw=raw)


def _parse(raw: dict[str, Any]) -> ProviderResult:
    # 业务错误（HTTP 200 但 success=false）
    if raw.get("success") is False or (
        raw.get("success") is None and raw.get("code") not in (None, 200)
    ):
        msg = raw.get("msg") or raw.get("message") or "GLM 业务错误"
        return _safe_result("glm", DISPLAY_NAME, "window", error=str(msg), raw=raw)

    data = raw.get("data") or {}
    limits = data.get("limits") or []
    level = data.get("level")  # 套餐等级，如 "pro"

    tiers: list[Tier] = []
    five_hour_done = weekly_done = False

    for item in limits:
        item_type = str(item.get("type") or "").upper()
        if item_type != "TOKENS_LIMIT":
            continue  # 跳过 TIME_LIMIT 等其它类型

        unit = item.get("unit")
        percentage = _to_float(item.get("percentage"))
        if percentage is None:
            continue

        # 区分窗口：优先用 unit；fallback 用 nextResetTime 数量（老接口可能无 unit）
        if unit == UNIT_FIVE_HOUR and not five_hour_done:
            tier_type, five_hour_done = TierType.FIVE_HOUR, True
        elif unit == UNIT_WEEKLY and not weekly_done:
            tier_type, weekly_done = TierType.WEEKLY, True
        elif unit in (None, "") and not five_hour_done:
            # 老套餐可能没有 unit 字段，第一条默认当作 5h
            tier_type, five_hour_done = TierType.FIVE_HOUR, True
        elif unit in (None, "") and not weekly_done:
            tier_type, weekly_done = TierType.WEEKLY, True
        else:
            continue  # 同一窗口重复条目，跳过

        tiers.append(_build_tier(tier_type, percentage, item, level))

    if not tiers:
        return _safe_result(
            "glm", DISPLAY_NAME, "window",
            error="响应中未找到 TOKENS_LIMIT 配额项", raw=raw,
        )

    return _safe_result(
        "glm", DISPLAY_NAME, "window",
        error=None, raw=raw, tiers=tiers,
        plan_level=level,  # 透出套餐等级（lite/pro 等）到结果层
    )


def _build_tier(tier_type: TierType, percentage: float, item: dict[str, Any], level: str | None) -> Tier:
    used = max(0.0, min(100.0, percentage))
    resets_at = _parse_timestamp(item.get("nextResetTime"))
    return Tier(
        type=tier_type,
        used_percent=used,
        remaining_percent=100.0 - used,
        resets_at=resets_at,
        level=level,
    )


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(v: Any) -> datetime | None:
    """GLM 返回毫秒时间戳。兼容字符串/数字/空值。"""
    if v is None or v == "":
        return None
    try:
        ms = int(v)
        # < 1e12 视为秒级（防御性，虽然 GLM 用毫秒）
        if ms < 1_000_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None
