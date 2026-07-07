"""模型能力元数据处理（纯动态）。

⚠️ 本模块不内置静态模型表。所有模型信息来自各账户的 /v1/models 实时拉取。
   /v1/models 只能返回「模型 id」和「context 长度」等基础字段，
   「思考模式 / 最大输出 / 思考参数」等能力信息接口拿不到。

设计原则（纯展示优化，不补数据）：
  - 接口有数据（如 Kimi 的 context_length）→ 正常展示并高亮
  - 接口无数据（思考模式、多数家的 context）→ 不显示「未知」占位，
    改为提供该家官方文档链接，把「未知」变成「可查证」
  - 每个 provider 映射一个文档入口 URL，前端生成「查文档」按钮
"""
from __future__ import annotations

# 各家官方文档入口（模型能力/定价页）。用户点「查文档」跳转这里核实能力。
PROVIDER_DOC_URLS: dict[str, str] = {
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
    "glm": "https://docs.bigmodel.cn/cn/coding-plan/faq",
    "kimi": "https://platform.kimi.com/docs/api/chat",
    "minimax": "https://platform.minimaxi.com/docs/guides/pricing-paygo",
    "openai_proxy": "",  # 中转站地址不固定，不提供
}


def list_models(provider: str | None = None) -> dict:
    """返回空的能力表占位（保留接口兼容，前端模型 tab 不再依赖此端点）。

    历史上这里返回写死的静态表，现已改为纯动态拉取。保留函数仅为不破坏
    GET /api/models 端点签名；模型 tab 改为遍历账户调 /api/accounts/{id}/models。
    """
    return {"last_updated": "", "models": {}}


def format_context(n: int | None) -> str:
    """1000 -> '1K', 131072 -> '128K', 1048576 -> '1M', None -> ''（空，前端用 — 占位）"""
    if n is None:
        return ""
    if n >= 1_000_000:
        return f"{n/1_000_000:.0f}M"
    if n >= 1000:
        return f"{n/1000:.0f}K"
    return str(n)


def doc_url_for(provider: str) -> str:
    """返回某 provider 的官方文档 URL（无则空串）。"""
    return PROVIDER_DOC_URLS.get(provider, "")


def normalize_live_models(live_models: list, provider: str = "", doc_url_override: str = "") -> list[dict]:
    """把 /v1/models 实时拉取的模型列表转成前端展示结构。

    策略：
      - 直接透传接口返回的字段（id / context_length / owned_by）
      - name 优先取 description 或 display_name，否则回退到 id
      - doc_url 优先用用户在账户配置里填的自定义文档地址，
        没填则回退到该 provider 内置的官方文档 URL
      - 同 provider 内按 id 小写去重（兼容厂商偶发重复返回）
    """
    # 用户自定义文档地址优先于内置映射；空白视为未填，回退内置
    _override = (doc_url_override or "").strip()
    resolved_doc_url = _override if _override else doc_url_for(provider)

    seen_ids: set[str] = set()
    out: list[dict] = []

    for live in live_models:
        # 兼容 LiveModel（pydantic 对象）和 dict 两种输入
        if isinstance(live, dict):
            lid = str(live.get("id") or "")
            ctx = live.get("context_length") or live.get("max_context_length")
            owned_by = live.get("owned_by")
            desc = live.get("description") or live.get("display_name")
        else:
            lid = str(getattr(live, "id", "") or "")
            ctx = getattr(live, "context_length", None)
            owned_by = getattr(live, "owned_by", None)
            desc = getattr(live, "description", None) or getattr(live, "display_name", None)

        if not lid or lid.lower() in seen_ids:
            continue
        seen_ids.add(lid.lower())

        out.append({
            "id": lid,
            "name": desc or lid,
            "context": ctx,                # 接口返回才有，否则 None
            "has_context": ctx is not None,  # 前端据此决定高亮还是低调
            "doc_url": resolved_doc_url,    # 查官方文档链接（用户自定义优先）
            "source": "live",
            "owned_by": owned_by,
        })

    return out
