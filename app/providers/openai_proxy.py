"""自定义 API（OpenAI / Anthropic 兼容）查询。

支持两种 API 格式，由 account 的 config_json 里 `api_format` 字段决定（默认 openai）：
  - openai    ：OpenAI 兼容（含 OneAPI / NewAPI 等中转站）。认证用 `Authorization: Bearer {key}`。
  - anthropic ：Anthropic 兼容（官方或代理）。认证用 `x-api-key: {key}` + `anthropic-version` 头。

base_url 语义（用户完全自主）：
  用户填什么就用什么，系统【不再自动追加 /v1 或任何后缀】。仅去掉尾部斜杠。
  端点拼接规则（base 指用户填的 base_url）：
    模型列表    ：GET {base}/models
    余额-路径1  ：GET {base}/dashboard/billing/subscription、{base}/dashboard/billing/usage
    余额-路径2  ：GET {base 去掉末尾 /v1 后的域名根}/api/user/self（NewAPI/OneAPI 原生接口在根域）
  例：
    base=https://x.com/v1      → models=https://x.com/v1/models（用户自己填了 /v1）
    base=https://x.com         → models=https://x.com/models（用户没填就不加）
    base=https://x.com/api/v1  → models=https://x.com/api/v1/models

模型列表（list_models）：两种格式都拉 {base}/models，仅认证头不同。

余额查询（query，仅 openai 格式；anthropic 不查）：
  作为「尽力而为」尝试：两路都失败时【不报错】，返回 balance=None、无 raw_error，
  让账户正常存在、模型页能拉模型。成功才填 balance/currency。

  路径 1 —— OpenAI 事实标准 dashboard billing：
    余额 = hard_limit_usd - total_usage/100
  路径 2 —— NewAPI/OneAPI 原生：data.quota ÷ 500000 = 美元
"""
from __future__ import annotations

import logging
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


async def query(api_key: str, **config: Any) -> ProviderResult:
    # anthropic 格式：无标准余额查询接口，静默提示（不报错）
    if _api_format(config) == "anthropic":
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
