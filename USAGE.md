# Coding Plan 各家直接调用速查（OpenAI 兼容格式）

> 你的 coding plan key 本身就能当通用 API 用，无需任何中转。
> 下面每家都给 curl + Python(openai SDK) 两个例子。
> **注意**：coding plan 厂商条款要求"仅限 coding 场景"，自用低频没问题，别拿去跑高并发业务或转售。

## 1. 智谱 GLM Coding Plan

```bash
curl https://open.bigmodel.cn/api/coding/paas/v4/chat/completions \
  -H "Authorization: Bearer 你的GLM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm-4.6","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

```python
from openai import OpenAI
client = OpenAI(
    api_key="你的GLM_KEY",
    base_url="https://open.bigmodel.cn/api/coding/paas/v4/"
)
resp = client.chat.completions.create(
    model="glm-4.6",
    messages=[{"role":"user","content":"hi"}],
)
```

## 2. Kimi for Coding

```bash
curl https://api.kimi.com/coding/v1/chat/completions \
  -H "Authorization: Bearer 你的KIMI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"kimi-k2","messages":[{"role":"user","content":"hi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(
    api_key="你的KIMI_KEY",
    base_url="https://api.kimi.com/coding/v1/"
)
```

## 3. MiniMax Coding Plan

```bash
curl https://api.minimaxi.com/v1/chat/completions \
  -H "Authorization: Bearer 你的MINIMAX_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"MiniMax-M2","messages":[{"role":"user","content":"hi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(
    api_key="你的MINIMAX_KEY",
    base_url="https://api.minimaxi.com/v1/"
)
```

## 4. DeepSeek

```bash
curl https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer 你的DEEPSEEK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hi"}]}'
```

```python
from openai import OpenAI
client = OpenAI(
    api_key="你的DEEPSEEK_KEY",
    base_url="https://api.deepseek.com"
)
```

## 切换不同家的小技巧

不想每次改代码里的 base_url？用一个工厂函数：

```python
from openai import OpenAI

PROVIDERS = {
    "glm":     ("你的GLM_KEY",  "https://open.bigmodel.cn/api/coding/paas/v4/"),
    "kimi":    ("你的KIMI_KEY", "https://api.kimi.com/coding/v1/"),
    "minimax": ("你的MM_KEY",   "https://api.minimaxi.com/v1/"),
    "deepseek":("你的DS_KEY",   "https://api.deepseek.com"),
}

def client_for(name: str) -> OpenAI:
    key, base = PROVIDERS[name]
    return OpenAI(api_key=key, base_url=base)

# 用哪家就传哪家
resp = client_for("glm").chat.completions.create(
    model="glm-4.6",
    messages=[{"role":"user","content":"hi"}],
)
```

## 在 Cursor / Claude Code 里用

这些工具都支持自定义 OpenAI 兼容端点，base_url 填上面的，key 填你的 plan key 即可。
配多套环境变量，要切就改 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 两个变量。

## 何时才真的需要中转？

只有当出现以下任一情况，才值得考虑建中转（届时用 new-api，别自己写）：
- 要给 2 人以上共用，需要计费/限流/多 key
- 要做故障转移（一家挂自动切另一家）
- 要统一日志和审计

**自用 + 只要调通 = 直接用上面的端点，别建中转。**

---

# 进阶：思考模式（Thinking）怎么用

各家"思考模型"的开关方式**不一致**，这是最容易踩坑的地方。下面是实测可用的写法。

## DeepSeek-R1（思考默认开启，无法关闭）

```python
resp = client_for("deepseek").chat.completions.create(
    model="deepseek-reasoner",       # 用这个 model 就是思考模式
    messages=[{"role":"user","content":"证明根号2是无理数"}],
)
# 思考过程在 resp.choices[0].message.reasoning_content
# 最终答案在 resp.choices[0].message.content
print(resp.choices[0].message.reasoning_content)  # 思考链
print(resp.choices[0].message.content)            # 正式回答
```

> 要**关闭**思考？换成 `deepseek-chat` 即可，它不支持思考。

## GLM-4.6（思考可选，需显式开启）

```python
resp = client_for("glm").chat.completions.create(
    model="glm-4.6",
    messages=[{"role":"user","content":"证明根号2是无理数"}],
    extra_body={
        "thinking": {"type": "enabled", "summary": True}
        # type: "enabled" 开 / "disabled" 关
        # summary: True 返回思考摘要 / False 只返回最终答案
    },
)
```

## Kimi K2（思考可选）

```python
resp = client_for("kimi").chat.completions.create(
    model="kimi-k2",
    messages=[{"role":"user","content":"证明根号2是无理数"}],
    extra_body={
        "thinking": {"type": "enabled"}
    },
)
# 或直接用 kimi-k2-thinking 模型名（默认开启，无需参数）
```

## ⚠️ 思考模式的三个通用注意点

1. **流式输出时**：思考内容会先于正式答案以独立 chunk 流出，要分别处理
2. **耗时与计费**：思考过程会消耗 token（有时比答案还多），别对所有问题都开
3. **OpenAI SDK 兼容性**：`extra_body` 是官方 SDK 传非标准参数的方式，各家都接受

---

# 进阶：上下文长度怎么处理

各家上下文窗口差异很大（DeepSeek 64K / GLM 200K / Kimi 128K / MiniMax 1M）。超了会报错或被截断。

## 各家上下文上限（详见面板「模型」页）

| 家 | 主力模型 | 上下文 |
|---|---|---|
| DeepSeek | deepseek-chat / reasoner | 64K |
| GLM | glm-4.6 | 200K |
| Kimi | kimi-k2 | 128K |
| MiniMax | MiniMax-M2 | **1M** |

## 处理超长输入的三种策略

```python
# 策略 1：估算 token 数，超了就截断最早的对话（最简单）
def trim_messages(messages, max_tokens=60000):
    """粗暴按字符数估算（1 汉字≈1.5 token，1 英文单词≈1.3 token）。"""
    total = sum(len(m["content"]) for m in messages) * 1.5
    while total > max_tokens and len(messages) > 2:
        # 删掉最早的非系统消息
        removed = messages.pop(1 if messages[0]["role"]=="system" else 0)
        total -= len(removed["content"]) * 1.5
    return messages

# 策略 2：用 tiktoken 精确计数（需 pip install tiktoken，仅对 OpenAI 系准确）
import tiktoken
enc = tiktoken.get_cl100k_base()
def count_tokens(messages):
    return sum(len(enc.encode(m["content"])) for m in messages)

# 策略 3：长文档用 RAG（检索），而不是硬塞进上下文
# 适合：文档 > 上下文上限时。用 embedding + 向量库，只取相关段落。
```

## 选哪家？

- **日常对话 / 短任务** → DeepSeek（便宜快）或 GLM（均衡）
- **长文档分析 / 大代码库** → MiniMax（1M 上下文，能塞下整本书）
- **复杂推理 / 数学 / 多步规划** → DeepSeek-R1 或开 thinking 的 GLM-4.6
- **长文本 + 推理** → Kimi K2 Thinking（128K + 思考）

> 💡 在面板的「模型」页能直接对比这些能力，不用记。

