# tradebot/application/parser.py

"""
Pure‐python parser - no telegram dependency - easy to unit‐test.
"""
import re
from typing import Sequence, List
from tradebot.domain.models import Signal, Target
from tradebot.domain.ports import SignalParserPort
from loguru import logger

# FIXED: use \s* for whitespace, not \\s*
_FIRST_LINE = re.compile(r".*?([A-Z]{3,6})\s*-\s*(BUY|SELL)\s*(NOW|LIMIT|MARKET)\b.*", re.I)
_FLOAT      = r"[0-9]+(?:\.[0-9]+)?"

class BasicSignalParser(SignalParserPort):
    def parse(self, message: str) -> Signal | None:
        try:
            lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
            first = _FIRST_LINE.search(lines[0])
            if not first:
                return None

            symbol = first.group(1).upper()
            side = first.group(2).lower()
            ot = first.group(3).lower() if first.group(3) else "market"
            order_type = "limit" if ot == "limit" else "market"

            entry_text = _find(lines, "Entry")
            entry = float(re.search(_FLOAT, entry_text).group())

            sl = None
            try:
                sl_text = _find(lines, "Stoploss")
                sl = float(re.search(_FLOAT, sl_text).group())
            except StopIteration:
                sl = None

            tgt_block = _collect_targets(lines)
            targets = [Target(float(t)) for t in tgt_block]

            comment = ""
            trader_lines = [ln for ln in lines if "Trader:" in ln]
            if trader_lines:
                raw = trader_lines[-1]
                part = raw.split("Trader:", 1)[1]
                cleaned = re.sub(r"[^A-Za-z0-9\+ ]", "", part)
                comment = cleaned.strip()

            return Signal(symbol, side, order_type,
                          entry, targets, sl, comment, message)
        
        except Exception as exc:
            logger.error("parser error: {}", exc)
            return None

def _find(lines: list[str], keyword: str) -> str:
    return next(ln for ln in lines if keyword.lower() in ln.lower())

def _collect_targets(lines: list[str]) -> List[str]:
    """
    Collect lines after 'Targets' header until 'Stoploss', extracting any numeric value.
    """
    idx = next(i for i, l in enumerate(lines) if "targets" in l.lower())
    targets: List[str] = []
    for line in lines[idx+1:]:
        if "stoploss" in line.lower():
            break
        m = re.search(_FLOAT, line)
        if m:
            targets.append(m.group())
    return targets
