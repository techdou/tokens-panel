"""端到端 HTTP 测试：登录 → 加账户 → 刷新 → 验证返回结构。

需要一个已运行的本地服务：python -m uvicorn app.main:app --port 8765
用 fake API Key，DeepSeek 会返回 401 错误——这正是我们要验证的：
错误处理链路（raw_error 正确填充，前端能标红）。
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"


def req(method, path, data=None, cookie=None):
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
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read().decode(), resp.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), None


def main():
    passed = 0
    total = 0

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

    # 3. 正确密码登录应 200 + 下发 cookie
    s, body, cookie = req("POST", "/api/login", {"password": "test123"})
    check("正确密码登录 → 200", s == 200, f"got {s}")
    check("登录返回 cookie", cookie is not None and "session" in (cookie or ""), "")
    sess = cookie.split(";")[0] if cookie else ""

    # 4. 登录后访问 /api/accounts 应 200
    s, body, _ = req("GET", "/api/accounts", cookie=sess)
    check("登录后 GET /api/accounts → 200", s == 200, f"got {s}")
    check("初始账户列表为空", json.loads(body)["accounts"] == [], body)

    # 5. providers 接口
    s, body, _ = req("GET", "/api/providers", cookie=sess)
    providers = json.loads(body)["providers"]
    check("providers 列表含 deepseek", any(p["provider"] == "deepseek" for p in providers), str(providers))

    # 6. 新增账户
    s, body, _ = req("POST", "/api/accounts", {
        "provider": "deepseek", "display_name": "测试DS", "api_key": "sk-fake-invalid-key"
    }, cookie=sess)
    check("新增账户 → 200", s == 200, f"got {s} {body}")
    acc = json.loads(body)
    aid = acc.get("id")
    check("新增账户有 id", aid is not None, str(acc))

    # 7. 刷新（用假 key，DeepSeek 会 401 → raw_error 应填充）
    s, body, _ = req("POST", "/api/refresh", cookie=sess)
    check("刷新接口 → 200", s == 200, f"got {s}")
    results = json.loads(body).get("results", [])
    check("刷新返回 1 条结果", len(results) == 1, str(results))
    if results:
        r = results[0]
        check("刷新结果含 raw_error（假 key 预期失败）", r.get("raw_error") is not None, str(r)[:200])
        check("刷新结果 provider=deepseek", r.get("provider") == "deepseek", "")
        check("刷新结果 type=balance", r.get("type") == "balance", "")

    # 8. 再次查 accounts，latest 应已被填充
    s, body, _ = req("GET", "/api/accounts", cookie=sess)
    accs = json.loads(body)["accounts"]
    check("account.latest 已填充", accs[0].get("latest") is not None, str(accs[0])[:200])
    check("account.latest 含 raw_error", accs[0]["latest"].get("raw_error") is not None, "")

    # 9. 删除账户
    s, body, _ = req("DELETE", f"/api/accounts/{aid}", cookie=sess)
    check("删除账户 → 200", s == 200, f"got {s}")
    s, body, _ = req("GET", "/api/accounts", cookie=sess)
    check("删除后列表为空", json.loads(body)["accounts"] == [], body)

    print(f"\n=== {passed}/{total} passed ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
