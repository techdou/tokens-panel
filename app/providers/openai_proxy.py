"""OpenAI 兼容中转站（OneAPI / NewAPI 等）余额查询。

与官方直连 provider 的区别：每个账户的站点地址（base_url）不同，
需从 account 的 config_json 里取 base_url（参考 GLM 用 config 存 region 的做法）。

查询策略（两套接口自动回退，覆盖最广）：

  路径 1 —— OpenAI 事实标准 dashboard billing（兼容面最广，非 one-api 系也可能支持）：
    GET {v1}/dashboard/billing/subscription  → hard_limit_usd（总额度，美元）
    GET {v1}/dashboard/billing/usage         → total_usage（单位是美分，÷100 得美元）
    余额 = hard_limit_usd - total_usage/100
    两个子请求独立容错：subscription 拿不到 → 整条路径放弃；
    usage 拿不到 → 按已用 0 处理（余额 = 全额）。

  路径 2 —— NewAPI/OneAPI 原生接口（兜底，专攻 one-api 系）：
    GET {root}/api/user/self  → data.quota ÷ 500000 = 美元余额（used_quota 不纳入算式，仅留 raw 供排障）

  两路都失败才报错。货币统一标 USD（接口本就是美元口径，即使人民币充值也按美元 quota 计）。
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AdapterError, ProviderResult, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "OpenAI 兼容中转站"

# NewAPI/OneAPI 内部 quota 换算：1 美元 = 500000 quota
QUOTA_PER_USD = 500000.0


async def list_models(api_key: str, **config: Any) -> list:
    """中转站通常也兼容 /v1/models，复用统一解析。"""
    from .base import fetch_models_openai_compat
    base_url = _require_base_url(config)
    root, v1 = _normalize_base(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    return await fetch_models_openai_compat(f"{v1}/models", headers)


async def query(api_key: str, **config: Any) -> ProviderResult:
    raw: dict[str, Any] | None = None
    try:
        base_url = _require_base_url(config)
        root, v1 = _normalize_base(base_url)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

        # ---- 路径 1：dashboard billing（OpenAI 事实标准）----
        sub_raw = None
        try:
            sub_raw = await http_get(f"{v1}/dashboard/billing/subscription", headers)
        except AdapterError as e:
            log.debug("中转站 subscription 接口不可用，转 user/self: %s", e)

        if sub_raw is not None:
            hard_limit = _parse_hard_limit_usd(sub_raw)
            if hard_limit is not None:
                # subscription 拿到了，再尝试 usage（失败按已用 0）
                used_usd = 0.0
                usage_raw = None
                try:
                    usage_raw = await http_get(f"{v1}/dashboard/billing/usage", headers)
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

        # ---- 路径 2：/api/user/self（NewAPI/OneAPI 原生兜底）----
        self_raw = None
        try:
            self_raw = await http_get(f"{root}/api/user/self", headers)
        except AdapterError as e:
            log.warning("中转站 user/self 也失败: %s", e)
            raw = {"subscription": sub_raw} if sub_raw else None
            return _safe_result(
                "openai_proxy", DISPLAY_NAME, "balance",
                error=str(e), raw=raw,
            )

        balance = _parse_user_self(self_raw)
        if balance is not None:
            return _safe_result(
                "openai_proxy", DISPLAY_NAME, "balance",
                error=None, raw={"user_self": self_raw},
                balance=max(0.0, balance), currency="USD",
            )

        # 两条路径都拿到了响应，但都解析不出有效额度
        return _safe_result(
            "openai_proxy", DISPLAY_NAME, "balance",
            error="响应中未找到余额信息（subscription / user/self 均无有效字段）",
            raw=self_raw,
        )
    except AdapterError as e:
        log.warning("中转站查询失败: %s", e)
        return _safe_result("openai_proxy", DISPLAY_NAME, "balance", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("中转站查询异常")
        return _safe_result("openai_proxy", DISPLAY_NAME, "balance", error=f"未知错误: {e}", raw=raw)


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

def _require_base_url(config: dict[str, Any]) -> str:
    base_url = str(config.get("base_url") or "").strip()
    if not base_url:
        raise AdapterError("请填写中转站站点地址（base_url）")
    return base_url


def _normalize_base(base_url: str) -> tuple[str, str]:
    """规范化站点地址，返回 (root, v1_root)。

    兼容用户各种写法：
      https://x.com         → root=https://x.com,     v1=https://x.com/v1
      https://x.com/        → root=https://x.com,     v1=https://x.com/v1
      https://x.com/v1      → root=https://x.com,     v1=https://x.com/v1
      https://x.com/v1/     → root=https://x.com,     v1=https://x.com/v1

    v1 用于拼 /v1/dashboard/billing/* 和 /v1/models；
    root 用于拼 /api/user/self。
    """
    s = base_url.strip().rstrip("/")
    if not s:
        raise AdapterError("站点地址不能为空")
    if s.lower().endswith("/v1"):
        v1 = s
        root = s[:-3]  # 去掉末尾 /v1（保持原大小写）
    else:
        root = s
        v1 = f"{s}/v1"
    return root, v1


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
