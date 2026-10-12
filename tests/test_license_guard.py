import asyncio
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tradebot.infrastructure.license_guard import LicenseGuard, verify_offline
from tradebot.infrastructure.zeno_feed import ZenoSignalFeed


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    body = json.dumps({"lid": "L1", "plan": "bundle-monthly", "prd": ["copier", "signals"],
                       "acc": 1, "iat": 1}).encode()
    key = f"ZENO-{_b64e(body)}.{_b64e(priv.sign(body))}"
    return key, pub


def _guard(key, pub, tmp_path, handler):
    return LicenseGuard(key, "https://ls", pub, cache_path=tmp_path / "cache.json",
                        http=httpx.Client(transport=httpx.MockTransport(handler)))


def _ok(request):
    return httpx.Response(200, json={"valid": True, "products": ["copier", "signals"],
                                     "expires_at": "2026-11-12T00:00:00+00:00"})


def test_owner_build_is_unlicensed_and_always_active(tmp_path):
    g = LicenseGuard("", "https://ls", "", cache_path=tmp_path / "c.json")
    assert not g.enabled
    assert g.check([1]).active
    assert g.has_product("copier")


def test_offline_verification(keypair):
    key, pub = keypair
    assert verify_offline(key, pub)["prd"] == ["copier", "signals"]
    with pytest.raises(ValueError):
        verify_offline(key[:-4] + "AAAA", pub)
    with pytest.raises(ValueError):
        verify_offline("garbage", pub)


def test_valid_license_and_account_limit(keypair, tmp_path):
    key, pub = keypair
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["mt5_account"])
        if body["mt5_account"] == 2:
            return httpx.Response(200, json={"valid": False, "reason": "account_limit"})
        return _ok(request)

    g = _guard(key, pub, tmp_path, handler)
    assert g.check([1]).active and g.has_product("copier")
    state = g.check([1, 2])
    assert not state.active and state.reason.startswith("account_limit")
    assert seen == [1, 1, 2]


def test_forged_key_never_reaches_server(keypair, tmp_path):
    key, pub = keypair
    calls = []
    g = _guard(key[:-4] + "AAAA", pub, tmp_path, lambda r: calls.append(r) or _ok(r))
    assert g.check([1]).reason == "invalid_key"
    assert calls == []


def test_offline_grace_uses_recent_cache(keypair, tmp_path):
    key, pub = keypair
    assert _guard(key, pub, tmp_path, _ok).check([1], now=1000).active

    def down(request):
        raise httpx.ConnectError("down")

    g = _guard(key, pub, tmp_path, down)
    assert g.check([1], now=1000 + 3600).reason == "offline_grace"
    assert g.check([1], now=1000 + 73 * 3600).reason == "server_unreachable"


def test_feed_handles_new_signals_in_order():
    pages = [
        {"cursor": 5, "signals": []},
        {"cursor": 7, "signals": [{"id": 6, "text": "A"}, {"id": 7, "text": "B"}]},
    ]
    afters = []

    def handler(request):
        afters.append(int(request.url.params["after"]))
        assert request.headers["X-License-Key"] == "ZENO-k"
        return httpx.Response(200, json=pages.pop(0))

    handled = []

    async def handle(text):
        handled.append(text)

    feed = ZenoSignalFeed("https://ls", "ZENO-k", handle,
                          http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(feed.poll_once())
    asyncio.run(feed.poll_once())
    assert afters == [0, 5]
    assert handled == ["A", "B"]
    assert feed.cursor == 7
