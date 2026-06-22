"""自定义 API（OpenAI / Anthropic 兼容）查询。

两种 API 格式（config.api_format，默认 openai）：
  - openai    ：OpenAI 兼容（OneAPI / NewAPI 等）。认证 `Authorization: Bearer {key}`。
  - anthropic ：Anthropic 兼容。认证 `x-api-key: {key}` + `anthropic-version`。

两种账户类型（config.account_type，默认 balance）：
  - balance   ：余额型，按金额扣费。查 /dashboard/billing/* 或 /api/user/self。
  - window    ：Token Plan（窗口型），按 5h/每周窗口算已用%。
                需额外填 config.quota_url（用量查询端点），用 GLM 的 quota/limit 格式解析。
                多数 Token Plan 中转站复刻 GLM Coding Plan 的响应结构。

base_url 语义（用户完全自主）：
  用户填什么就用什么，不臵测 /v1。仅末尾拼 /models。
  例：base=https://x.com/v1 → models=https://x.com/v1/models

模型列表（list_models）：两种格式都拉 {base}/models，仅认证头不同。

余额查询（query，balance 型，仅 openai 格式）：尽力而为，失败不报错。
  路径 1：/dashboard/billing/subscription + /dashboard/billing/usage
  路径 2：{domain}/api/user/self（NewAPI/OneAPI 原生）
  两路都失败 → balance=None，账户照常供模型拉取。

Window 查询（query，window 型）：GET {quota_url}，GLM quota/limit 格式解析。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .base import AdapterError, ProviderResult, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "自定义 API（OpenAI/Anthropic 兼容）"

# NewAPI/OneAPI 内部 quota 换算：1 美元 = 500000 quota
QUOTA_PER_USD = 500000.0

# Anthropic API 版本头（官方约定，各家代理基本沿用）
_ANTHROPIC_VERSION = "2023-06-01"


def _api_format(config: dict[str, Any]) -> str:
    """读取 api_format，默认 openai（向后兼容无此字段的老账户）。"""
    fmt = str(config.get("api_format") or "openai").strip().lower()
    return fmt if fmt in ("openai", "anthropic") else "openai"


def _account_type(config: dict[str, Any]) -> str:
    """读取账户类型，默认 balance（向后兼容老账户）。"""
    t = str(config.get("account_type") or "balance").strip().lower()
    return t if t in ("balance", "window") else "balance"


def _require_base_url(config: dict[str, Any]) -> str:
    base_url = str(config.get("base_url") or "").strip()
    if not base_url:
        raise AdapterError("请填写 API 站点地址（base_url）")
    return base_url


def _base_root(base_url: str) -> str:
    """规范化用户填的 base_url：去尾斜杠。不再臵测 /v1，用户填什么用什么。

    返回值用于直接拼 /models、/dashboard/billing/*。
    """
    s = base_url.strip().rstrip("/")
    if not s:
        raise AdapterError("站点地址不能为空")
    return s


def _domain_root(base_url: str) -> str:
    """去掉 base_url 末尾的 /v1（含大小写变体），返回站点域名根。

    专供 NewAPI/OneAPI 的 /api/user/self 用——该接口固定在站点根域，
    而用户可能把 base_url 填成了 https://x.com/v1。
    例：
      https://x.com/v1     → https://x.com
      https://x.com/api/v1 → https://x.com/api
      https://x.com        → https://x.com
    """
    s = base_url.strip().rstrip("/")
    lo = s.lower()
    if lo.endswith("/v1"):
        return s[:-3]
    return s


def _models_headers(api_key: str, fmt: str) -> dict[str, str]:
    if fmt == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Accept": "application/json",
        }
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


async def list_models(api_key: str, **config: Any) -> list:
    """拉取模型列表：GET {base}/models。两种格式仅认证头不同。"""
    from .base import fetch_models_openai_compat
    base = _base_root(_require_base_url(config))
    fmt = _api_format(config)
    headers = _models_headers(api_key, fmt)
    return await fetch_models_openai_compat(f"{base}/models", headers)


# ---- window 型（Token Plan）查询 ----
# 复用 GLM Coding Plan 的 quota/limit 响应格式（多数 token plan 中转站复刻此结构）。
# GLM 的 unit 字段：3=5小时窗口, 6=每周窗口。
_UNIT_FIVE_HOUR = 3
_UNIT_WEEKLY = 6


async def _query_window(api_key: str, fmt: str, config: dict[str, Any]) -> ProviderResult:
    """Token Plan 用量查询：GET {quota_url}，GLM quota/limit 格式解析。"""
    from .base import Tier, TierType
    raw: dict[str, Any] | None = None
    quota_url = str(config.get("quota_url") or "").strip()
    if not quota_url:
        return _safe_result(
            "openai_proxy", DISPLAY_NAME, "window",
            error="Token Plan 需填写用量查询端点 URL（quota_url）", raw=None,
        )

    headers = _models_headers(api_key, fmt)
    headers["Content-Type"] = "application/json"
    headers["Accept-Language"] = "en-US,en"

    try:
        raw = await http_get(quota_url, headers)
    except AdapterError as e:
        log.warning("Token Plan 用量查询失败: %s", e)
        return _safe_result("openai_proxy", DISPLAY_NAME, "window", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("Token Plan 用量查询异常")
        return _safe_result("openai_proxy", DISPLAY_NAME, "window", error=f"未知错误: {e}", raw=raw)

    # 解析 GLM quota/limit 格式
    if raw.get("success") is False or (
        raw.get("success") is None and raw.get("code") not in (None, 200)
    ):
        msg = raw.get("msg") or raw.get("message") or "Token Plan 业务错误"
        return _safe_result("openai_proxy", DISPLAY_NAME, "window", error=str(msg), raw=raw)

    data = raw.get("data") or {}
    limits = data.get("limits") or []
    level = data.get("level")

    tiers: list[Tier] = []
    five_hour_done = weekly_done = False
    for item in limits:
        item_type = str(item.get("type") or "").upper()
        if item_type != "TOKENS_LIMIT":
            continue
        # unit 兼容 int / str（GLM 原生 int，部分中转站返回字符串 "3"/"6"）
        raw_unit = item.get("unit")
        try:
            unit = int(raw_unit) if raw_unit not in (None, "") else None
        except (TypeError, ValueError):
            unit = None
        percentage = _to_float(item.get("percentage"))
        if percentage is None:
            continue
        # 区分窗口（优先 unit，fallback 按 nextResetTime 出现顺序）
        if unit == _UNIT_FIVE_HOUR and not five_hour_done:
            tier_type, five_hour_done = TierType.FIVE_HOUR, True
        elif unit == _UNIT_WEEKLY and not weekly_done:
            tier_type, weekly_done = TierType.WEEKLY, True
        elif unit is None and not five_hour_done:
            tier_type, five_hour_done = TierType.FIVE_HOUR, True
        elif unit is None and not weekly_done:
            tier_type, weekly_done = TierType.WEEKLY, True
        else:
            continue
        used = max(0.0, min(100.0, percentage))
        tiers.append(Tier(
            type=tier_type,
            used_percent=used,
            remaining_percent=100.0 - used,
            resets_at=_parse_timestamp(item.get("nextResetTime")),
            level=level,
        ))

    if not tiers:
        return _safe_result(
            "openai_proxy", DISPLAY_NAME, "window",
            error="响应中未找到 TOKENS_LIMIT 配额项（确认端点 URL 是否正确）", raw=raw,
        )
    return _safe_result(
        "openai_proxy", DISPLAY_NAME, "window",
        error=None, raw=raw, tiers=tiers, plan_level=level,
    )


def _parse_timestamp(v: Any) -> datetime | None:
    """兼容毫秒/秒时间戳（GLM 用毫秒）。"""
    if v is None or v == "":
        return None
    try:
        ms = int(v)
        if ms < 1_000_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


async def query(api_key: str, **config: Any) -> ProviderResult:
    acc_type = _account_type(config)
    fmt = _api_format(config)

    # ---- window 型（Token Plan）：调 quota_url，GLM 格式解析 ----
    if acc_type == "window":
        return await _query_window(api_key, fmt, config)

    # ---- balance 型 ----
    # anthropic 格式：无标准余额接口，静默提示（不报错）
    if fmt == "anthropic":
        return _safe_result(
            "openai_proxy", DISPLAY_NAME, "balance",
            error="Anthropic 格式不支持余额查询，请到「模型」页查看可用模型",
            raw=None,
        )

    try:
        base_url = _require_base_url(config)
        base = _base_root(base_url)
        domain = _domain_root(base_url)
    except AdapterError as e:
        # 配置缺失：返回错误结果（不抛异常，对外承诺只返回 ProviderResult）
        return _safe_result("openai_proxy", DISPLAY_NAME, "balance", error=str(e), raw=None)

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    # ---- 路径 1：dashboard billing（OpenAI 事实标准）----
    try:
        sub_raw = await http_get(f"{base}/dashboard/billing/subscription", headers)
        hard_limit = _parse_hard_limit_usd(sub_raw)
        if hard_limit is not None:
            used_usd = 0.0
            usage_raw = None
            try:
                usage_raw = await http_get(f"{base}/dashboard/billing/usage", headers)
                used_usd = _parse_total_usage_usd(usage_raw) or 0.0
            except AdapterError as e:
                log.debug("中转站 usage 接口不可用，按已用 0 处理: %s", e)
            balance = max(0.0, hard_limit - used_usd)
            return _safe_result(
                "openai_proxy", DISPLAY_NAME, "balance",
                error=None,
                raw={"subscription": sub_raw, "usage": usage_raw},
                balance=balance, currency="USD",
            )
    except AdapterError as e:
        log.debug("中转站 subscription 不可用，尝试 user/self: %s", e)

    # ---- 路径 2：/api/user/self（NewAPI/OneAPI 原生）----
    try:
        self_raw = await http_get(f"{domain}/api/user/self", headers)
        balance = _parse_user_self(self_raw)
        if balance is not None:
            return _safe_result(
                "openai_proxy", DISPLAY_NAME, "balance",
                error=None, raw={"user_self": self_raw},
                balance=max(0.0, balance), currency="USD",
            )
    except AdapterError as e:
        log.debug("中转站 user/self 不可用: %s", e)

    # 两路都拿不到余额：静默返回 balance=None（不报错，账户照常存在供模型拉取）
    log.info("自定义 API 余额查询无果（两路均不支持或无数据），账户仍可用于模型拉取")
    return _safe_result(
        "openai_proxy", DISPLAY_NAME, "balance",
        error=None, raw=None,
        balance=None, currency=None,
    )


# ---------------- 路径解析 ----------------

def _parse_hard_limit_usd(raw: dict[str, Any] | None) -> float | None:
    """从 /dashboard/billing/subscription 取 hard_limit_usd。

    OpenAI 原始结构在顶层；部分中转站把它包进 data 里，两种都兼容。
    raw 为 None 时返回 None（防御）。
    """
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    val = data.get("hard_limit_usd") if isinstance(data, dict) else None
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_total_usage_usd(raw: dict[str, Any] | None) -> float | None:
    """从 /dashboard/billing/usage 取 total_usage。

    total_usage 单位是美分（0.01 美元），÷100 得美元。
    结构可能是 {data: {total_usage: ...}} 或直接 {total_usage: ...}。
    raw 为 None 时返回 None（防御网络层意外返回空）。
    """
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    val = data.get("total_usage") if isinstance(data, dict) else None
    if val is None:
        return None
    try:
        return float(val) / 100.0
    except (TypeError, ValueError):
        return None


def _parse_user_self(raw: dict[str, Any] | None) -> float | None:
    """从 /api/user/self 取美元余额（quota ÷ 500000）。

    返回 None 表示响应里没有有效的 quota 字段。
    used_quota 不纳入余额（NewAPI 的 quota 本就是「剩余」口径），但保留在 raw 里供排障。
    raw 为 None 时返回 None（防御）。
    """
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        return None
    quota = _to_float(data.get("quota"))
    if quota is None:
        return None
    return quota / QUOTA_PER_USD


# ---------------- 工具 ----------------

def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
