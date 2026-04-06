from __future__ import annotations

import datetime


class DailyLossGuard:
    """Tracks intraday P&L and blocks new trades once daily loss limit is reached.

    Resets automatically at the start of each new calendar day (local time).
    """

    def __init__(self, max_daily_loss_pct: float = 5.0):
        self.max_pct = max_daily_loss_pct
        self._day_start_balance: float = 0.0
        self._current_day: int = -1

    def update(self, current_balance: float) -> None:
        """Call once per scan loop iteration to refresh the day-start baseline."""
        today = datetime.date.today().toordinal()
        if today != self._current_day:
            self._current_day = today
            self._day_start_balance = current_balance

    def is_blocked(self, current_balance: float) -> bool:
        """Return True if daily loss has reached or exceeded the configured limit."""
        if self._day_start_balance <= 0:
            return False
        return self.daily_loss_pct(current_balance) >= self.max_pct

    def daily_loss_pct(self, current_balance: float) -> float:
        """Return today's loss as a positive percentage (0.0 if profitable or not yet set)."""
        if self._day_start_balance <= 0:
            return 0.0
        return max(0.0, (self._day_start_balance - current_balance) / self._day_start_balance * 100)
