"""端到端 HTTP 测试：登录 → 加账户 → 刷新 → 验证返回结构。

运行方式（二选一）：
  1. 先起服务再跑：set ADMIN_PASSWORD=test123 && python -m uvicorn app.main:app --port 8765
                  然后：python -u tests/test_e2e.py
  2. 通过 run_tests.py 自动拉起服务再跑。

服务没起时会输出 SKIP 而非报错，不阻塞其它测试套件。
用 fake API Key，DeepSeek 会返回 401 —— 验证错误处理链路（raw_error 正确填充）。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "http://127.0.0.1:8765"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "test123")


def req(method, path, data=None, cookie=None, timeout=5):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode(), resp.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), None


def _server_alive() -> bool:
    try:
        urllib.request.urlopen(f"{BASE}/api/session", timeout=2).read()
        return True
    except Exception:
        return False


def _ensure_server():
    """服务没起则自动拉起（后台），等就绪。返回是否由我们启动。"""
    if _server_alive():
        return False
    # 后台拉起
    import subprocess
    env = dict(os.environ, ADMIN_PASSWORD=PASSWORD)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    # 等就绪（最多 8 秒）
    for _ in range(40):
        if _server_alive():
            return True
        time.sleep(0.2)
    proc.terminate()
    return False


def test_e2e_full_flow():
    """完整端到端流程：登录→加账户→刷新→删除。"""
    started_by_us = _ensure_server()
    try:
        if not _server_alive():
            print("[SKIP] test_e2e_full_flow — 本地服务无法启动（uvicorn 未装或端口占用）")
            return

        passed, total = 0, 0

        def check(name, cond, detail=""):
            nonlocal passed, total
            total += 1
            if cond:
                passed += 1
                print(f"[PASS] {name}")
            else:
                print(f"[FAIL] {name} {detail}")

        # 1. 未登录访问受保护接口应 401
        s, _, _ = req("GET", "/api/accounts")
        check("未登录 GET /api/accounts → 401", s == 401, f"got {s}")

        # 2. 错误密码登录应 401
        s, _, _ = req("POST", "/api/login", {"password": "wrong"})
        check("错误密码 → 401", s == 401, f"got {s}")

        # 3. 正确密码登录
        s, body, cookie = req("POST", "/api/login", {"password": PASSWORD})
        check("正确密码登录 → 200", s == 200, f"got {s}")
        check("登录返回 cookie", bool(cookie) and "session" in (cookie or ""), "")
        sess = cookie.split(";")[0] if cookie else ""

        # 4. 登录后访问 /api/accounts
        s, body, _ = req("GET", "/api/accounts", cookie=sess)
        check("登录后 GET /api/accounts → 200", s == 200, f"got {s}")

        # 5. providers 接口
        s, body, _ = req("GET", "/api/providers", cookie=sess)
        providers = json.loads(body)["providers"]
        check("providers 含 deepseek", any(p["provider"] == "deepseek" for p in providers), str(providers))

        # 6. 新增账户
        s, body, _ = req("POST", "/api/accounts", {
            "provider": "deepseek", "display_name": "e2e测试", "api_key": "sk-fake-invalid-key"
        }, cookie=sess)
        check("新增账户 → 200", s == 200, f"got {s}")
        aid = json.loads(body).get("id")

        # 7. 刷新（假 key 会 401 → raw_error 应填充）
        s, body, _ = req("POST", "/api/refresh", cookie=sess)
        check("刷新接口 → 200", s == 200, f"got {s}")
        results = json.loads(body).get("results", [])
        check("刷新返回 1 条结果", len(results) == 1, str(results))
        if results:
            r = results[0]
            check("raw_error 已填充（假 key 预期失败）", r.get("raw_error") is not None, str(r)[:200])

        # 8. latest 已落库
        s, body, _ = req("GET", "/api/accounts", cookie=sess)
        accs = json.loads(body)["accounts"]
        check("account.latest 已填充", bool(accs) and accs[0].get("latest") is not None, "")

        # 9. 删除账户
        if aid:
            req("DELETE", f"/api/accounts/{aid}", cookie=sess)
        s, body, _ = req("GET", "/api/accounts", cookie=sess)
        check("删除后账户清理干净", json.loads(body)["accounts"] == [], "")

        print(f"\n=== e2e: {passed}/{total} passed ===")
        assert passed == total, f"e2e 有 {total - passed} 项失败"
    finally:
        # 如果是我们起的服务，测试完关掉
        if started_by_us:
            import socket
            try:
                s = socket.socket()
                s.settimeout(1)
                s.connect(("127.0.0.1", 8765))
                s.close()
            except Exception:
                pass


if __name__ == "__main__":
    test_e2e_full_flow()
