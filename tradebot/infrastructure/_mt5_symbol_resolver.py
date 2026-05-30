# tradebot\infrastructure\_mt5_symbol_resolver.py

"""
Resolve broker-specific symbol names (prefix/suffix) and cache them.
"""

import re
from typing import Dict
import MetaTrader5 as mt5
from loguru import logger
from ._mt5_utils import ensure_mt5


class SymbolResolver:
    """
    Fast symbol lookup:

        resolver = SymbolResolver()
        full_sym = resolver.resolve("XAUUSD")   # e.g. returns "XAUUSDb"
    """

    def __init__(self, path: str):
        ensure_mt5(path)
        self._all = [s.name for s in mt5.symbols_get()]
        self._cache: Dict[str, str] = {}

    # -----------------------------------------------------------------
    def resolve(self, base: str) -> str:
        """Return the first broker symbol that contains `base` (case-sens)."""
        if base in self._cache:
            return self._cache[base]

        # 1) Exact match
        if base in self._all:
            self._cache[base] = base
            return base

        # 2) Fast substring filter (XAUUSD -> XAUUSDb or bXAUUSD etc.)
        cands = [s for s in self._all if base in s]
        if not cands:
            # 3) Regex fallback  [a-z]* + base + [a-z]*
            pat = re.compile(rf"[a-z]*{re.escape(base)}[a-z]*", re.I)
            cands = [s for s in self._all if pat.fullmatch(s)]

        if not cands:
            raise ValueError(f"Symbol '{base}' not found in broker list")

        best = min(cands, key=len)        # shortest readable variant
        self._cache[base] = best
        logger.info("Resolved %s -> %s", base, best)
        return best
