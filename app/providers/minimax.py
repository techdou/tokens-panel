"""MiniMax Coding Plan 用量查询。

端点：
  国内: https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains
  国际: https://api.minimax.io/v1/api/openplatform/coding_plan/remains
  Authorization: Bearer {key}

⚠️ 关键坑：
  1. 用【新版百分比字段】 current_interval_remaining_percent / current_weekly_remaining_percent
     不要用旧版 current_interval_usage_count（绝对计数，已废弃，且字段名误导：
     名叫 usage 实际存的是 remaining）
  2. model_remains[] 里【跳过 model_name=="video"】，只取 "general"
  3. current_weekly_status==1 才有周桶；==3 表示无周限额，展示会出错
  4. remaining_percent 是「剩余百分比」，需反转为「已用」：used = 100 - remaining
  5. base_resp.status_code != 0 → 业务错误，读 status_msg
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import AdapterError, ProviderResult, Tier, TierType, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "MiniMax Coding Plan"
ENDPOINT_CN = "https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains"
ENDPOINT_INTL = "https://api.minimax.io/v1/api/openplatform/coding_plan/remains"
MODELS_ENDPOINT_CN = "https://api.minimaxi.com/v1/models"
MODELS_ENDPOINT_INTL = "https://api.minimax.io/v1/models"


async def list_models(api_key: str, **config: Any) -> list:
    from .base import fetch_models_openai_compat
    endpoint = MODELS_ENDPOINT_INTL if str(config.get("region", "")).lower() in ("intl", "international", "io") else MODELS_ENDPOINT_CN
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    return await fetch_models_openai_compat(endpoint, headers)


async def query(api_key: str, **config: Any) -> ProviderResult:
    endpoint = ENDPOINT_INTL if str(config.get("region", "")).lower() in ("intl", "international", "io") else ENDPOINT_CN
    raw: dict[str, Any] | None = None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        raw = await http_get(endpoint, headers)
        return _parse(raw)
    except AdapterError as e:
        log.warning("MiniMax 查询失败: %s", e)
        return _safe_result("minimax", DISPLAY_NAME, "window", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("MiniMax 查询异常")
        return _safe_result("minimax", DISPLAY_NAME, "window", error=f"未知错误: {e}", raw=raw)


def _parse(raw: dict[str, Any]) -> ProviderResult:
    # 业务错误
    base_resp = raw.get("base_resp") or {}
    status_code = base_resp.get("status_code")
    if status_code not in (None, 0):
        msg = base_resp.get("status_msg") or f"MiniMax 业务错误 (status_code={status_code})"
        return _safe_result("minimax", DISPLAY_NAME, "window", error=str(msg), raw=raw)

    model_remains = raw.get("model_remains") or []
    # 只取 general，跳过 video
    general = next((m for m in model_remains if (m.get("model_name") or "").lower() == "general"), None)
    if general is None and model_remains:
        # 防御性：没有 general 就取第一个非 video 的
        general = next((m for m in model_remains if (m.get("model_name") or "").lower() != "video"), None)
    if general is None:
        return _safe_result(
            "minimax", DISPLAY_NAME, "window",
            error="响应中未找到 model_remains (general)", raw=raw,
        )

    tiers: list[Tier] = []

    # 5 小时窗口
    interval_remaining = _to_float(
        general.get("current_interval_remaining_percent"),
        # 旧版 fallback（绝对计数）——只在百分比字段缺失时尝试
        fallback_fn=lambda: _remaining_percent_from_count(general, "current_interval_usage_count", "current_interval_total_count"),
    )
    if interval_remaining is not None:
        tiers.append(_build_tier(
            TierType.FIVE_HOUR,
            interval_remaining,
            general.get("end_time"),
        ))

    # 每周窗口：仅当 current_weekly_status == 1 才展示（==3 表示无周限额）
    if general.get("current_weekly_status") == 1:
        weekly_remaining = _to_float(general.get("current_weekly_remaining_percent"))
        if weekly_remaining is not None:
            tiers.append(_build_tier(
                TierType.WEEKLY,
                weekly_remaining,
                general.get("weekly_end_time"),
            ))

    if not tiers:
        return _safe_result(
            "minimax", DISPLAY_NAME, "window",
            error="未能解析出任何配额窗口", raw=raw,
        )

    return _safe_result("minimax", DISPLAY_NAME, "window", error=None, raw=raw, tiers=tiers)


def _remaining_percent_from_count(g: dict[str, Any], remaining_key: str, total_key: str) -> float | None:
    """旧版字段兼容：字段名叫 usage 实际存 remaining。"""
    remaining = _to_float(g.get(remaining_key))
    total = _to_float(g.get(total_key))
    if remaining is None or total is None or total <= 0:
        return None
    return max(0.0, min(100.0, remaining / total * 100.0))


def _build_tier(tier_type: TierType, remaining_percent: float, end_time_ms: Any) -> Tier:
    remaining = max(0.0, min(100.0, remaining_percent))
    used = 100.0 - remaining
    return Tier(
        type=tier_type,
        used_percent=used,
        remaining_percent=remaining,
        resets_at=_parse_ms(end_time_ms),
    )


def _to_float(v: Any, fallback_fn=None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback_fn() if fallback_fn else None


def _parse_ms(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    try:
        ms = int(v)
        if ms < 1_000_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
