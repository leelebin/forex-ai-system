from __future__ import annotations

from collections import defaultdict
from typing import DefaultDict, Optional, Tuple


class LossGuard:
    """
    连续亏损防护：
    - key: (symbol, direction, trend_id)
    - 连续2次亏损，禁止该方向交易
    """

    def __init__(self, threshold: int = 2) -> None:
        self.threshold = max(1, int(threshold))
        self._loss_streak: DefaultDict[Tuple[str, str, str], int] = defaultdict(int)

    def _key(self, symbol: str, direction: str, trend_id: Optional[str] = None) -> Tuple[str, str, str]:
        return (symbol.upper(), direction.upper(), trend_id or "*")

    def record_result(
        self,
        symbol: str,
        direction: str,
        pnl: float,
        trend_id: Optional[str] = None,
    ) -> None:
        key = self._key(symbol, direction, trend_id)

        if pnl < 0:
            self._loss_streak[key] += 1
        else:
            self._loss_streak[key] = 0

    def is_blocked(self, symbol: str, direction: str, trend_id: Optional[str] = None) -> dict:
        key_exact = self._key(symbol, direction, trend_id)
        key_global = self._key(symbol, direction, None)

        streak = max(self._loss_streak.get(key_exact, 0), self._loss_streak.get(key_global, 0))
        blocked = streak >= self.threshold

        return {
            "blocked": blocked,
            "reason": f"loss_streak={streak}",
            "streak": streak,
            "threshold": self.threshold,
        }
