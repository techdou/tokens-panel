"""定时任务：定时刷新 + 每日报告 + 快照清理。

用 APScheduler 的 AsyncIOScheduler，与 FastAPI 的事件循环共用。
"""
from __future__ import annotations

import asyncio
import json
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, crypto, db
from . import alerts
from .providers import registry

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def refresh_all_accounts() -> None:
    """定时刷新所有 enabled 的 account。失败不抛，每个 account 独立处理。"""
    accounts = db.list_accounts()
    if not accounts:
        return
    log.info("定时刷新开始，共 %d 个账户", len(accounts))
    for acc in accounts:
        if not acc["enabled"]:
            continue
        try:
            api_key = crypto.decrypt(acc["encrypted_api_key"])
            cfg = json.loads(acc["config_json"] or "{}")
            result = await registry.run_query(acc["provider"], api_key, **cfg)
            result_dict = result.model_dump(mode="json")
            db.add_snapshot(acc["id"], result_dict)
            ok = result.raw_error is None
            log.info("定时刷新 account=%s provider=%s ok=%s", acc["id"], acc["provider"], ok)
            # 刷新成功后检查告警
            if ok:
                alerts.check_and_alert(acc, result_dict)
        except Exception as e:  # noqa: BLE001
            log.exception("定时刷新 account=%s 异常", acc["id"])


async def cleanup_snapshots_job() -> None:
    """每天清理 30 天前的快照。"""
    deleted = db.cleanup_old_snapshots(days=30)
    if deleted:
        log.info("清理了 %d 条过期快照", deleted)


def _make_daily_report_trigger() -> CronTrigger:
    """从 DAILY_REPORT_TIME (HH:MM) 构造每日触发器。"""
    try:
        hh, mm = config.DAILY_REPORT_TIME.split(":")
        return CronTrigger(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        log.warning("DAILY_REPORT_TIME 格式错误: %s，回退到 09:00", config.DAILY_REPORT_TIME)
        return CronTrigger(hour=9, minute=0)


def start() -> None:
    """启动调度器（幂等）。在 FastAPI startup 时调用。"""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 1. 定时刷新
    _scheduler.add_job(
        refresh_all_accounts,
        IntervalTrigger(minutes=config.REFRESH_INTERVAL_MINUTES),
        id="refresh_all",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # 2. 每日快照清理（凌晨 3:17）
    _scheduler.add_job(
        cleanup_snapshots_job,
        CronTrigger(hour=3, minute=17),
        id="cleanup_snapshots",
        replace_existing=True,
    )

    # 3. 每日报告（每天 DAILY_REPORT_TIME）
    _scheduler.add_job(
        alerts.daily_report,
        _make_daily_report_trigger(),
        id="daily_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    log.info(
        "调度器已启动：刷新间隔=%d分钟, 每日报告时间=%s",
        config.REFRESH_INTERVAL_MINUTES, config.DAILY_REPORT_TIME,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
