"""阶段1 冒烟测试：加密、DB、DeepSeek parser、登录鉴权。不依赖网络。"""
import os
import sys

# 确保用项目根目录的 .env（若存在），否则用环境变量
os.environ.setdefault("ADMIN_PASSWORD", "test123")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_crypto_roundtrip():
    from app import crypto
    enc = crypto.encrypt("sk-test-key-123")
    assert crypto.decrypt(enc) == "sk-test-key-123"


def test_db_crud():
    from app import crypto, db
    db.init_db()
    enc = crypto.encrypt("sk-test-key-123")
    aid = db.create_account("deepseek", "测试DS", enc, {"note": "hello"})
    acc = db.get_account(aid)
    assert acc is not None
    assert acc["provider"] == "deepseek"
    assert crypto.decrypt(acc["encrypted_api_key"]) == "sk-test-key-123"
    # snapshot
    db.add_snapshot(aid, {"provider": "deepseek", "balance": 12.34, "currency": "CNY"})
    snap = db.latest_snapshot(aid)
    assert snap is not None
    assert snap["balance"] == 12.34
    # cleanup
    assert db.delete_account(aid) is True


def test_deepseek_parser_normal():
    from app.providers import deepseek
    result = deepseek._parse({
        "is_available": True,
        "balance_infos": [
            {"currency": "CNY", "total_balance": "123.45", "disabled": False},
            {"currency": "USD", "total_balance": "0.00", "disabled": False},
        ],
    })
    assert result.type == "balance"
    assert result.balance == 123.45
    assert result.currency == "CNY"
    assert result.raw_error is None


def test_deepseek_parser_empty():
    from app.providers import deepseek
    result = deepseek._parse({"is_available": True, "balance_infos": []})
    assert result.balance == 0.0


def test_deepseek_parser_usd_only():
    from app.providers import deepseek
    result = deepseek._parse({
        "balance_infos": [{"currency": "USD", "total_balance": "5.50"}],
    })
    assert result.balance == 5.50
    assert result.currency == "USD"


def test_auth_session():
    from app import auth
    token = auth.create_session_cookie()
    assert auth.verify_session_cookie(token) is True
    assert auth.verify_session_cookie(None) is False
    assert auth.verify_session_cookie("garbage") is False
    assert auth.verify_password("test123") is True
    assert auth.verify_password("wrong") is False


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {fn.__name__}: {e!r}")
            import traceback
            traceback.print_exc()
    print(f"\n=== {passed}/{len(funcs)} passed ===")
    sys.exit(0 if passed == len(funcs) else 1)
