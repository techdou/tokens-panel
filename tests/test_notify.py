"""通知渲染层 + 渠道结构测试。

覆盖：
- md_to_html：粗体/斜体/标题/列表/换行/转义/边界
- email_shell：HTML 外壳结构
- _send_email：MIMEMultipart 结构（mock smtplib）
- _send_telegram：HTML payload（mock httpx）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADMIN_PASSWORD", "test123")

from app import notify
from app.notify_render import _escape_html, email_shell, md_to_html


# ============ md_to_html 基础语法 ============

def test_bold():
    assert md_to_html("**粗体**") == "<b>粗体</b>"
    assert md_to_html("a **b** c") == "a <b>b</b> c"


def test_italic():
    assert md_to_html("_斜体_") == "<i>斜体</i>"
    # 单词内下划线不误伤（如 my_var_name）
    assert md_to_html("my_var_name") == "my_var_name"


def test_heading():
    out = md_to_html("## 📊 标题")
    assert "<b " in out and "📊 标题" in out
    assert "font-size:1.15em" in out


def test_bullet():
    assert md_to_html("- 项目") == "• 项目"
    # 非行首的 - 不转换
    assert md_to_html("a - b") == "a - b"


def test_newline_to_br():
    assert md_to_html("第一行\n第二行") == "第一行<br>第二行"


def test_combined_daily_report():
    """模拟日报实际内容，验证组合渲染。"""
    md = "## 📊 Token 额度日报\n\n- **我的DS**：46.98 CNY\n\n**合计余额**：46.98 CNY"
    out = md_to_html(md)
    assert "<b " in out  # 标题
    assert "<b>我的DS</b>" in out  # 账户名粗体
    assert "• " in out  # 列表项
    assert "<b>合计余额</b>" in out


# ============ 转义 / XSS 防御 ============

def test_escape_html_entities():
    assert _escape_html("<script>") == "&lt;script&gt;"
    assert _escape_html("a & b") == "a &amp; b"


def test_xss_in_md_to_html():
    """原文含 <script>，转换后被转义，不会被解析成标签。"""
    out = md_to_html("**a** <script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<b>a</b>" in out  # 正常标记仍生效


def test_escape_amp_first():
    """& 转义在最前，避免把后造出的实体再转。"""
    out = _escape_html("&lt;")
    assert out == "&amp;lt;"  # 原本的 &lt; 字符串再转义


# ============ 边界 ============

def test_empty():
    assert md_to_html("") == ""
    assert md_to_html(None) == ""


def test_single_asterisk_no_match():
    """单词内单个 * 不触发粗体。"""
    assert md_to_html("2 * 3 = 6") == "2 * 3 = 6"


def test_emoji_preserved():
    assert md_to_html("✅ 成功") == "✅ 成功"


# ============ email_shell ============

def test_email_shell_structure():
    html = email_shell("日报标题", "<b>合计</b>：100")
    assert "<!DOCTYPE html>" in html
    assert "日报标题" in html
    assert "<b>合计</b>：100" in html
    assert "#FAFAF7" in html  # 纸色背景
    assert "max-width:560" in html  # 居中卡片
    assert "tabular-nums" in html  # 等宽数字


def test_email_shell_escapes_title():
    """标题里的 < > 被转义。"""
    html = email_shell("a<b>c", "body")
    assert "a&lt;b&gt;c" in html
    assert "<b>c" not in html.split("a")[1].split("c")[0]  # 无裸标签


# ============ _send_email MIMEMultipart 结构（mock smtplib）============

def test_send_email_multipart_structure(monkeypatch=None):
    """_send_email 应生成 multipart/alternative，含 plain + html 两 part。"""
    # 写入 SMTP 配置
    from app import db
    db.init_db()
    db.set_setting("notify_smtp_host", "smtp.test.com")
    db.set_setting("notify_smtp_port", "465")
    db.set_setting("notify_smtp_user", "from@test.com")
    db.set_setting("notify_smtp_password", "pwd")
    db.set_setting("notify_smtp_to", "to@test.com")

    captured = {}

    class FakeServer:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): pass
        def sendmail(self, frm, to, raw):
            captured["raw"] = raw
            captured["to"] = to
        def quit(self): pass

    # mock smtplib.SMTP_SSL
    import smtplib
    orig = smtplib.SMTP_SSL
    smtplib.SMTP_SSL = FakeServer
    try:
        result = notify._send_email("📊 日报", "## 标题\n- **DS**：10 元")
    finally:
        smtplib.SMTP_SSL = orig

    assert result == {"ok": True}
    raw = captured["raw"]
    # 用标准库 email 解析器解码（处理 base64 / quoted-printable）
    import email as email_lib
    msg = email_lib.message_from_string(raw)
    assert msg.is_multipart()
    parts = {p.get_content_type(): p for p in msg.walk() if p.get_content_type() in ("text/plain", "text/html")}
    assert "text/plain" in parts
    assert "text/html" in parts
    # plain part 保留原文
    plain_text = parts["text/plain"].get_payload(decode=True).decode("utf-8")
    assert "## 标题" in plain_text
    # html part 含转换结果
    html_text = parts["text/html"].get_payload(decode=True).decode("utf-8")
    assert "<b>DS</b>" in html_text
    assert "<!DOCTYPE html>" in html_text


# ============ _send_telegram HTML payload（mock httpx）============

def test_send_telegram_html_payload():
    """_send_telegram 应发送转换后的 HTML，parse_mode=HTML。"""
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self): return {"ok": True, "result": {}}

    class FakeClient:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            class _R:
                status_code = 200
                def json(self): return {"ok": True}
            return _R()

    import httpx
    orig = httpx.Client
    httpx.Client = FakeClient
    try:
        result = notify._send_telegram("123:abc", "999", "⚠️ 告警", "**DS** 余额仅 5 元\n- 已用 90%")
    finally:
        httpx.Client = orig

    assert result == {"ok": True}
    assert "parse_mode" in captured["data"]
    assert captured["data"]["parse_mode"] == "HTML"
    assert captured["data"]["chat_id"] == "999"
    text = captured["data"]["text"]
    # 标题加粗
    assert "<b>⚠️ 告警</b>" in text
    # 正文转换
    assert "<b>DS</b>" in text
    assert "• 已用" in text
    assert "<br>" in text


if __name__ == "__main__":
    import inspect
    funcs = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in funcs:
        try:
            # 支持 monkeypatch 参数（无参调用）
            sig = inspect.signature(fn)
            if len(sig.parameters) == 0:
                fn()
            else:
                fn(None)
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e!r}")
            import traceback; traceback.print_exc()
            failed.append(name)
    print(f"\n=== {passed}/{len(funcs)} passed ===")
    sys.exit(0 if not failed else 1)
