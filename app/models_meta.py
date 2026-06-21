"""模型能力元数据处理（纯动态）。

⚠️ 本模块不再内置任何静态模型表。所有模型信息来自各账户的 /v1/models 实时拉取。
   /v1/models 只能返回「模型 id」和「context 长度」等基础字段，
   「思考模式 / 最大输出 / 思考参数」等能力信息接口拿不到，统一显示「未知」。

展示字段（前端用）：
  id:           模型 ID（API 调用时用的 model 参数值）
  name:         展示名（优先用接口返回的 description/display_name，否则回退到 id）
  context:      上下文窗口（tokens），接口没返回则为 None → 前端显示「未公开」
  max_output:   最大输出长度（恒为 None，接口拿不到）
  thinking:     思考模式支持（恒为 "unknown"）
  thinking_param: 思考参数示例（恒为 None）
  notes:        备注（恒为「未知（待补充）」）
  source:       数据来源（恒为 "live"）
  owned_by:     归属方（接口返回才有）

上下文长度的常识：1K ≈ 750 英文单词 ≈ 500 汉字。
"""
from __future__ import annotations


def list_models(provider: str | None = None) -> dict:
    """返回空的能力表占位（保留接口兼容，前端模型 tab 不再依赖此端点）。

    历史上这里返回写死的静态表，现已改为纯动态拉取。保留函数仅为不破坏
    GET /api/models 端点签名；模型 tab 改为遍历账户调 /api/accounts/{id}/models。
    """
    return {"last_updated": "", "models": {}}


def format_context(n: int | None) -> str:
    """1000 -> '1K', 131072 -> '128K', 1048576 -> '1M', None -> '未公开'"""
    if n is None:
        return "未公开"
    if n >= 1_000_000:
        return f"{n/1_000_000:.0f}M"
    if n >= 1000:
        return f"{n/1000:.0f}K"
    return str(n)


def normalize_live_models(live_models: list) -> list[dict]:
    """把 /v1/models 实时拉取的模型列表转成前端展示结构。

    策略：
      - 直接透传接口返回的字段（id / context_length / owned_by）
      - name 优先取 description 或 display_name，否则回退到 id
      - 能力字段（thinking / max_output / thinking_param / notes）接口拿不到，
        统一填「未知」/None，前端据此显示「未知」
      - 同 provider 内按 id 小写去重（兼容厂商偶发重复返回）
    """
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
            "context": ctx,
            "max_output": None,                # 接口拿不到
            "thinking": "unknown",             # 接口拿不到
            "thinking_param": None,            # 接口拿不到
            "notes": "未知（待补充）",          # 接口拿不到
            "source": "live",
            "owned_by": owned_by,
        })

    return out
