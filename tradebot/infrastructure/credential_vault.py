"""Encryption for MT5 passwords stored in the users database.

Passwords are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before they
are written to SQLite and decrypted when rows are read back.

The key comes from the ``TELETRADER_SECRET_KEY`` environment variable when
set. Otherwise it is read from a key file (``TELETRADER_KEY_FILE``, default
``secret.key``), which is generated on first use. Keep that file out of
backups that include ``users.db``: anyone holding both can decrypt.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

PREFIX = "enc:v1:"
DEFAULT_KEY_FILE = "secret.key"

_cache: dict[str, Fernet] = {}


def _key_source() -> tuple[str, bytes | None, Path | None]:
    env_key = os.environ.get("TELETRADER_SECRET_KEY", "").strip()
    if env_key:
        return f"env:{env_key}", env_key.encode(), None
    path = Path(os.environ.get("TELETRADER_KEY_FILE", DEFAULT_KEY_FILE))
    return f"file:{path.resolve()}", None, path


def _load_or_create_key_file(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.warning(
        f"Generated new credential key at {path}. Back it up separately: "
        "without it, stored MT5 passwords cannot be decrypted."
    )
    return key


def _fernet() -> Fernet:
    cache_key, key, path = _key_source()
    f = _cache.get(cache_key)
    if f is None:
        if key is None:
            key = _load_or_create_key_file(path)  # type: ignore[arg-type]
        f = Fernet(key)
        _cache[cache_key] = f
    return f


def is_encrypted(value: str | None) -> bool:
    return bool(value) and value.startswith(PREFIX)  # type: ignore[union-attr]


def encrypt(plaintext: str) -> str:
    """Encrypt a password for storage. Already-encrypted values pass through."""
    if not plaintext or is_encrypted(plaintext):
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt(stored: str) -> str:
    """Decrypt a stored password. Legacy plaintext values pass through."""
    if not is_encrypted(stored):
        return stored
    try:
        return _fernet().decrypt(stored[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Cannot decrypt stored MT5 password: the credential key does not "
            "match the one used to encrypt it."
        ) from exc
