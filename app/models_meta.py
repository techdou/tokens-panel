"""各家服务商的模型能力元数据（静态表）。

⚠️ 重要：这些数据会随厂商更新而过时。请定期对照官方文档核实。
更新日期见 LAST_UPDATED。每个模型条目标注 source（官方文档链接）。

数据结构：
  provider -> [model_entry]
  model_entry: {
    id:           模型 ID（API 调用时用的 model 参数值）
    name:         展示名
    context:      上下文窗口（tokens）
    max_output:   最大输出长度（tokens，None 表示未公开）
    thinking:     是否支持思考模式（"supported" / "unsupported" / "default_on"）
    thinking_param: 如何开启思考的参数示例（None 表示不支持）
    notes:        备注（特殊调用注意点）
    source:       数据来源 URL
  }

上下文长度的常识：1K ≈ 750 英文单词 ≈ 500 汉字。
"""
from __future__ import annotations

LAST_UPDATED = "2026-06-15"

# 每家的模型列表。只收录 coding plan 常用 / 主推模型，避免信息过载。
MODELS: dict[str, list[dict]] = {
    "deepseek": [
        {
            "id": "deepseek-chat",
            "name": "DeepSeek-V3（对话）",
            "context": 64000,
            "max_output": 8192,
            "thinking": "unsupported",
            "thinking_param": None,
            "notes": "通用对话模型，速度快、便宜。不支持思考。",
            "source": "https://api-docs.deepseek.com/quick_start/pricing",
        },
        {
            "id": "deepseek-reasoner",
            "name": "DeepSeek-R1（推理）",
            "context": 64000,
            "max_output": 32768,
            "thinking": "default_on",
            "thinking_param": None,
            "notes": "推理模型，思考默认开启且无法关闭。响应更慢但更准。",
            "source": "https://api-docs.deepseek.com/guides/reasoning_model",
        },
    ],
    "glm": [
        {
            "id": "glm-4.6",
            "name": "GLM-4.6",
            "context": 200000,
            "max_output": 128000,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled", "summary": true}',
            "notes": "智谱当前主推。200K 上下文。思考可选开关，summary 控制是否返回思考摘要。",
            "source": "https://docs.bigmodel.cn/cn/coding-plan/faq",
        },
        {
            "id": "glm-4.5",
            "name": "GLM-4.5",
            "context": 128000,
            "max_output": 4096,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled"}',
            "notes": "上一代旗舰，128K 上下文。",
            "source": "https://docs.bigmodel.cn/cn/coding-plan/faq",
        },
    ],
    "kimi": [
        {
            "id": "kimi-k2",
            "name": "Kimi K2",
            "context": 131072,
            "max_output": None,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled"}',
            "notes": "Moonshot 旗舰，128K 上下文。思考可选。长文本能力强。",
            "source": "https://platform.kimi.com/docs/api/chat",
        },
        {
            "id": "kimi-k2-thinking",
            "name": "Kimi K2 Thinking",
            "context": 131072,
            "max_output": None,
            "thinking": "default_on",
            "thinking_param": None,
            "notes": "推理增强版，思考默认开启。",
            "source": "https://platform.kimi.com/docs/api/chat",
        },
    ],
    "minimax": [
        {
            "id": "MiniMax-M2",
            "name": "MiniMax M2",
            "context": 1048576,
            "max_output": None,
            "thinking": "supported",
            "thinking_param": '"stream_mode": " augmentation"',
            "notes": "1M（百万级）上下文是亮点。思考通过特定参数开启（具体名厂商仍在调整，建议核实）。",
            "source": "https://platform.minimaxi.com/docs/guides/pricing-paygo",
        },
    ],
}


def list_models(provider: str | None = None) -> dict:
    """返回模型能力表。provider 为空返回全部。"""
    if provider and provider in MODELS:
        return {"last_updated": LAST_UPDATED, "models": {provider: MODELS[provider]}}
    return {"last_updated": LAST_UPDATED, "models": MODELS}


def format_context(n: int | None) -> str:
    """1000 -> '1K', 131072 -> '128K', 1048576 -> '1M'"""
    if n is None:
        return "未公开"
    if n >= 1_000_000:
        return f"{n/1_000_000:.0f}M"
    if n >= 1000:
        return f"{n/1000:.0f}K"
    return str(n)


def merge_live_with_static(provider: str, live_models: list) -> list[dict]:
    """把动态拉取的模型列表与静态能力表合并。

    策略：
      - 动态列表为主（保证不漏新模型）
      - 静态表的能力（context/thinking/notes）按 id 匹配补充
      - 动态返回了 context_length 的（如 GLM）优先用动态值
      - 静态表里有、但动态没返回的模型 → 仍保留（可能该 key 无权限，但模型存在）
      - 动态有、静态没有的 → 标记为"未知（待补充）"
    """
    # 静态表按 id 索引（兼容大小写）
    static_by_id = {}
    for m in MODELS.get(provider, []):
        static_by_id[m["id"].lower()] = m

    seen_ids = set()
    merged: list[dict] = []

    for live in live_models:
        lid = str(getattr(live, "id", live.get("id") if isinstance(live, dict) else ""))
        if not lid or lid.lower() in seen_ids:
            continue
        seen_ids.add(lid.lower())

        live_ctx = getattr(live, "context_length", None) or (live.get("context_length") if isinstance(live, dict) else None)
        static = static_by_id.get(lid.lower(), {})

        merged.append({
            "id": lid,
            "name": static.get("name", getattr(live, "description", None) or lid),
            "context": live_ctx or static.get("context"),  # 动态优先
            "max_output": static.get("max_output"),
            "thinking": static.get("thinking", "unknown"),
            "thinking_param": static.get("thinking_param"),
            "notes": static.get("notes", "未知（待补充）"),
            "source": "live",
            "owned_by": getattr(live, "owned_by", None) or (live.get("owned_by") if isinstance(live, dict) else None),
        })

    # 静态表里有、但动态没返回的（兜底，避免 key 无权限时模型消失）
    for sid, sm in static_by_id.items():
        if sid not in seen_ids:
            entry = dict(sm)
            entry["source"] = "static"
            merged.append(entry)

    return merged
