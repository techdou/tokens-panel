"""通知发送：Server 酱 / Telegram / 邮件。

每个渠道一个发送函数，配置都从 settings 表读取（前端设置页写入）。
渠道之间独立，任何一个失败不影响其它。

settings 表的 key 约定：
  notify_serverchan_key    = SCTxxxx
  notify_telegram_bot_token = 123:abc
  notify_telegram_chat_id   = 123456
  notify_smtp_host          = smtp.qq.com
  notify_smtp_port          = 465
  notify_smtp_user          = you@qq.com
  notify_smtp_password      = 授权码
  notify_smtp_to            = target@example.com
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

import httpx

from . import db

log = logging.getLogger(__name__)

HTTP_TIMEOUT = httpx.Timeout(10.0)


def _setting(key: str) -> str:
    return (db.get_setting(key) or "").strip()


def send(title: str, content: str) -> dict[str, Any]:
    """向所有【已配置】的渠道发送通知，返回每个渠道的结果。

    content 支持 Markdown（Server 酱/TG 会渲染，邮件转 HTML）。
    """
    results: dict[str, Any] = {}

    # Server 酱
    if _setting("notify_serverchan_key"):
        results["serverchan"] = _send_serverchan(title, content)

    # Telegram
    tg_token = _setting("notify_telegram_bot_token")
    tg_chat = _setting("notify_telegram_chat_id")
    if tg_token and tg_chat:
        results["telegram"] = _send_telegram(tg_token, tg_chat, title, content)

    # 邮件
    if _setting("notify_smtp_host") and _setting("notify_smtp_user"):
        results["email"] = _send_email(title, content)

    if not results:
        log.warning("通知未发送：没有配置任何渠道")
    return results


def _send_serverchan(title: str, content: str) -> dict[str, Any]:
    """Server 酱 Turbo 接口。失败返回 {ok:False, error}。"""
    key = _setting("notify_serverchan_key")
    try:
        url = f"https://sctapi.ftqq.com/{key}.send"
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, data={"title": title[:32], "desp": content})
            data = resp.json()
            if resp.status_code == 200 and data.get("code", 0) == 0:
                log.info("Server 酱发送成功")
                return {"ok": True}
            return {"ok": False, "error": f"{data.get('message') or resp.text[:100]}"}
    except Exception as e:  # noqa: BLE001
        log.exception("Server 酱发送异常")
        return {"ok": False, "error": str(e)}


def _send_telegram(bot_token: str, chat_id: str, title: str, content: str) -> dict[str, Any]:
    """Telegram Bot sendMessage（HTML 模式）。Markdown → TG 支持的 HTML 子集。"""
    from .notify_render import _escape_html, md_to_html
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # 标题加粗；正文用 md_to_html 转换（输出已转义，TG HTML 安全）
        text = f"<b>{_escape_html(title)}</b><br><br>{md_to_html(content)}"
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(url, data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            data = resp.json()
            if data.get("ok"):
                log.info("Telegram 发送成功")
                return {"ok": True}
            return {"ok": False, "error": data.get("description") or resp.text[:100]}
    except Exception as e:  # noqa: BLE001
        log.exception("Telegram 发送异常")
        return {"ok": False, "error": str(e)}


def _send_email(title: str, content: str) -> dict[str, Any]:
    """邮件发送：MIMEMultipart alternative（纯文本兜底 + HTML 优先）。

    content 是 Markdown：纯文本 part 保留原文（不支持 HTML 的客户端可读），
    HTML part 用 md_to_html 转换并包进极简风外壳。
    """
    from email.mime.multipart import MIMEMultipart
    from .notify_render import email_shell, md_to_html

    host = _setting("notify_smtp_host")
    port = int(_setting("notify_smtp_port") or "465")
    user = _setting("notify_smtp_user")
    password = _setting("notify_smtp_password")
    to = _setting("notify_smtp_to") or user

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = formataddr(("Token 面板", user))
        msg["To"] = to
        # RFC 2046：plain 在前、html 在后，客户端按偏好选最优
        msg.attach(MIMEText(content, "plain", "utf-8"))
        msg.attach(MIMEText(email_shell(title, md_to_html(content)), "html", "utf-8"))

        # 465 用 SSL，其它端口用 STARTTLS
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        try:
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
            log.info("邮件发送成功 → %s", to)
            return {"ok": True}
        finally:
            server.quit()
    except Exception as e:  # noqa: BLE001
        log.exception("邮件发送异常")
        return {"ok": False, "error": str(e)}


# ---------------- 测试通知 ----------------

def send_test() -> dict[str, Any]:
    """供前端「测试通知」按钮调用。"""
    return send(
        "Token 面板 · 测试通知",
        "✅ 如果你收到这条消息，说明通知渠道配置正确。\n\n_来自 Token 余额聚合面板_",
    )
