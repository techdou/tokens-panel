"""DeepSeek 余额查询。

文档：https://api-docs.deepseek.com/zh-cn/api/get-user-balance
GET https://api.deepseek.com/user/balance
Authorization: Bearer {key}
返回 balance_infos[]，每项含 currency(CNY/USD) + total_balance（字符串）。

属于「余额型」。
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AdapterError, ProviderResult, _safe_result, http_get

log = logging.getLogger(__name__)

DISPLAY_NAME = "DeepSeek"
ENDPOINT = "https://api.deepseek.com/user/balance"
MODELS_ENDPOINT = "https://api.deepseek.com/models"


async def list_models(api_key: str, **_config: Any) -> list:
    """拉取 DeepSeek 当前可用模型列表（OpenAI 兼容 /models）。"""
    from .base import fetch_models_openai_compat
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    return await fetch_models_openai_compat(MODELS_ENDPOINT, headers)


async def query(api_key: str, **_config: Any) -> ProviderResult:
    raw: dict[str, Any] | None = None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        raw = await http_get(ENDPOINT, headers)
        return _parse(raw)
    except AdapterError as e:
        log.warning("DeepSeek 查询失败: %s", e)
        return _safe_result("deepseek", DISPLAY_NAME, "balance", error=str(e), raw=raw)
    except Exception as e:  # noqa: BLE001
        log.exception("DeepSeek 查询异常")
        return _safe_result("deepseek", DISPLAY_NAME, "balance", error=f"未知错误: {e}", raw=raw)


def _parse(raw: dict[str, Any]) -> ProviderResult:
    """解析响应。可能有多个币种的余额项，合并展示。"""
    infos = raw.get("balance_infos") or []
    if not infos:
        # 余额为空也返回 0，不当作错误（账户确实可能没充值）
        return _safe_result(
            "deepseek", DISPLAY_NAME, "balance",
            error=None, raw=raw, balance=0.0, currency="CNY",
        )

    # 优先取人民币，否则取第一项
    picked = next((i for i in infos if (i.get("currency") or "").upper() == "CNY"), infos[0])
    currency = (picked.get("currency") or "CNY").upper()
    try:
        total = float(picked.get("total_balance", 0))
    except (TypeError, ValueError):
        total = 0.0

    return _safe_result(
        "deepseek", DISPLAY_NAME, "balance",
        error=None, raw=raw, balance=total, currency=currency,
    )
