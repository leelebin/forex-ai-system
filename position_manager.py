from __future__ import annotations

import MetaTrader5 as mt5


class PositionManager:
    """持仓准入控制：主仓唯一、加仓约束、总仓位上限。"""

    def can_open(
        self,
        symbol: str,
        direction: str,
        trend_state: str,
        account_mode: str,
        max_positions: int,
        allow_pyramiding: bool,
        volatility_regime: str = "NORMAL",
    ) -> dict:
        positions = mt5.positions_get()
        positions = list(positions) if positions else []

        if len(positions) >= int(max_positions):
            return {"allowed": False, "reason": "max_positions_reached"}

        symbol_positions = [p for p in positions if p.symbol == symbol]
        if not symbol_positions:
            return {"allowed": True, "reason": "no_position_on_symbol"}

        # 每个symbol只允许一个主仓；已有仓位时仅在“允许加仓 + 强趋势 + 非防御模式”可继续
        if account_mode == "DEFENSIVE":
            return {"allowed": False, "reason": "defensive_mode_no_pyramiding"}

        if not allow_pyramiding:
            return {"allowed": False, "reason": "pyramiding_disabled"}

        if volatility_regime == "HIGH_VOL":
            return {"allowed": False, "reason": "high_vol_no_pyramiding"}

        if trend_state != "STRONG":
            return {"allowed": False, "reason": "pyramiding_requires_strong_trend"}

        # 仅允许盈利加仓
        if any(float(getattr(p, "profit", 0.0) or 0.0) <= 0 for p in symbol_positions):
            return {"allowed": False, "reason": "pyramiding_requires_profit"}

        # 如已有反向仓位，不允许同symbol反向开新单
        side_mismatch = any(
            (p.type == mt5.ORDER_TYPE_BUY and direction == "SELL")
            or (p.type == mt5.ORDER_TYPE_SELL and direction == "BUY")
            for p in symbol_positions
        )
        if side_mismatch:
            return {"allowed": False, "reason": "opposite_position_exists"}

        return {"allowed": True, "reason": "pyramiding_allowed"}

    def count_positions(self) -> int:
        positions = mt5.positions_get()
        return len(positions) if positions else 0
