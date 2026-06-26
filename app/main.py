"""FastAPI 入口。

路由分层：
  GET  /                  -> 前端单页（未登录显示登录页，登录后显示面板）
  POST /api/login         -> 登录
  POST /api/logout        -> 登出
  GET  /api/accounts      -> 列出所有 account（含最新快照）
  POST /api/accounts      -> 新增 account
  PATCH /api/accounts/{id}-> 改 account
  DELETE /api/accounts/{id}-> 删 account
  POST /api/refresh       -> 手动刷新所有 / 单个 account
  GET  /api/providers     -> 可用 provider 列表
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import auth, config, crypto, db, notify
from .providers import registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("tokens")

app = FastAPI(title="Token 余额聚合面板", docs_url=None, redoc_url=None)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 挂载静态文件（app.js 等）。html=True 让 /static/ 也走缓存友好头
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log.info("DB 初始化完成: %s", config.DB_PATH)
    # 阶段3 再挂 APScheduler；这里先留接口
    try:
        from . import scheduler  # noqa: F401
        scheduler.start()
    except Exception as e:  # noqa: BLE001
        log.warning("调度器未启动（阶段3功能）: %s", e)


# ---------------- 鉴权依赖 ----------------

def require_login(request: Request) -> None:
    if not auth.is_logged_in(request):
        raise HTTPException(status_code=401, detail="未登录")


# ---------------- 页面 ----------------

@app.get("/")
def index(request: Request):
    # 单页应用：同一个 HTML，前端根据登录态切换
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------- 登录 / 登出 ----------------

async def _maybe_json_password(request: Request) -> str:
    """登录参数来源：优先 form，其次 JSON body，最后查询参数。"""
    ct = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        form = await request.form()
        return (form.get("password") or "").strip()
    if "application/json" in ct:
        try:
            body = await request.json()
            if isinstance(body, dict):
                return str(body.get("password") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    return str(request.query_params.get("password") or "").strip()


@app.post("/api/login")
async def login(request: Request):
    password = await _maybe_json_password(request)
    if not auth.verify_password(password):
        raise HTTPException(status_code=401, detail="密码错误")
    token = auth.create_session_cookie()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "session", token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True, samesite="lax",
        secure=False,  # VPS 若套 HTTPS 反代，建议改 true
    )
    log.info("管理员登录，IP=%s", request.client.host if request.client else "?")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.get("/api/session")
def session_status(request: Request):
    return {"logged_in": auth.is_logged_in(request)}


# ---------------- accounts ----------------

class AccountCreate(BaseModel):
    provider: str
    display_name: str
    api_key: str
    config: dict[str, Any] | None = None


class AccountUpdate(BaseModel):
    display_name: str | None = None
    api_key: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


def _account_with_snapshot(acc: dict[str, Any]) -> dict[str, Any]:
    snap = db.latest_snapshot(acc["id"])
    return {
        "id": acc["id"],
        "provider": acc["provider"],
        "display_name": acc["display_name"],
        "enabled": bool(acc["enabled"]),
        "config": __import__("json").loads(acc["config_json"] or "{}"),
        "created_at": acc["created_at"],
        "latest": snap,
    }


@app.get("/api/accounts", dependencies=[Depends(require_login)])
def api_list_accounts():
    accounts = db.list_accounts()
    return {"accounts": [_account_with_snapshot(a) for a in accounts]}


@app.post("/api/accounts", dependencies=[Depends(require_login)])
def api_create_account(body: AccountCreate):
    if not registry.get_provider_meta(body.provider):
        raise HTTPException(status_code=400, detail=f"不支持的 provider: {body.provider}")
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key 不能为空")
    enc = crypto.encrypt(body.api_key.strip())
    aid = db.create_account(body.provider, body.display_name.strip(), enc, body.config)
    log.info("新增 account id=%s provider=%s", aid, body.provider)
    acc = db.get_account(aid)
    return _account_with_snapshot(acc)  # type: ignore[arg-type]


@app.patch("/api/accounts/{account_id}", dependencies=[Depends(require_login)])
def api_update_account(account_id: int, body: AccountUpdate):
    if not db.get_account(account_id):
        raise HTTPException(status_code=404, detail="account 不存在")
    enc = crypto.encrypt(body.api_key.strip()) if body.api_key is not None else None
    db.update_account(
        account_id,
        display_name=body.display_name,
        encrypted_api_key=enc,
        config_json=body.config,
        enabled=None if body.enabled is None else int(body.enabled),
    )
    acc = db.get_account(account_id)
    return _account_with_snapshot(acc)  # type: ignore[arg-type]


@app.delete("/api/accounts/{account_id}", dependencies=[Depends(require_login)])
def api_delete_account(account_id: int):
    if not db.delete_account(account_id):
        raise HTTPException(status_code=404, detail="account 不存在")
    return {"ok": True}


# ---------------- providers 元信息 ----------------

@app.get("/api/providers")
def api_providers():
    return {"providers": registry.list_providers()}


@app.get("/api/models")
def api_models(provider: str | None = None):
    """模型能力表占位端点（保留接口兼容）。

    模型表已改为纯动态：前端「模型」tab 遍历各账户调
    /api/accounts/{id}/models 实时拉取 /v1/models，不再依赖本端点。
    """
    from . import models_meta
    return models_meta.list_models(provider)


@app.get("/api/accounts/{account_id}/models", dependencies=[Depends(require_login)])
async def api_live_models(account_id: int):
    """用某账户的 key 动态拉取该家当前可用模型列表（纯实时，无静态补充）。"""
    import json as _json
    from . import models_meta
    acc = db.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="account 不存在")
    try:
        api_key = crypto.decrypt(acc["encrypted_api_key"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"key 解密失败: {e}")
    cfg = _json.loads(acc["config_json"] or "{}")
    try:
        live = await registry.run_list_models(acc["provider"], api_key, **cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("动态拉取模型失败 account=%s: %s", account_id, e)
        return {
            "account_id": account_id,
            "provider": acc["provider"],
            "display_name": acc["display_name"],
            "models": [],
            "live_error": str(e),
            "fetched_at": datetime.utcnow().isoformat(),
        }
    normalized = models_meta.normalize_live_models(live, acc["provider"])
    return {
        "account_id": account_id,
        "provider": acc["provider"],
        "display_name": acc["display_name"],
        "models": normalized,
        "live_count": len(live),
        "fetched_at": datetime.utcnow().isoformat(),
    }


# ---------------- 通知配置 ----------------

# 通知相关的 settings key 白名单（避免前端乱写）
_NOTIFY_KEYS = [
    "notify_serverchan_key",
    "notify_telegram_bot_token", "notify_telegram_chat_id",
    "notify_smtp_host", "notify_smtp_port", "notify_smtp_user",
    "notify_smtp_password", "notify_smtp_to",
    "alert_balance_threshold", "alert_used_threshold",
]


@app.get("/api/notify/config", dependencies=[Depends(require_login)])
def api_get_notify_config():
    settings = db.get_all_settings()
    # 只返回白名单内的，且敏感字段做存在性提示（不回显完整值）
    out = {}
    for k in _NOTIFY_KEYS:
        v = settings.get(k, "")
        if k in ("notify_serverchan_key", "notify_smtp_password", "notify_telegram_bot_token"):
            out[k] = {"set": bool(v), "masked": (v[:3] + "***") if v else ""}
        else:
            out[k] = v
    return out


class NotifyConfigUpdate(BaseModel):
    notify_serverchan_key: str | None = None
    notify_telegram_bot_token: str | None = None
    notify_telegram_chat_id: str | None = None
    notify_smtp_host: str | None = None
    notify_smtp_port: str | None = None
    notify_smtp_user: str | None = None
    notify_smtp_password: str | None = None
    notify_smtp_to: str | None = None
    alert_balance_threshold: str | None = None
    alert_used_threshold: str | None = None


@app.post("/api/notify/config", dependencies=[Depends(require_login)])
def api_set_notify_config(body: NotifyConfigUpdate):
    """更新通知配置。空字符串视为清空，None 视为不修改。"""
    data = body.model_dump(exclude_none=True)
    for k, v in data.items():
        if k in _NOTIFY_KEYS:
            db.set_setting(k, str(v))
    return {"ok": True}


@app.post("/api/notify/test", dependencies=[Depends(require_login)])
def api_notify_test():
    """发送一条测试通知，返回每个渠道结果。"""
    results = notify.send_test()
    return {"results": results, "any_ok": any(r.get("ok") for r in results.values())}


@app.get("/api/notify/logs", dependencies=[Depends(require_login)])
def api_notify_logs():
    return {"logs": db.list_notify_logs(limit=50)}


# ---------------- 刷新 ----------------

@app.post("/api/refresh", dependencies=[Depends(require_login)])
async def api_refresh(account_id: int | None = None):
    """刷新所有 enabled 的 account，或指定单个。返回最新结果。"""
    accounts = db.list_accounts()
    if account_id is not None:
        accounts = [a for a in accounts if a["id"] == account_id]
        if not accounts:
            raise HTTPException(status_code=404, detail="account 不存在")

    results = []
    for acc in accounts:
        if not acc["enabled"]:
            continue
        try:
            api_key = crypto.decrypt(acc["encrypted_api_key"])
        except Exception as e:  # noqa: BLE001
            log.error("解密 key 失败 account=%s: %s", acc["id"], e)
            continue
        cfg = {}
        try:
            cfg = __import__("json").loads(acc["config_json"] or "{}")
        except Exception:  # noqa: BLE001
            cfg = {}
        result = await registry.run_query(acc["provider"], api_key, **cfg)
        # 落库快照（成功失败都存，失败也有 fetched_at）
        db.add_snapshot(acc["id"], result.model_dump(mode="json"))
        results.append({"account_id": acc["id"], **result.model_dump(mode="json")})
        log.info("刷新 account=%s provider=%s ok=%s", acc["id"], acc["provider"], result.raw_error is None)

    return {"refreshed_at": datetime.utcnow().isoformat(), "results": results}


# ---------------- 历史 / 趋势 ----------------

@app.get("/api/accounts/{account_id}/history", dependencies=[Depends(require_login)])
def api_history(account_id: int, days: int = 7):
    """返回某账户最近 N 天的快照序列，供前端画趋势图。"""
    if not db.get_account(account_id):
        raise HTTPException(status_code=404, detail="account 不存在")
    days = max(1, min(90, days))  # 限制 1-90 天
    from datetime import timedelta
    since = datetime.now() - timedelta(days=days)
    snaps = db.snapshots_since(account_id, since)
    # 提取关键字段，减小 payload
    points = []
    for s in snaps:
        item = {"fetched_at": s.get("fetched_at")}
        if s.get("type") == "balance":
            item["balance"] = s.get("balance")
            item["currency"] = s.get("currency")
        elif s.get("tiers"):
            # 取每个桶的 used_percent
            for i, t in enumerate(s["tiers"]):
                item[f"{t['type']}_used"] = t.get("used_percent")
        item["error"] = s.get("raw_error")
        points.append(item)
    acc = db.get_account(account_id)
    return {
        "account": {
            "id": acc["id"],  # type: ignore[index]
            "display_name": acc["display_name"],  # type: ignore[index]
            "provider": acc["provider"],  # type: ignore[index]
            "type": registry.get_provider_meta(acc["provider"])["type"],  # type: ignore[index]
        },
        "points": points,
    }


@app.get("/api/history", dependencies=[Depends(require_login)])
def api_history_all(days: int = 7):
    """返回所有窗口型账户最近 N 天的已用百分比序列，供前端画热力图。

    响应结构（D3 stack 友好的宽格式，按时间桶对齐）：
      {
        "keys": ["我的GLM", "我的Kimi", ...],
        "series": [
          {"date": "2026-06-15 10:00", "我的GLM": 12.3, "我的Kimi": 5.0},
          ...
        ],
        "has_window_accounts": true   # 是否存在窗口型账户（区分「无此类账户」与「有但无快照」）
      }

    时间桶策略：把快照时间规约到「分钟」粒度对齐（去掉秒），同桶内取最新一条。
    避免各账户快照时间戳不完全一致时出现稀疏跳变/锯齿。
    余额型账户不纳入（量纲不同，由单账户折线图展示）。
    """
    days = max(1, min(90, days))
    from datetime import timedelta
    since = datetime.now() - timedelta(days=days)

    def _is_window_account(a: dict) -> bool:
        meta = registry.get_provider_meta(a["provider"])
        if not meta or not a.get("enabled"):
            return False
        # 内置 provider 直接看 registry type
        if a["provider"] != "openai_proxy":
            return meta.get("type") == "window"
        # 自定义 API：type 由账户 config.account_type 决定（默认 balance）
        import json as _json
        try:
            cfg = _json.loads(a.get("config_json") or "{}")
        except Exception:  # noqa: BLE001
            cfg = {}
        return str(cfg.get("account_type") or "balance").lower() == "window"

    window_accounts = [a for a in db.list_accounts() if _is_window_account(a)]
    keys = [a["display_name"] for a in window_accounts]
    if not window_accounts:
        return {"keys": [], "series": [], "has_window_accounts": False}

    # 每个账户：按「分钟桶」取每桶最新一条的 used_percent（5h/weekly 取最高）
    # bucket_key 格式 'YYYY-MM-DD HH:MM'（去秒）
    acc_series: dict[int, dict[str, float]] = {}
    bucket_set: set[str] = set()
    for a in window_accounts:
        snaps = db.snapshots_since(a["id"], since)
        per_bucket: dict[str, float] = {}
        for s in snaps:
            if s.get("raw_error"):
                continue
            tiers = s.get("tiers") or []
            if not tiers:
                continue
            t = s.get("fetched_at")
            if not t or len(t) < 16:
                continue
            bucket = t[:16]  # 'YYYY-MM-DD HH:MM'
            used = max((float(tier.get("used_percent") or 0) for tier in tiers), default=0.0)
            # 同桶内天然按写入顺序，snapshots_since 已按时间序，后写覆盖即「最新」
            per_bucket[bucket] = used
            bucket_set.add(bucket)
        acc_series[a["id"]] = per_bucket

    if not bucket_set:
        return {"keys": keys, "series": [], "has_window_accounts": True}

    # 宽格式：每个分钟桶一行，各账户一列；账户在该桶无数据 → 沿用其上一个桶的值（前向填充）
    # 前向填充避免「账户没在这个分钟被刷新」被误判为用量归零
    sorted_buckets = sorted(bucket_set)
    last_val: dict[int, float] = {a["id"]: 0.0 for a in window_accounts}
    series = []
    for b in sorted_buckets:
        row: dict[str, Any] = {"date": b}
        for a in window_accounts:
            v = acc_series.get(a["id"], {}).get(b)
            if v is None:
                v = last_val.get(a["id"], 0.0)  # 前向填充
            else:
                last_val[a["id"]] = v
            row[a["display_name"]] = v
        series.append(row)

    return {"keys": keys, "series": series, "has_window_accounts": True}


@app.get("/api/history/balance", dependencies=[Depends(require_login)])
def api_history_balance(days: int = 7):
    """返回所有余额型账户最近 N 天的余额序列，供前端画跨账户对比折线图。

    响应结构（echarts 多线友好）：
      {
        "keys": ["我的DeepSeek", "我的中转站", ...],
        "currencies": ["CNY", "USD"],          # 涉及的币种（用于 Y 轴/图例）
        "dates": ["2026-06-15 10:00", ...],    # 按天聚合的时间桶
        "series": [                            # 每个账户一条线
          {"name": "我的DeepSeek", "currency": "CNY", "data": [12.3, 11.8, ...]}
        ],
        "has_balance_accounts": true
      }

    时间桶策略：按天聚合（取当天最后一次有效余额），跨度大时不会挤成一团。
    余额型账户：DeepSeek、OpenAI 中转站（balance 型）。
    """
    days = max(1, min(90, days))
    from datetime import timedelta, date as date_cls
    since = datetime.now() - timedelta(days=days)

    def _is_balance_account(a: dict) -> bool:
        meta = registry.get_provider_meta(a["provider"])
        if not meta or not a.get("enabled"):
            return False
        if a["provider"] != "openai_proxy":
            return meta.get("type") == "balance"
        import json as _json
        try:
            cfg = _json.loads(a.get("config_json") or "{}")
        except Exception:  # noqa: BLE001
            cfg = {}
        return str(cfg.get("account_type") or "balance").lower() == "balance"

    balance_accounts = [a for a in db.list_accounts() if _is_balance_account(a)]
    if not balance_accounts:
        return {"keys": [], "currencies": [], "dates": [], "series": [], "has_balance_accounts": False}

    # 按天聚合：每天取该账户最后一条有效快照的余额
    # acc_id -> {date_str: (balance, currency)}
    acc_daily: dict[int, dict[str, tuple[float, str]]] = {}
    all_dates: set[str] = set()
    currencies_seen: set[str] = set()

    for a in balance_accounts:
        snaps = db.snapshots_since(a["id"], since)
        daily: dict[str, tuple[float, str]] = {}
        for s in snaps:
            if s.get("raw_error") or s.get("type") != "balance":
                continue
            bal = s.get("balance")
            cur = s.get("currency") or "CNY"
            if bal is None:
                continue
            t = s.get("fetched_at")
            if not t or len(t) < 10:
                continue
            day_key = t[:10]  # 'YYYY-MM-DD'
            # 同一天内取最后一条（snapshots_since 已按时间升序，后写覆盖）
            daily[day_key] = (float(bal), cur)
            all_dates.add(day_key)
            currencies_seen.add(cur)
        acc_daily[a["id"]] = daily

    if not all_dates:
        return {
            "keys": [a["display_name"] for a in balance_accounts],
            "currencies": sorted(currencies_seen),
            "dates": [], "series": [],
            "has_balance_accounts": True,
        }

    # 按天升序，前向填充缺失天（沿用上一日余额，避免断线）
    sorted_dates = sorted(all_dates)
    series = []
    for a in balance_accounts:
        daily = acc_daily.get(a["id"], {})
        # 确定该账户的币种（取最近一条的 currency，默认 CNY）
        cur = next((v[1] for v in reversed(list(daily.values()))), "CNY")
        data = []
        last_val = None
        for d in sorted_dates:
            if d in daily:
                last_val = daily[d][0]
            data.append(last_val)  # None 表示该账户当时还没有数据
        series.append({"name": a["display_name"], "currency": cur, "data": data})

    return {
        "keys": [a["display_name"] for a in balance_accounts],
        "currencies": sorted(currencies_seen),
        "dates": sorted_dates,
        "series": series,
        "has_balance_accounts": True,
    }
