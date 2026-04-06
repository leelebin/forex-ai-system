from __future__ import annotations

import datetime
from typing import List


class DailyLossGuard:
    """Tracks intraday P&L and blocks new trades once the daily loss limit is reached.

    Supports balance-tiered limits via *tiers* parameter so that a turbo growth
    account allows a higher daily loss percentage than a conservative account.

    Tier format (list of dicts, evaluated top-to-bottom):
        [{"balance_max": 2000, "max_pct": 20.0},
         {"balance_max": 5000, "max_pct": 10.0},
         {"balance_max": null,  "max_pct": 5.0}]   <- null means "no upper limit"

    If *tiers* is empty or None, falls back to *max_daily_loss_pct* (flat limit).
    Resets automatically at the start of each new calendar day (local time).
    """

    def __init__(
        self,
        max_daily_loss_pct: float = 5.0,
        tiers: List[dict] | None = None,
    ):
        self.max_pct = max_daily_loss_pct
        self.tiers = tiers or []
        self._day_start_balance: float = 0.0
        self._current_day: int = -1

    def _effective_max_pct(self, current_balance: float) -> float:
        """Return the applicable daily loss % cap for *current_balance*."""
        for tier in self.tiers:
            bal_max = tier.get("balance_max")
            if bal_max is None or current_balance <= float(bal_max):
                return float(tier.get("max_pct", self.max_pct))
        return self.max_pct

    def update(self, current_balance: float) -> None:
        """Call once per scan loop to refresh the day-start baseline."""
        today = datetime.date.today().toordinal()
        if today != self._current_day:
            self._current_day = today
            self._day_start_balance = current_balance

    def is_blocked(self, current_balance: float) -> bool:
        """Return True if today's loss has reached the tiered cap."""
        if self._day_start_balance <= 0:
            return False
        return self.daily_loss_pct(current_balance) >= self._effective_max_pct(current_balance)

    def daily_loss_pct(self, current_balance: float) -> float:
        """Today's loss as a positive % (0.0 when profitable or not yet initialised)."""
        if self._day_start_balance <= 0:
            return 0.0
        return max(
            0.0,
            (self._day_start_balance - current_balance) / self._day_start_balance * 100,
        )

    @property
    def effective_limit_pct(self) -> float:
        """Convenience: return the cap that would apply at the current day-start balance."""
        return self._effective_max_pct(self._day_start_balance)
