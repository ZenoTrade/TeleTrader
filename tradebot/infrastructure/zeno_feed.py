"""Poll the Zeno signals feed instead of reading a Telegram channel directly.

Customers cannot add their own bot to Zeno's private channel, so the Zeno bot
relays each channel post to the license server and the Copier polls it here,
authenticated by the license key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from loguru import logger


class ZenoSignalFeed:
    def __init__(self, server_url: str, license_key: str,
                 handle_text: Callable[[str], Awaitable[None]],
                 interval_sec: float = 3.0, http: httpx.AsyncClient | None = None) -> None:
        self.url = server_url.rstrip("/") + "/v1/signals"
        self.headers = {"X-License-Key": license_key.strip()}
        self.handle_text = handle_text
        self.interval = interval_sec
        self._http = http or httpx.AsyncClient(timeout=15)
        self.cursor = 0
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> int:
        """Fetch new signals and handle them in order. Returns how many were handled."""
        resp = await self._http.get(self.url, params={"after": self.cursor}, headers=self.headers)
        if resp.status_code == 403:
            logger.error(f"Signals feed refused: {resp.json().get('detail')}")
            return 0
        resp.raise_for_status()
        data = resp.json()
        for sig in data["signals"]:
            try:
                await self.handle_text(sig["text"])
            except Exception:
                logger.exception(f"Failed to handle feed signal {sig['id']}")
        self.cursor = data["cursor"]
        return len(data["signals"])

    async def run(self) -> None:
        logger.info("Polling the Zeno signals feed")
        while True:
            try:
                await self.poll_once()
            except httpx.HTTPError as exc:
                logger.warning(f"Signals feed error: {exc}")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
