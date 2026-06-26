"""通知内容的 Markdown → HTML 转换层（邮件 / Telegram 共用）。

只支持项目实际使用的 Markdown 子集（见 alerts.py / daily_report）：
  **bold** → <b>bold</b>
  _italic_ → <i>italic</i>
  ## 标题   → 整行加粗放大（不用 <h2>，TG 不支持）
  - 项目   → • 项目（不用 <ul>/<li>，inline CSS 麻烦且 TG 不支持）
  \n      → <br>

实现原则：
  1. 先 HTML 转义全文（& < >），杜绝 XSS 与标签错乱；
  2. 转义后再做标记替换，避免原文里的 <script> 等被解析；
  3. 输出是 HTML 片段（不含 <html> 外壳），由调用方按渠道包裹。

Server 酱不走这里——它接受原生 Markdown 由服务端渲染微信卡片。
"""
from __future__ import annotations

import re

# 行内标记：先粗体再斜体，避免 **a _b** c** 这种交叉误伤
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RE_ITALIC = re.compile(r"(?<![*\w])_(.+?)_(?![*\w])")  # _ 两侧不能是 * 或 字母数字，防 a_b_c 误伤
_RE_BULLET = re.compile(r"^-\s+", re.MULTILINE)  # 行首的 "- "
_RE_HEADING = re.compile(r"^##\s+(.+)$", re.MULTILINE)  # ## 标题


def _escape_html(s: str) -> str:
    """转义 HTML 特殊字符（& 必须最先，否则把后面造出的实体再转一次）。"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_to_html(md: str) -> str:
    """Markdown → 极简 HTML 片段（邮件/TG 共用）。

    流程：转义 → 标题 → 粗体 → 斜体 → 列表项 → 换行。
    返回值不含 <html>/<body> 外壳，由调用方按渠道包裹。
    """
    if not md:
        return ""

    # 1. 先全文 HTML 转义，杜绝 XSS
    s = _escape_html(md)

    # 2. 标题行（## 开头）→ 加粗放大整行
    s = _RE_HEADING.sub(lambda m: f'<b style="font-size:1.15em">{m.group(1).strip()}</b>', s)

    # 3. 行内：粗体 → 斜体（顺序重要）
    s = _RE_BOLD.sub(r"<b>\1</b>", s)
    s = _RE_ITALIC.sub(r"<i>\1</i>", s)

    # 4. 列表项：行首 "- " → "• "
    s = _RE_BULLET.sub("• ", s)

    # 5. 换行 → <br>（TG 和邮件都认 <br>，比 \n 稳）
    s = s.replace("\n", "<br>")

    return s


def email_shell(title: str, body_html: str) -> str:
    """把转换后的 HTML 片段包进极简风邮件外壳（全 inline CSS）。

    设计：纸色背景 #FAFAF7、白卡片、暗色文本、柔和分隔线、等宽数字。
    max-width 560 居中，兼容 Gmail/Outlook/手机原生客户端。
    """
    safe_title = _escape_html(title or "")
    return f"""\
<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px;background:#FAFAF7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Hanken Grotesk',Arial,sans-serif;color:#17181C;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><tr><td align="center">
    <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px;border-collapse:collapse;background:#FFFFFF;border:1px solid #E6E4DD;border-radius:10px;overflow:hidden;">
      <tr><td style="padding:20px 28px;border-bottom:1px solid #E6E4DD;">
        <p style="margin:0;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#6B6B66;">Token 余额聚合面板</p>
        <h1 style="margin:6px 0 0;font-size:20px;font-weight:600;line-height:1.3;">{safe_title}</h1>
      </td></tr>
      <tr><td style="padding:24px 28px;font-size:14px;line-height:1.7;font-variant-numeric:tabular-nums;">
        {body_html}
      </td></tr>
    </table>
    <p style="margin:16px 0 0;font-size:11px;color:#9B9B95;text-align:center;">— 本邮件由你自部署的 Token 面板自动发送 —</p>
  </td></tr></table>
</body></html>"""
