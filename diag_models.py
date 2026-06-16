"""诊断脚本：用真实 key 测试各家动态模型列表拉取 + 合并静态能力。

用法：
  set DEEPSEEK_API_KEY=sk-xxx
  set GLM_API_KEY=xxx
  set KIMI_API_KEY=sk-xxx
  set MINIMAX_API_KEY=sk-xxx
  python diag_models.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import models_meta
from app.providers import registry


def _mask(key: str) -> str:
    if not key:
        return "<未设置>"
    return key[:4] + "***" + key[-4:]


async def test_one(name: str, provider: str, key: str):
    print(f"\n{'='*60}")
    print(f"【{name}】  key={_mask(key)}")
    print('='*60)
    if not key:
        print("⚠️  未设置环境变量，跳过（仅显示静态表）")
        static = models_meta.MODELS.get(provider, [])
        print(f"静态表 {len(static)} 个模型：{[m['id'] for m in static]}")
        return
    try:
        live = await registry.run_list_models(provider, key)
        print(f"✅ 动态拉取成功，共 {len(live)} 个模型：")
        for m in live:
            ctx = getattr(m, "context_length", None)
            ctx_str = f"{ctx//1000}K" if ctx else "-"
            print(f"   - {m.id:30s}  ctx={ctx_str:8s}  owned_by={getattr(m,'owned_by','-')}")
        merged = models_meta.merge_live_with_static(provider, live)
        known = sum(1 for m in merged if m.get("thinking") != "unknown")
        print(f"\n合并后 {len(merged)} 个模型（{known} 个有完整能力信息，{len(merged)-known} 个待补充）")
    except Exception as e:
        print(f"❌ 拉取失败: {e}")
        static = models_meta.MODELS.get(provider, [])
        print(f"   回退到静态表 {len(static)} 个：{[m['id'] for m in static]}")


async def main():
    keys = [
        ("DeepSeek", "deepseek", os.environ.get("DEEPSEEK_API_KEY", "")),
        ("GLM", "glm", os.environ.get("GLM_API_KEY", "")),
        ("Kimi", "kimi", os.environ.get("KIMI_API_KEY", "")),
        ("MiniMax", "minimax", os.environ.get("MINIMAX_API_KEY", "")),
    ]
    for name, provider, key in keys:
        await test_one(name, provider, key)
    print(f"\n{'='*60}")
    print("完成。")
    print('='*60)


if __name__ == "__main__":
    asyncio.run(main())
