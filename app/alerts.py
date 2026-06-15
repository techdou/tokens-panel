"""告警判定 + 每日报告。

告警规则（默认，可被 account.config 里的阈值覆盖）：
  - balance 型：余额 ≤ 默认 10 元告警（config.alert_balance_threshold）
  - window 型：任一窗口已用% ≥ 默认 90 告警（config.alert_used_threshold）

防轰炸：同一账户同种告警，距上次发送 < 6 小时则跳过。
每日报告：每天 DAILY_REPORT_TIME 发一次汇总，含各账户当前状态 + 与昨日变化。

阈值配置优先级：account.config_json > settings 表默认值 > 硬编码默认。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from . import db, notify
from .providers import registry

log = logging.getLogger(__name__)

DEFAULT_BALANCE_THRESHOLD = 10.0   # 余额 ≤ 10 元
DEFAULT_USED_THRESHOLD = 90.0      # 已用 ≥ 90%
ALERT_COOLDOWN_HOURS = 6           # 同账户同告警冷却


def _account_thresholds(acc: dict[str, Any]) -> tuple[float, float]:
    """取某账户的告警阈值（balance_threshold, used_threshold）。"""
    try:
        cfg = json.loads(acc.get("config_json") or "{}")
    except Exception:  # noqa: BLE001
        cfg = {}
    bal_thr = cfg.get("alert_balance_threshold")
    used_thr = cfg.get("alert_used_threshold")
    # 也允许全局 settings 覆盖默认
    bal_thr = bal_thr or db.get_setting("alert_balance_threshold") or DEFAULT_BALANCE_THRESHOLD
    used_thr = used_thr or db.get_setting("alert_used_threshold") or DEFAULT_USED_THRESHOLD
    return float(bal_thr), float(used_thr)


def check_and_alert(acc: dict[str, Any], result: dict[str, Any]) -> str | None:
    """检查单账户结果是否触发告警，触发则发送。返回告警文案（未触发返回 None）。"""
    if result.get("raw_error"):
        return None  # 查询失败不告警（避免 key 失效时狂发）

    reasons: list[str] = []
    bal_thr, used_thr = _account_thresholds(acc)

    if result.get("type") == "balance" and result.get("balance") is not None:
        balance = float(result["balance"])
        currency = result.get("currency", "CNY")
        if balance <= bal_thr:
            reasons.append(f"余额仅 {balance:.2f} {currency}（阈值 {bal_thr:.0f}）")

    if result.get("type") == "window" and result.get("tiers"):
        for t in result["tiers"]:
            used = float(t.get("used_percent", 0))
            if used >= used_thr:
                label = {"five_hour": "5小时", "weekly": "每周"}.get(t.get("type"), t.get("type"))
                reasons.append(f"{label}窗口已用 {used:.1f}%（阈值 {used_thr:.0f}%）")

    if not reasons:
        return None

    # 防轰炸
    last = db.last_alert_time(acc["id"], "alert")
    if last and datetime.now() - last < timedelta(hours=ALERT_COOLDOWN_HOURS):
        log.info("账户 %s 告警冷却中（上次 %s），跳过", acc["id"], last)
        return None

    message = f"**{acc['display_name']}** 触发告警：\n" + "\n".join(f"- {r}" for r in reasons)
    title = f"⚠️ {acc['display_name']} 额度预警"

    results = notify.send(title, message)
    # 记录日志（每个渠道一条）
    any_ok = False
    for channel, res in results.items():
        ok = bool(res.get("ok"))
        any_ok = any_ok or ok
        db.add_notify_log(acc["id"], "alert", message, channel, ok)
    if not results:
        db.add_notify_log(acc["id"], "alert", message, "none", False)

    return message if any_ok else None


async def daily_report() -> None:
    """每日报告：汇总各账户当前状态 + 与昨日变化。"""
    accounts = db.list_accounts()
    if not accounts:
        return

    lines: list[str] = ["## 📊 Token 额度日报\n"]
    balance_total: dict[str, float] = {}

    for acc in accounts:
        snap = db.latest_snapshot(acc["id"])
        if not snap:
            lines.append(f"- **{acc['display_name']}**：暂无数据")
            continue

        if snap.get("raw_error"):
            lines.append(f"- **{acc['display_name']}**：❌ {snap['raw_error'][:50]}")
            continue

        if snap.get("type") == "balance":
            bal = snap.get("balance") or 0
            cur = snap.get("currency", "CNY")
            balance_total[cur] = balance_total.get(cur, 0) + bal
            lines.append(f"- **{acc['display_name']}**：{bal:.2f} {cur}")
        elif snap.get("tiers"):
            parts = []
            for t in snap["tiers"]:
                label = {"five_hour": "5h", "weekly": "周"}.get(t.get("type"), t.get("type"))
                parts.append(f"{label} {t.get('used_percent', 0):.0f}%")
            lines.append(f"- **{acc['display_name']}**：" + " / ".join(parts))

    if balance_total:
        lines.append("\n**合计余额**：" + " + ".join(f"{v:.2f} {k}" for k, v in balance_total.items()))

    lines.append(f"\n_生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    content = "\n".join(lines)

    results = notify.send("📊 Token 额度日报", content)
    any_ok = any(r.get("ok") for r in results.values())
    for channel, res in results.items():
        db.add_notify_log(None, "daily", content, channel, bool(res.get("ok")))
    if not results:
        db.add_notify_log(None, "daily", content, "none", False)
    log.info("每日报告发送完毕，ok=%s", any_ok)
