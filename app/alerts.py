"""告警判定 + 每日报告。

告警规则（默认，可被 account.config 里的阈值覆盖）：
  - balance 型：余额 ≤ 默认 10 元告警（config.alert_balance_threshold）
  - window 型：任一窗口已用% ≥ 默认 90 告警（config.alert_used_threshold）

⚠️ edge trigger（状态变化触发）：仅在"从未超→超阈值"的跳变时推送一次。
  持续超阈值不重复发（省通知额度，尤其 Server 酱 5 次/天限制）。
  窗口重置后用量回落再回升突破阈值，才算新跳变。
  6 小时冷却作为双保险（极端情况）。

每日报告：每天 DAILY_REPORT_TIME 发一次汇总，含各账户当前状态 +
  「告警中」区块（即使当天没推送，也汇总当前所有超阈值的账户）。

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
ALERT_COOLDOWN_HOURS = 6           # 同账户告警冷却（edge trigger 的双保险）


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


def _progress_bar(percent: float, width: int = 10) -> str:
    """字符进度条：92.0% (width=10) → '▰▰▰▰▰▰▰▰▰▱'。用于 Server 酱/TG/邮件通用。"""
    filled = round(max(0.0, min(100.0, percent)) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def _is_triggered(result: dict[str, Any], bal_thr: float, used_thr: float) -> bool:
    """判断单账户结果是否触发告警阈值（不含 raw_error 判定，调用方应先排除）。"""
    if result.get("type") == "balance" and result.get("balance") is not None:
        if float(result["balance"]) <= bal_thr:
            return True
    if result.get("type") == "window" and result.get("tiers"):
        for t in result["tiers"]:
            if float(t.get("used_percent", 0)) >= used_thr:
                return True
    return False


def _build_alert_reasons(result: dict[str, Any], bal_thr: float, used_thr: float) -> list[str]:
    """构造告警明细行（带进度条）。"""
    reasons: list[str] = []
    if result.get("type") == "balance" and result.get("balance") is not None:
        balance = float(result["balance"])
        currency = result.get("currency", "CNY")
        if balance <= bal_thr:
            reasons.append(
                f"余额 {balance:.2f} {currency}\n  阈值 {bal_thr:.0f}"
            )
    if result.get("type") == "window" and result.get("tiers"):
        for t in result["tiers"]:
            used = float(t.get("used_percent", 0))
            if used >= used_thr:
                label = {"five_hour": "5小时", "weekly": "每周"}.get(t.get("type"), t.get("type"))
                bar = _progress_bar(used)
                reasons.append(
                    f"{label} {bar} {used:.1f}%\n  阈值 {used_thr:.0f}% · 剩余 {100-used:.0f}%"
                )
    return reasons


def check_and_alert(acc: dict[str, Any], result: dict[str, Any]) -> str | None:
    """检查单账户结果是否触发告警（edge trigger），触发则发送。返回告警文案（未触发/未发送返回 None）。

    edge trigger 逻辑：
      - 查询失败（raw_error）→ 不告警，重置状态为「未触发」
      - 当前未超阈值 → 不发，状态置「未触发」
      - 当前超阈值 + 上次已触发 → 不发（持续状态）
      - 当前超阈值 + 上次未触发（首次/回落后再突破）→ 发送
      - ⚠️ 仅发送成功才标记「已触发」，失败则保持原状态下次重试（避免永久吞告警）
    """
    bal_thr, used_thr = _account_thresholds(acc)

    # 查询失败：不告警，重置状态（下次成功查询时若仍超阈值会重新触发）
    if result.get("raw_error"):
        db.set_last_alert_state(acc["id"], False)
        return None

    now_triggered = _is_triggered(result, bal_thr, used_thr)
    last_triggered = db.get_last_alert_state(acc["id"])

    # 当前未超阈值：立即更新状态为「未触发」，不发
    if not now_triggered:
        db.set_last_alert_state(acc["id"], False)
        return None

    # 当前超阈值 + 上次也超阈值：持续状态，不重发（edge trigger 核心）
    if last_triggered:
        return None

    # edge trigger 已充分保证"状态变化才发"，冷却是双保险防状态记录丢失。
    # 但冷却不应阻断"回落后再突破"的合法跳变（last_triggered=False 的真跳变），
    # 仅在"首次判定（last=None）"时检查冷却（防历史状态全丢导致首次狂发）。
    if last_triggered is None:
        last = db.last_alert_time(acc["id"], "alert")
        if last and datetime.now() - last < timedelta(hours=ALERT_COOLDOWN_HOURS):
            log.info("账户 %s 告警冷却中（首次判定，上次 %s），跳过", acc["id"], last)
            return None

    reasons = _build_alert_reasons(result, bal_thr, used_thr)
    message = f"**{acc['display_name']}** 触发告警：\n\n" + "\n".join(f"- {r}" for r in reasons)
    title = f"⚠️ {acc['display_name']} 额度预警"

    results = notify.send(title, message)
    any_ok = False
    for channel, res in results.items():
        ok = bool(res.get("ok"))
        any_ok = any_ok or ok
        db.add_notify_log(acc["id"], "alert", message, channel, ok)
    if not results:
        db.add_notify_log(acc["id"], "alert", message, "none", False)

    # ⚠️ 仅发送成功才把状态标记为「已触发」。
    # 失败则保持原状态（last_triggered 仍为 None/False），下次刷新会重试，避免永久吞告警。
    if any_ok:
        db.set_last_alert_state(acc["id"], True)

    return message if any_ok else None


def _currency_symbol(currency: str) -> str:
    return "¥" if currency.upper() == "CNY" else ("$" if currency.upper() == "USD" else "")


async def daily_report() -> None:
    """每日报告：汇总各账户当前状态 + 告警中账户汇总。"""
    accounts = db.list_accounts()
    if not accounts:
        return

    lines: list[str] = ["## 📊 Token 额度日报\n"]
    balance_total: dict[str, float] = {}
    alert_lines: list[str] = []  # 告警中账户汇总

    for acc in accounts:
        snap = db.latest_snapshot(acc["id"])
        if not snap:
            lines.append(f"- **{acc['display_name']}**：暂无数据")
            continue

        if snap.get("raw_error"):
            lines.append(f"- **{acc['display_name']}** 🔴 {snap['raw_error'][:50]}")
            continue

        bal_thr, used_thr = _account_thresholds(acc)

        if snap.get("type") == "balance":
            bal = snap.get("balance") or 0
            cur = snap.get("currency", "CNY")
            balance_total[cur] = balance_total.get(cur, 0) + bal
            sym = _currency_symbol(cur)
            flag = " ⚠️" if bal <= bal_thr else ""
            lines.append(f"- **{acc['display_name']}**：{sym}{bal:.2f}{flag}")
        elif snap.get("tiers"):
            parts = []
            over = False
            for t in snap["tiers"]:
                used = float(t.get("used_percent", 0))
                label = {"five_hour": "5h", "weekly": "周"}.get(t.get("type"), t.get("type"))
                parts.append(f"{label} {used:.0f}%")
                if used >= used_thr:
                    over = True
            lines.append(f"- **{acc['display_name']}**：" + " / ".join(parts) + (" ⚠️" if over else ""))
            if over:
                alert_lines.append(f"- **{acc['display_name']}**：" + " / ".join(parts))

    if balance_total:
        total_str = " + ".join(f"{_currency_symbol(k)}{v:.2f}" for k, v in balance_total.items())
        lines.append(f"\n**合计余额**：{total_str}")

    # 告警中账户汇总（即使当天没推送也列出）
    if alert_lines:
        lines.append("\n**告警中**：")
        lines.extend(alert_lines)

    lines.append(f"\n_生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    content = "\n".join(lines)

    results = notify.send("📊 Token 额度日报", content)
    any_ok = any(r.get("ok") for r in results.values())
    for channel, res in results.items():
        db.add_notify_log(None, "daily", content, channel, bool(res.get("ok")))
    if not results:
        db.add_notify_log(None, "daily", content, "none", False)
    log.info("每日报告发送完毕，ok=%s", any_ok)
