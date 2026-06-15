"""单用户会话鉴权。

策略：单一管理员密码（环境变量），登录后下发签名 cookie（itsdangerous），
中间件校验 cookie 有效性。API Key 本身也存加密，但面板入口仍需密码保护。
"""
from __future__ import annotations

import bcrypt
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config

_SECRET = config.MASTER_KEY  # 复用主密钥作为签名密钥
_serializer = URLSafeTimedSerializer(_SECRET, salt="tokens-session")
_MAX_AGE = config.SESSION_TTL_HOURS * 3600


def verify_password(plain: str) -> bool:
    """明文比对（单用户场景够用，且密码可随时改环境变量）。"""
    return plain == config.ADMIN_PASSWORD


def create_session_cookie() -> str:
    return _serializer.dumps({"u": "admin"})


def verify_session_cookie(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_logged_in(request: Request) -> bool:
    return verify_session_cookie(request.cookies.get("session"))


# bcrypt 不可用时退化为明文比对的备用哈希（当前未启用，留作以后多用户扩展）
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password_hash(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False
