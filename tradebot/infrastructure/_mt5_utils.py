# tradebot/infrastructure/_mt5_utils.py
import MetaTrader5 as mt5
from loguru import logger
import os
import time

def ensure_mt5(path: str | None = None):
    """
    Ensure MT5 is initialized.

    - If `path` is provided and a different terminal is active, reinitialize with that path.
    - If `path` is None and MT5 is not initialized, initialize with default terminal.
    - If already initialized with the requested path, do nothing.
    """

    term_info = mt5.terminal_info()

    # If already initialized, keep the current terminal (don't switch)
    if term_info:
        logger.debug("MT5 already initialized; keeping current terminal")
        return

    # If not initialized at all
    if term_info is None:
        # User-requested flow: try default initialize() first
        if mt5.initialize():
            ti = mt5.terminal_info()
            logger.info(f"MT5 initialized with default terminal: {ti.path if ti else 'unknown'}")
            return

        # Default failed; if path is provided, try path-based init with retries
        if path:
            _initialize_with_retries(path)
            logger.info(f"MT5 initialized with terminal at {path}")
            return

        # No path and default failed -> error
        logger.error("Couldn't initialize MT5 (default init failed and no path provided)")
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")

    # Already initialized and no path specified -> do nothing
    logger.info("MT5 already initialized (no path change requested)")


def _initialize_with_retries(path: str, attempts: int = 3, delay_sec: float = 2.0) -> None:
    """Attempt to initialize MT5 with the given path, retrying on IPC timeout.

    Raises RuntimeError on final failure.
    """
    if not os.path.exists(path):
        raise RuntimeError(f"MT5 path does not exist: {path}")

    for i in range(1, attempts + 1):
        if mt5.initialize(path=path):
            return
        err = mt5.last_error()
        logger.warning(f"MT5.initialize failed (attempt {i}/{attempts}) for {path}: {err}")
        # If IPC timeout, give MT5 a moment to come up
        time.sleep(delay_sec)

    raise RuntimeError(f"MT5 initialization failed after {attempts} attempts: {mt5.last_error()}")



