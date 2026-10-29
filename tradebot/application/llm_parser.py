# tradebot/application/llm_parser.py

"""
LLM fallback parser, ported from zeno-signals.

The basic parser only reads the Zeno channel format. Channels customers
follow write signals in many shapes, so when the basic parser gives up an
LLM reads the message and returns JSON, which is checked here before it
becomes a Signal. A message is never traded unless it has a symbol, a side,
an entry, a stop loss and at least one target.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from loguru import logger

from tradebot.domain.models import Signal, Target
from tradebot.domain.ports import SignalParserPort

# Only messages that look like a trade idea are sent to the LLM.
_ADMISSION = re.compile(r"\b(buy|sell|long|short)\b", re.I)
_HAS_NUMBER = re.compile(r"\d")
_SYMBOL = re.compile(r"^[A-Z0-9.]{3,12}$")

Complete = Callable[[str], str]


class LlmSignalParser(SignalParserPort):
    """Parse free-form signal messages with an LLM that answers in JSON."""

    def __init__(self, complete: Complete, min_confidence: float = 0.7):
        self._complete = complete
        self._min_confidence = min_confidence

    def parse(self, message: str) -> Signal | None:
        if not (_ADMISSION.search(message) and _HAS_NUMBER.search(message)):
            return None
        try:
            payload = json.loads(self._complete(build_prompt(message)))
        except Exception as exc:
            logger.warning("LLM parser failed: {}", exc)
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return signal_from_payload(payload, message, self._min_confidence)
        except (KeyError, TypeError, ValueError) as exc:
            logger.info("LLM output rejected: {}", exc)
            return None


class HybridSignalParser(SignalParserPort):
    """Try the exact parser first; fall back to the LLM only when it fails."""

    def __init__(self, primary: SignalParserPort, fallback: SignalParserPort):
        self._primary = primary
        self._fallback = fallback

    def parse(self, message: str) -> Signal | None:
        return self._primary.parse(message) or self._fallback.parse(message)


def build_prompt(message: str) -> str:
    return f"""
You are a trading-signal parser.

Decide whether the message is a new, actionable trading signal. Reject
education, marketing, news, commentary, results of closed trades, updates to
earlier trades, and anything unclear.

Return only JSON. Do not include markdown.

For rejected messages:
{{"action": "reject", "confidence": 0.0, "reason": "short reason"}}

For accepted messages:
{{
  "action": "accept",
  "confidence": 0.0,
  "symbol": "XAUUSD",
  "side": "buy|sell",
  "order_type": "market|limit",
  "entry": {{"min": 1.0, "max": null}},
  "stop_loss": 1.0,
  "take_profits": [1.0],
  "trader": null
}}

Use "market" when the message says now, market or gives no entry condition.
"trader" is the analyst's name if the message names one.

Message:
{message}
""".strip()


def signal_from_payload(payload: dict[str, Any], raw: str,
                        min_confidence: float = 0.7) -> Signal | None:
    if str(payload.get("action", "")).lower() != "accept":
        return None
    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0))))
    if confidence < min_confidence:
        logger.info("LLM confidence {:.2f} below {:.2f}", confidence, min_confidence)
        return None

    symbol = re.sub(r"[^A-Z0-9.]", "", str(payload["symbol"]).upper())
    if not _SYMBOL.match(symbol):
        raise ValueError(f"bad symbol {payload['symbol']!r}")

    side = str(payload["side"]).lower()
    side = {"long": "buy", "short": "sell"}.get(side, side)
    if side not in ("buy", "sell"):
        raise ValueError(f"bad side {payload['side']!r}")

    order_type = "limit" if str(payload.get("order_type", "")).lower() == "limit" else "market"

    entry_obj = payload["entry"]
    lo = float(entry_obj["min"])
    hi = entry_obj.get("max")
    entry = (lo + float(hi)) / 2 if hi not in (None, "") else lo

    if payload.get("stop_loss") in (None, ""):
        raise ValueError("no stop loss")
    stop_loss = float(payload["stop_loss"])

    targets = [float(t) for t in payload.get("take_profits") or []]
    if not targets:
        raise ValueError("no take profit")

    # The stop must sit on the losing side and every target on the winning side.
    if side == "buy":
        ok = stop_loss < entry and all(t > entry for t in targets)
    else:
        ok = stop_loss > entry and all(t < entry for t in targets)
    if not ok:
        raise ValueError("stop or targets on the wrong side of entry")

    trader = re.sub(r"[^A-Za-z0-9+ ]", "", str(payload.get("trader") or "")).strip()
    return Signal(symbol, side, order_type, entry,
                  [Target(t) for t in targets], stop_loss, trader, raw)


class OpenAiCompatibleClient:
    """Chat-completions client for any OpenAI-compatible API (OpenAI, DeepSeek, local)."""

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 30):
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout

    def __call__(self, prompt: str) -> str:
        import httpx

        resp = httpx.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])
