import base64
import json
import sys
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

sys.modules.setdefault("tkinter", MagicMock())
sys.modules.setdefault("tkinter.ttk", MagicMock())
sys.modules.setdefault("tkinter.messagebox", MagicMock())

import setup_wizard as sw  # noqa: E402


def test_chat_id_from_updates_picks_latest_private_chat():
    updates = [
        {"message": {"chat": {"id": 1, "type": "private"}}},
        {"message": {"chat": {"id": -100, "type": "group"}}},
        {"message": {"chat": {"id": 2, "type": "private"}}},
        {"my_chat_member": {}},
    ]
    assert sw.chat_id_from_updates(updates) == 2
    assert sw.chat_id_from_updates([]) is None


def test_render_env_keeps_custom_lines_and_replaces_ours():
    out = sw.render_env(license_key=" ZENO-a.b ", server="https://api.x/", bot_token="1:t",
                        chat_id=42, signal_source="zeno", risk_per_trade=0.01,
                        existing="MAX_SLIPPAGE=30\nSIGNAL_CHAT_ID=1\n")
    lines = out.splitlines()
    assert "MAX_SLIPPAGE=30" in lines
    assert "SIGNAL_CHAT_ID=42" in lines and "SIGNAL_CHAT_ID=1" not in lines
    assert "ZENO_LICENSE_KEY=ZENO-a.b" in lines
    assert "ZENO_LICENSE_SERVER=https://api.x" in lines
    assert "RISK_PER_TRADE=0.01" in lines


def test_products_in_key():
    priv = Ed25519PrivateKey.generate()
    pub = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    body = json.dumps({"lid": "x", "plan": "p", "prd": ["copier", "signals"], "acc": 1, "iat": 1}).encode()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    key = f"ZENO-{b64(body)}.{b64(priv.sign(body))}"
    assert sw.products_in_key(key, pub) == ["copier", "signals"]
    assert sw.products_in_key(key, "") == []
    with pytest.raises(ValueError):
        sw.products_in_key("ZENO-bad.key", pub)
