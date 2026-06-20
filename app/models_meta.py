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
        # 联调确认（2026-06-15）：真实 /models 返回 v4-flash / v4-pro
        {
            "id": "deepseek-v4-flash",
            "name": "DeepSeek V4 Flash",
            "context": 64000,
            "max_output": 8192,
            "thinking": "unsupported",
            "thinking_param": None,
            "notes": "轻量快速版。能力参数待官方文档确认。",
            "source": "live /models",
        },
        {
            "id": "deepseek-v4-pro",
            "name": "DeepSeek V4 Pro",
            "context": 64000,
            "max_output": 32768,
            "thinking": "default_on",
            "thinking_param": None,
            "notes": "推理增强版（思考默认开启）。能力参数待官方文档确认。",
            "source": "live /models",
        },
    ],
    "glm": [
        # 联调确认（2026-06-15）：真实 /models 返回 glm-4.5 ~ glm-5.2 共 8 个
        # 但 context_length 未返回，下面能力参数来自历史文档，新模型（glm-5.x）标 unknown
        {
            "id": "glm-4.6",
            "name": "GLM-4.6",
            "context": 200000,
            "max_output": 128000,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled", "summary": true}',
            "notes": "200K 上下文。思考可选开关，summary 控制是否返回思考摘要。",
            "source": "https://docs.bigmodel.cn/cn/coding-plan/faq",
        },
        {
            "id": "glm-4.5",
            "name": "GLM-4.5",
            "context": 128000,
            "max_output": 4096,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled"}',
            "notes": "128K 上下文。",
            "source": "https://docs.bigmodel.cn/cn/coding-plan/faq",
        },
        # glm-4.5-air / glm-4.7 / glm-5 / glm-5-turbo / glm-5.1 / glm-5.2
        # → 不在静态表，靠动态拉取时自动出现并标 unknown
    ],
    "kimi": [
        # 联调确认（2026-06-15）：真实 /models 只返回 kimi-for-coding，ctx=262K
        {
            "id": "kimi-for-coding",
            "name": "Kimi for Coding",
            "context": 262144,
            "max_output": None,
            "thinking": "supported",
            "thinking_param": '"thinking": {"type": "enabled"}',
            "notes": "联调实测 ctx=262K（API 实时返回）。思考可选。",
            "source": "live /models",
        },
    ],
    "minimax": [
        # 联调确认（2026-06-15）：真实 /models 返回 M2/M2.1/M2.5/M2.7/M3 共 8 个
        # context_length 未返回
        {
            "id": "MiniMax-M3",
            "name": "MiniMax M3（最新）",
            "context": 1048576,
            "max_output": None,
            "thinking": "supported_param_unknown",
            "thinking_param": None,
            "notes": "最新旗舰。1M 上下文为家族规格，思考参数厂商仍在调整，建议查最新文档。",
            "source": "live /models",
        },
        {
            "id": "MiniMax-M2",
            "name": "MiniMax M2",
            "context": 1048576,
            "max_output": None,
            "thinking": "supported_param_unknown",
            "thinking_param": None,
            "notes": "1M 上下文。思考参数厂商仍在调整。",
            "source": "live /models",
        },
        # M2.1/M2.5/M2.7 及 highspeed 变体 → 动态拉取时自动出现标 unknown
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
