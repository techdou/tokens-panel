"""SQLite 数据访问层。

阶段1只需 accounts + snapshots 两张表，alert_rules / notify_logs 在阶段4再加。
用 sqlite3 原生接口，避免 ORM 重量；连接按请求短暂打开，SQLite 自带文件锁足够单用户用。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from . import config

# SQLite 写入用一把全局锁，避免多线程并发写冲突（APScheduler + Web 请求线程都有写）
_write_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 读写并发更友好
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）。"""
    with _write_lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                provider           TEXT    NOT NULL,
                display_name       TEXT    NOT NULL,
                encrypted_api_key  TEXT    NOT NULL,
                config_json        TEXT    NOT NULL DEFAULT '{}',
                enabled            INTEGER NOT NULL DEFAULT 1,
                created_at         TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                result_json TEXT    NOT NULL,
                fetched_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_account_time ON snapshots(account_id, fetched_at);

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notify_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER,
                kind        TEXT NOT NULL,
                message     TEXT NOT NULL,
                channel     TEXT NOT NULL,
                ok          INTEGER NOT NULL,
                sent_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_notify_logs_time ON notify_logs(sent_at);
            """
        )


# ---------------- accounts ----------------

def list_accounts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_account(account_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return dict(row) if row else None


def create_account(provider: str, display_name: str, encrypted_api_key: str, config_json: dict | None = None) -> int:
    with _write_lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts (provider, display_name, encrypted_api_key, config_json) VALUES (?,?,?,?)",
            (provider, display_name, encrypted_api_key, json.dumps(config_json or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def update_account(
    account_id: int,
    *,
    display_name: str | None = None,
    encrypted_api_key: str | None = None,
    config_json: dict | None = None,
    enabled: int | None = None,
) -> bool:
    fields, params = [], []
    if display_name is not None:
        fields.append("display_name = ?"); params.append(display_name)
    if encrypted_api_key is not None:
        fields.append("encrypted_api_key = ?"); params.append(encrypted_api_key)
    if config_json is not None:
        fields.append("config_json = ?"); params.append(json.dumps(config_json, ensure_ascii=False))
    if enabled is not None:
        fields.append("enabled = ?"); params.append(int(enabled))
    if not fields:
        return False
    params.append(account_id)
    with _write_lock, get_conn() as conn:
        cur = conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", params)
        return cur.rowcount > 0


def delete_account(account_id: int) -> bool:
    with _write_lock, get_conn() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return cur.rowcount > 0


# ---------------- snapshots ----------------

def add_snapshot(account_id: int, result: dict[str, Any], fetched_at: str | None = None) -> None:
    """记录一次查询快照。fetched_at 留空则用当前本地时间（默认）。"""
    if fetched_at:
        with _write_lock, get_conn() as conn:
            conn.execute(
                "INSERT INTO snapshots (account_id, result_json, fetched_at) VALUES (?, ?, ?)",
                (account_id, json.dumps(result, ensure_ascii=False, default=str), fetched_at),
            )
    else:
        with _write_lock, get_conn() as conn:
            conn.execute(
                "INSERT INTO snapshots (account_id, result_json) VALUES (?, ?)",
                (account_id, json.dumps(result, ensure_ascii=False, default=str)),
            )


def latest_snapshot(account_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json, fetched_at FROM snapshots WHERE account_id = ? ORDER BY id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["result_json"])
        data["fetched_at"] = row["fetched_at"]
        return data


def snapshots_since(account_id: int, since: datetime) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result_json, fetched_at FROM snapshots WHERE account_id = ? AND fetched_at >= ? ORDER BY fetched_at",
            (account_id, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["result_json"])
            d["fetched_at"] = r["fetched_at"]
            out.append(d)
        return out


def cleanup_old_snapshots(days: int = 30) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _write_lock, get_conn() as conn:
        cur = conn.execute("DELETE FROM snapshots WHERE fetched_at < ?", (cutoff,))
        return cur.rowcount


# ---------------- settings ----------------

def get_setting(key: str, default: Any = None) -> Any:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _write_lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ---------------- notify_logs ----------------

def add_notify_log(account_id: int | None, kind: str, message: str, channel: str, ok: bool) -> None:
    with _write_lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO notify_logs (account_id, kind, message, channel, ok) VALUES (?,?,?,?,?)",
            (account_id, kind, message[:500], channel, int(ok)),
        )


def last_alert_time(account_id: int, kind: str = "alert") -> datetime | None:
    """返回某账户最后一次成功告警的时间（用于防轰炸）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sent_at FROM notify_logs WHERE account_id = ? AND kind = ? AND ok = 1 "
            "ORDER BY id DESC LIMIT 1",
            (account_id, kind),
        ).fetchone()
        if not row:
            return None
        try:
            return datetime.strptime(row["sent_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def list_notify_logs(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notify_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
