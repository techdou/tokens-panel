"""adapter 注册表：provider key -> adapter 模块。

新增一家只需：1) 写 providers/xxx.py 实现 query()；2) 在此登记。
"""
from __future__ import annotations

from types import ModuleType
from typing import Any

from . import deepseek, glm, kimi, minimax, openai_proxy
from .base import ProviderResult

# provider key -> (adapter module, 展示名, 类型, 是否 window 型需要按桶渲染)
_REGISTRY: dict[str, dict[str, Any]] = {
    "deepseek": {"module": deepseek, "display_name": deepseek.DISPLAY_NAME, "type": "balance"},
    "glm": {"module": glm, "display_name": glm.DISPLAY_NAME, "type": "window"},
    "kimi": {"module": kimi, "display_name": kimi.DISPLAY_NAME, "type": "window"},
    "minimax": {"module": minimax, "display_name": minimax.DISPLAY_NAME, "type": "window"},
    "openai_proxy": {"module": openai_proxy, "display_name": openai_proxy.DISPLAY_NAME, "type": "balance"},
}


def list_providers() -> list[dict[str, Any]]:
    """给前端展示「可新增的 provider 列表」。"""
    return [
        {"provider": k, "display_name": v["display_name"], "type": v["type"]}
        for k, v in _REGISTRY.items()
    ]


def get_provider_meta(provider: str) -> dict[str, Any] | None:
    return _REGISTRY.get(provider)


async def run_query(provider: str, api_key: str, **config: Any) -> ProviderResult:
    meta = _REGISTRY.get(provider)
    if not meta:
        from .base import now_utc
        return ProviderResult(
            provider=provider, display_name=provider, type="balance",
            fetched_at=now_utc(), raw_error=f"未知的 provider: {provider}",
        )
    module: ModuleType = meta["module"]
    return await module.query(api_key, **config)


async def run_list_models(provider: str, api_key: str, **config: Any) -> list:
    """动态拉取某家的模型列表。provider 不支持时返回空列表。"""
    meta = _REGISTRY.get(provider)
    if not meta:
        return []
    module: ModuleType = meta["module"]
    fn = getattr(module, "list_models", None)
    if fn is None:
        return []
    return await fn(api_key, **config)
