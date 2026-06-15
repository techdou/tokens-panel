# Token 余额聚合面板

一个自用的 Web 面板，聚合查看多家国产大模型 **API 会员** 的额度用量。

支持 DeepSeek（余额型）、智谱 GLM / Kimi / MiniMax（Coding Plan 窗口型），统一看板、定时刷新、低额度告警、每日报告。

![type](https://img.shields.io/badge/Python-3.11-blue) ![deploy](https://img.shields.io/badge/deploy-Docker-success)

## ✨ 功能

- 📊 **统一面板**：4 家服务商额度一眼看完，不用挨个网站点
- 🔁 **定时刷新**：默认每 15 分钟自动拉取（可配）
- 📈 **趋势图表**：余额 / 用量历史曲线，最近 1~90 天可选
- 🔔 **低额度告警**：余额 ≤ 阈值 或 窗口已用 ≥ 阈值 时推送，每账户可单独配阈值
- 📅 **每日报告**：每天定时汇总各家状态到微信 / Telegram / 邮箱
- 🔒 **本地加密**：API Key 用 Fernet 加密存 SQLite，密码登录保护

## 🚀 快速部署（VPS + Docker）

### 1. 拉代码 + 配置

```bash
git clone <你的仓库地址> tokens
cd tokens
cp .env.example .env
```

编辑 `.env`，**至少改这两个**：
```ini
ADMIN_PASSWORD=你的强密码
MASTER_KEY=              # 留空！首次启动会自动生成并写回
```

### 2. 启动

```bash
docker compose up -d --build
```

打开 `http://你的VPS-IP:8000`，用密码登录。

### 3. 添加账户

在「设置」页填服务商 + 显示名 + API Key，保存。点「刷新」即可看到数据。

### 4.（可选）配置告警通知

在「设置 → 通知配置」里填 Server 酱 SendKey（去 https://sct.ftqq.com 免费申请），点「测试通知」确认能收到。

## 🔧 配置项（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_PASSWORD` | 必改 | 登录密码 |
| `MASTER_KEY` | 自动生成 | API Key 加密密钥，**生成后别换**，否则已存 key 解不开 |
| `PORT` | 8000 | 监听端口 |
| `REFRESH_INTERVAL_MINUTES` | 15 | 定时刷新间隔 |
| `DAILY_REPORT_TIME` | 09:00 | 每日报告时间 |
| `SESSION_TTL_HOURS` | 720 | 登录有效期（小时）|

## 📡 支持的服务商

| 服务商 | 类型 | 查询内容 |
|---|---|---|
| **DeepSeek** | 余额型 | 账户剩余金额（CNY/USD） |
| **智谱 GLM** Coding Plan | 窗口型 | 5 小时 / 每周 窗口已用% |
| **Kimi** for Coding | 窗口型 | 5 小时 / 每周 窗口已用% |
| **MiniMax** Coding Plan | 窗口型 | 5 小时 / 每周 窗口已用% |

> 注：GLM/Kimi/MiniMax 的用量查询接口属于厂商内部接口（非公开 API），通过逆向抓包得到。接口可能随厂商调整而失效，本项目做了容错处理，失效时卡片会标红但不影响其它家。

## 🌍 国际版账户

GLM / MiniMax 支持国际版（z.ai / minimax.io）。在账户配置 JSON 里加 `"region": "intl"`：
```json
{"region": "intl"}
```
（账户编辑时填入；UI 化的配置在后续版本完善）

## ➕ 如何新增一家服务商

1. 在 `app/providers/` 新建 `xxx.py`，实现 `query(api_key, **config) -> ProviderResult`，参考 `deepseek.py`（余额型）或 `glm.py`（窗口型）
2. 在 `app/providers/registry.py` 的 `_REGISTRY` 注册一行
3.（可选）在 `tests/test_providers.py` 加该家的 parser 单测
4. 重启服务，新服务商自动出现在设置页下拉里

每个 adapter 的认证细节、字段解析、已知坑都封装在文件内，互不影响。

## 🛠 本地开发

```bash
pip install -r requirements.txt

# 跑测试（不打真实 API）
python tests/test_smoke.py            # 加密 / DB / DeepSeek parser
python tests/test_providers.py        # GLM/Kimi/MiniMax parser
python tests/test_real_samples.py     # 联调真实响应回归
python tests/test_scheduler_history.py # 定时 / 历史
python tests/test_alerts.py           # 告警 / 每日报告

# 联调诊断（用你的真实 key）
set DEEPSEEK_API_KEY=sk-xxx
set GLM_API_KEY=xxx
python diag.py

# 启动开发服务器
python -m uvicorn app.main:app --reload --port 8000
```

## 📂 目录结构

```
tokens/
├── app/
│   ├── main.py              # FastAPI 入口、路由
│   ├── config.py            # 配置、环境变量
│   ├── db.py                # SQLite 数据层
│   ├── crypto.py            # API Key 加解密
│   ├── auth.py              # 登录、会话
│   ├── scheduler.py         # 定时刷新 + 每日报告
│   ├── alerts.py            # 告警判定
│   ├── notify.py            # 通知发送（Server酱/TG/邮件）
│   ├── providers/
│   │   ├── base.py          # 统一数据模型 + adapter 接口
│   │   ├── registry.py      # adapter 注册表
│   │   ├── deepseek.py
│   │   ├── glm.py
│   │   ├── kimi.py
│   │   └── minimax.py
│   └── templates/index.html # 单页面板（Alpine.js + Tailwind + ECharts）
├── tests/                   # 测试（60+ 用例）
├── diag.py                  # 联调诊断脚本
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 🔒 安全说明

- API Key 用 Fernet（AES128-CBC + HMAC）加密存储，主密钥在 `.env`
- 面板需密码登录，会话 cookie 由 itsdangerous 签名
- 建议在 VPS 上套 Caddy/Nginx + HTTPS 反代后再对外
- 容器以非 root 用户运行

## 📝 已知限制

- ChatGPT Plus / Claude Pro / Gemini Pro 等**网页包月会员**无法查询（无开放 API），本面板只覆盖 API Key 充值/订阅型
- GLM/Kimi 的用量接口是逆向所得，厂商改接口可能导致失效
- 单用户设计，不支持多账号体系

## 📜 License

MIT
