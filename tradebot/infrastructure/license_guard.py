"""Zeno license checks for customer builds of the Copier.

Licensing is on only when a public key is present (``EMBEDDED_PUBLIC_KEY``,
filled in by the release build, or ``ZENO_LICENSE_PUBLIC_KEY``). Owner builds
leave both empty and run unlicensed.

A key is verified offline against the public key, then confirmed with the
license server for each enabled MT5 account. If the server cannot be reached,
the last successful check keeps the Copier running for ``grace_hours``.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from loguru import logger

# Set by the release build. Empty means an owner build with licensing off.
EMBEDDED_PUBLIC_KEY = ""

KEY_PREFIX = "ZENO-"


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_offline(key: str, public_b64: str) -> dict:
    """Return the key's payload if its signature is valid; raise ValueError if not."""
    key = key.strip()
    if not key.startswith(KEY_PREFIX) or "." not in key:
        raise ValueError("Malformed license key")
    body_b64, sig_b64 = key[len(KEY_PREFIX):].split(".", 1)
    try:
        body = _b64d(body_b64)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(_b64d(sig_b64), body)
        return json.loads(body)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Invalid license key") from exc


@dataclass
class LicenseState:
    active: bool
    reason: str = ""
    products: list[str] = field(default_factory=list)
    expires_at: str = ""
    checked_at: float = 0.0


class LicenseGuard:
    def __init__(self, key: str, server_url: str, public_key: str,
                 cache_path: str | Path = "license_cache.json", grace_hours: float = 72,
                 recheck_hours: float = 6, http: httpx.Client | None = None) -> None:
        self.key = key.strip()
        self.server_url = server_url.rstrip("/")
        self.public_key = public_key.strip()
        self.cache_path = Path(cache_path)
        self.grace_seconds = grace_hours * 3600
        self.recheck_seconds = recheck_hours * 3600
        self._http = http or httpx.Client(timeout=15)
        self._state = LicenseState(active=not self.enabled, reason="" if self.enabled else "unlicensed")
        self._accounts: list[int] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.public_key)

    @property
    def state(self) -> LicenseState:
        return self._state

    def is_active(self) -> bool:
        return self._state.active

    def has_product(self, product: str) -> bool:
        return not self.enabled or (self._state.active and product in self._state.products)

    # ------------------------------------------------------------------
    def _machine_id(self) -> str:
        return hex(uuid.getnode())

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self, state: LicenseState) -> None:
        try:
            self.cache_path.write_text(json.dumps({
                "key_tail": self.key[-16:], "checked_at": state.checked_at,
                "products": state.products, "expires_at": state.expires_at}))
        except OSError as exc:
            logger.warning(f"Could not write license cache: {exc}")

    def check(self, mt5_accounts: list[int] | None = None, now: float | None = None) -> LicenseState:
        """Validate the key for every account. Updates and returns the state."""
        if not self.enabled:
            return self._state
        now = now or time.time()
        if mt5_accounts is not None:
            self._accounts = list(mt5_accounts)

        if not self.key:
            self._state = LicenseState(False, "missing_key")
            return self._state
        try:
            payload = verify_offline(self.key, self.public_key)
        except ValueError:
            self._state = LicenseState(False, "invalid_key")
            return self._state

        try:
            last: dict = {}
            for account in self._accounts or [None]:
                resp = self._http.post(f"{self.server_url}/v1/licenses/validate", json={
                    "key": self.key, "mt5_account": account, "machine_id": self._machine_id()})
                resp.raise_for_status()
                last = resp.json()
                if not last.get("valid"):
                    reason = last.get("reason", "rejected")
                    if reason == "account_limit":
                        reason = f"account_limit: {account} exceeds the plan's account count"
                    self._state = LicenseState(False, reason, last.get("products", []),
                                               last.get("expires_at", ""), now)
                    return self._state
            self._state = LicenseState(True, "", last.get("products", payload.get("prd", [])),
                                       last.get("expires_at", ""), now)
            self._save_cache(self._state)
        except httpx.HTTPError as exc:
            cache = self._load_cache()
            fresh = (cache.get("key_tail") == self.key[-16:]
                     and now - float(cache.get("checked_at", 0)) < self.grace_seconds)
            if fresh:
                logger.warning(f"License server unreachable ({exc}); using cached check")
                self._state = LicenseState(True, "offline_grace", cache.get("products", []),
                                           cache.get("expires_at", ""), float(cache["checked_at"]))
            else:
                self._state = LicenseState(False, "server_unreachable")
        return self._state

    # ------------------------------------------------------------------
    def start(self, on_change=None) -> None:
        """Re-check in the background; call ``on_change(state)`` when activity flips."""
        if not self.enabled or self._thread:
            return

        def loop() -> None:
            while not self._stop.wait(self.recheck_seconds):
                before = self._state.active
                state = self.check()
                if state.active != before and on_change:
                    on_change(state)

        self._thread = threading.Thread(target=loop, name="license-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
