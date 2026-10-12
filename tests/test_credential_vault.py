import sqlite3

import pytest
from cryptography.fernet import Fernet

from tradebot.infrastructure import credential_vault, db


@pytest.fixture(autouse=True)
def isolated_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TELETRADER_SECRET_KEY", raising=False)
    monkeypatch.setenv("TELETRADER_KEY_FILE", str(tmp_path / "secret.key"))
    credential_vault._cache.clear()
    yield
    credential_vault._cache.clear()


def _raw_password(db_path, account):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT mt5_password FROM users WHERE mt5_account=?", (account,)
        ).fetchone()[0]
    finally:
        con.close()


def test_round_trip_and_key_file_created(tmp_path):
    token = credential_vault.encrypt("hunter2")
    assert token.startswith(credential_vault.PREFIX)
    assert "hunter2" not in token
    assert credential_vault.decrypt(token) == "hunter2"
    assert (tmp_path / "secret.key").exists()


def test_plaintext_and_empty_pass_through():
    assert credential_vault.decrypt("legacy") == "legacy"
    assert credential_vault.encrypt("") == ""
    token = credential_vault.encrypt("pw")
    assert credential_vault.encrypt(token) == token


def test_env_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("TELETRADER_SECRET_KEY", Fernet.generate_key().decode())
    token = credential_vault.encrypt("pw")
    assert credential_vault.decrypt(token) == "pw"


def test_wrong_key_raises(monkeypatch):
    token = credential_vault.encrypt("pw")
    monkeypatch.setenv("TELETRADER_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        credential_vault.decrypt(token)


def test_db_stores_ciphertext_and_returns_plaintext(tmp_path):
    db_path = tmp_path / "users.db"
    db.init_db(db_path)
    uid = db.add_user(db.UserAccount(first_name="A", last_name="B", mt5_account=111,
                                     mt5_password="s3cret", mt5_server="S", mt5_path="p"),
                      db_path)
    assert credential_vault.is_encrypted(_raw_password(db_path, 111))
    user = db.get_user(uid, db_path)
    assert user.mt5_password == "s3cret"

    user.mt5_password = "changed"
    db.update_user(user, db_path)
    assert "changed" not in _raw_password(db_path, 111)
    assert db.get_user(uid, db_path).mt5_password == "changed"


def test_init_db_encrypts_legacy_plaintext_rows(tmp_path):
    db_path = tmp_path / "users.db"
    db.init_db(db_path)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO users (first_name,last_name,mt5_account,mt5_password,mt5_server,mt5_path)"
        " VALUES ('A','B',222,'plain-pw','S','p')"
    )
    con.commit()
    con.close()

    db.init_db(db_path)

    assert credential_vault.is_encrypted(_raw_password(db_path, 222))
    assert db.get_enabled_users(db_path)[0].mt5_password == "plain-pw"
