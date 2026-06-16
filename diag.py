"""诊断脚本：用真实 API Key 测试 4 家 adapter，打印原始响应 + 解析结果。

用法：
  set DEEPSEEK_API_KEY=sk-xxx
  set GLM_API_KEY=xxx
  set KIMI_API_KEY=sk-xxx
  set MINIMAX_API_KEY=xxx
  python diag.py

安全：API Key 只从环境变量读，不会打印也不会上传。
打印的「原始响应」里若意外含 key，请粘贴前手动脱敏。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.providers import deepseek, glm, kimi, minimax
from app.providers.base import ProviderResult


def _mask(key: str) -> str:
    if not key:
        return "<未设置>"
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


async def test_one(name: str, module, key: str):
    print(f"\n{'='*60}")
    print(f"【{name}】  key={_mask(key)}")
    print('='*60)
    if not key:
        print("⚠️  未设置环境变量，跳过")
        return
    result = await module.query(key)
    print("\n--- 解析结果 ---")
    print(f"type        : {result.type}")
    print(f"display_name: {result.display_name}")
    print(f"raw_error   : {result.raw_error}")
    if result.type == "balance":
        print(f"balance     : {result.balance} {result.currency}")
    if result.tiers:
        for i, t in enumerate(result.tiers):
            print(f"  tier[{i}] {t.type.value}: 已用 {t.used_percent:.2f}% / 剩余 {t.remaining_percent:.2f}%  重置={t.resets_at}")
    print("\n--- 原始响应（用于排查 parser 问题）---")
    raw_str = json.dumps(result.raw_response, ensure_ascii=False, indent=2) if result.raw_response else "<无>"
    # 脱敏：如果原始响应里意外包含 key 字样，遮蔽
    if key and key in raw_str:
        raw_str = raw_str.replace(key, "***MASKED***")
    print(raw_str)


async def main():
    keys = {
        "DeepSeek": (deepseek, os.environ.get("DEEPSEEK_API_KEY", "")),
        "GLM":      (glm,      os.environ.get("GLM_API_KEY", "")),
        "Kimi":     (kimi,     os.environ.get("KIMI_API_KEY", "")),
        "MiniMax":  (minimax,  os.environ.get("MINIMAX_API_KEY", "")),
    }
    print("将测试以下 4 家（key 已脱敏）：")
    for name, (_, key) in keys.items():
        print(f"  {name:10s}: {_mask(key)}")

    for name, (module, key) in keys.items():
        await test_one(name, module, key)

    print(f"\n{'='*60}")
    print("完成。把以上输出贴给我（原始响应部分），我会据此修 parser。")
    print("如果某家 raw_error 显示 401/403，说明 key 不对或没权限。")
    print('='*60)


if __name__ == "__main__":
    asyncio.run(main())
