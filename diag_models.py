"""诊断脚本：用真实 key 测试各家动态模型列表拉取（纯动态，无静态表）。

用法：
  set DEEPSEEK_API_KEY=sk-xxx
  set GLM_API_KEY=xxx
  set KIMI_API_KEY=sk-xxx
  set MINIMAX_API_KEY=sk-xxx
  python diag_models.py

也可测试自定义 API（OpenAI/Anthropic 兼容）：
  set CUSTOM_BASE_URL=https://api.your-relay.com
  set CUSTOM_API_KEY=sk-xxx
  set CUSTOM_API_FORMAT=openai   （或 anthropic）
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


async def test_builtin(name: str, provider: str, key: str):
    print(f"\n{'='*60}")
    print(f"【{name}】  key={_mask(key)}")
    print('=' * 60)
    if not key:
        print("⚠️  未设置环境变量，跳过")
        return
    await _probe(provider, key)


async def test_custom():
    base = os.environ.get("CUSTOM_BASE_URL", "").strip()
    key = os.environ.get("CUSTOM_API_KEY", "").strip()
    fmt = os.environ.get("CUSTOM_API_FORMAT", "openai").strip().lower()
    if not base or not key:
        return
    print(f"\n{'='*60}")
    print(f"【自定义 API · {fmt}】  base={base}  key={_mask(key)}")
    print('=' * 60)
    await _probe("openai_proxy", key, base_url=base, api_format=fmt)


async def _probe(provider: str, key: str, **config):
    try:
        live = await registry.run_list_models(provider, key, **config)
        print(f"✅ 动态拉取成功，共 {len(live)} 个模型：")
        for m in live:
            ctx = getattr(m, "context_length", None)
            ctx_str = f"{ctx // 1000}K" if ctx else "-"
            print(f"   - {m.id:30s}  ctx={ctx_str:8s}  owned_by={getattr(m, 'owned_by', '-')}")
        normalized = models_meta.normalize_live_models(live)
        with_ctx = sum(1 for m in normalized if m["context"])
        print(f"\nnormalize 后 {len(normalized)} 个（{with_ctx} 个有上下文，{len(normalized) - with_ctx} 个上下文未公开）")
    except Exception as e:  # noqa: BLE001
        print(f"❌ 拉取失败: {e}")


async def main():
    await test_builtin("DeepSeek", "deepseek", os.environ.get("DEEPSEEK_API_KEY", ""))
    await test_builtin("GLM", "glm", os.environ.get("GLM_API_KEY", ""))
    await test_builtin("Kimi", "kimi", os.environ.get("KIMI_API_KEY", ""))
    await test_builtin("MiniMax", "minimax", os.environ.get("MINIMAX_API_KEY", ""))
    await test_custom()
    print(f"\n{'='*60}\n完成。\n{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
