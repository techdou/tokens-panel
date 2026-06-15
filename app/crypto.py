"""API Key 的对称加解密。基于 Fernet（AES128-CBC + HMAC）。"""
from __future__ import annotations

from cryptography.fernet import Fernet

from . import config

_fernet = Fernet(config.MASTER_KEY.encode() if isinstance(config.MASTER_KEY, str) else config.MASTER_KEY)


def encrypt(plaintext: str) -> str:
    """加密明文，返回可存库的字符串。"""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """解密。token 无效或密钥不匹配会抛 InvalidToken。"""
    return _fernet.decrypt(token.encode("ascii")).decode("utf-8")
