from __future__ import annotations


class PnLEngine:
    """使用内存状态记录权益峰值与回撤。"""

    def __init__(self) -> None:
        self.peak_equity = 0.0
        self.latest_equity = 0.0

    def update(self, equity: float) -> dict:
        equity = max(0.0, float(equity or 0.0))
        self.latest_equity = equity

        is_new_peak = equity >= self.peak_equity
        if is_new_peak:
            self.peak_equity = equity

        return {
            "equity": equity,
            "peak_equity": self.peak_equity,
            "drawdown": self.get_drawdown(),
            "is_new_peak": is_new_peak,
        }

    def get_drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        drawdown = (self.peak_equity - self.latest_equity) / self.peak_equity * 100
        return max(0.0, round(drawdown, 4))
