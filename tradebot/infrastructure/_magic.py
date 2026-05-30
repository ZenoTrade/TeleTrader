# tradebot\infrastructure\_magic.py

import time

class MagicGen:
    """
    Magic number from current UTC timestamp (μs precision).

    Using time.time_ns()//1_000 ensures:
      • Monotonic increase across restarts
      • Resolution to the microsecond
    """
    @staticmethod
    def generate() -> int:
        # microseconds since epoch, truncated to 32-bit
        return (time.time_ns() // 1_000) & 0xFFFFFFFF