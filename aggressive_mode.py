from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

import MetaTrader5 as mt5


def get_risk_percent(balance: float) -> float:
    """
    分阶段风险系统（成长型激进模式）:
    - TURBO   : balance <= 1000  -> 8%   ($500 起步阶段，最高激进)
    - HIGH    : balance <= 2000  -> 6%   (冲刺至目标$2000)
    - GROWTH  : balance <= 5000  -> 3.5% (过渡阶段，开始保护)
    - MODERATE: balance > 5000   -> 1.5% (保守模式)
    """
    balance = float(balance or 0.0)
    if balance <= 1_000:
        return 8.0
    if balance <= 2_000:
        return 6.0
    if balance <= 5_000:
        return 3.5
    return 1.5


def should_add_position(position: dict) -> bool:
    """
    盈利加仓条件:
    - 盈利状态
    - 浮盈达到下一阶段R阈值
    - 最多两次加仓
    """
    if float(position.get("profit", 0.0) or 0.0) <= 0:
        return False

    add_count = int(position.get("add_count", 0) or 0)
    if add_count >= 2:
        return False

    r_multiple = float(position.get("r_multiple", 0.0) or 0.0)
    next_trigger = 1.0 if add_count == 0 else 2.0
    return r_multiple > next_trigger


def adjust_risk_by_volatility(atr: float, atr_avg: float) -> dict:
    """
    高波动选择性放行:
    - atr > 1.5 * atr_avg: 允许交易，但手数减半
    - atr > 2.0 * atr_avg: 禁止交易
    """
    atr = float(atr or 0.0)
    atr_avg = float(atr_avg or 0.0)
    if atr_avg <= 0:
        return {"allow_trade": True, "lot_multiplier": 1.0, "atr_level": "normal"}

    ratio = atr / atr_avg
    if ratio > 2.0:
        return {"allow_trade": False, "lot_multiplier": 0.0, "atr_level": "high"}
    if ratio > 1.5:
        return {"allow_trade": True, "lot_multiplier": 0.5, "atr_level": "high"}
    if ratio < 0.8:
        return {"allow_trade": True, "lot_multiplier": 1.0, "atr_level": "low"}
    return {"allow_trade": True, "lot_multiplier": 1.0, "atr_level": "normal"}


def calculate_dynamic_lot(symbol: str, balance: float, sl_pips: float) -> float:
    """
    lot = (balance * risk%) / (SL_pips * pip_value)
    这里 risk% 采用 Aggressive 分段风险，pip_value 从 MT5 symbol_info 获取。
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0
    pip_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    if pip_value <= 0:
        return 0.0
    risk_pct = get_risk_percent(balance)
    sl_pips = float(sl_pips or 0.0)
    if sl_pips <= 0:
        return 0.0
    return max(0.0, (float(balance or 0.0) * (risk_pct / 100.0)) / (sl_pips * pip_value))


@dataclass
class DrawdownControl:
    allow_trade: bool
    risk_multiplier: float
    drawdown_pct: float


class AggressiveModeController:
    def __init__(self, cfg: dict) -> None:
        mode_cfg = cfg.get("aggressive_mode", {})
        self.enabled = bool(mode_cfg.get("enabled", False))
        self.whitelist = set(mode_cfg.get("whitelist", ["XBRUSD", "AUDJPY", "CHFJPY", "EURUSD"]))
        self.blacklist = set(mode_cfg.get("blacklist", ["BTCUSD", "XAGUSD", "US30", "NAS100"]))

        self.initial_balance = float(mode_cfg.get("initial_balance", cfg.get("backtest", {}).get("starting_balance", 500)))
        self.drawdown_soft = float(mode_cfg.get("drawdown_soft", 15.0))
        self.drawdown_hard = float(mode_cfg.get("drawdown_hard", 25.0))

        self.symbol_cooldown_sec = int(mode_cfg.get("symbol_cooldown_sec", 2 * 3600))
        self.global_pause_sec = int(mode_cfg.get("global_pause_sec", 3 * 3600))

        self.loss_streak_symbol: Dict[str, int] = defaultdict(int)
        self.loss_streak_global = 0
        self.symbol_block_until: Dict[str, float] = {}
        self.global_pause_until = 0.0

        self.trade_outcomes: Deque[float] = deque(maxlen=3)
        self.position_add_count: Dict[int, int] = defaultdict(int)
        self.locked_profit_mode = False
        self.ultra_low_risk_mode = False

    def is_symbol_allowed(self, symbol: str) -> bool:
        if symbol in self.blacklist:
            return False
        if self.whitelist and symbol not in self.whitelist:
            return False
        return True

    def can_trade_now(self, symbol: str, now_ts: Optional[float] = None) -> dict:
        now_ts = now_ts if now_ts is not None else time.time()
        if now_ts < self.global_pause_until:
            return {"blocked": True, "reason": "global_loss_pause"}

        blocked_until = self.symbol_block_until.get(symbol, 0.0)
        if now_ts < blocked_until:
            return {"blocked": True, "reason": "symbol_loss_pause"}

        return {"blocked": False, "reason": "ok"}

    def record_trade_result(self, symbol: str, pnl: float, now_ts: Optional[float] = None) -> None:
        if not self.enabled:
            return
        now_ts = now_ts if now_ts is not None else time.time()
        pnl = float(pnl or 0.0)
        self.trade_outcomes.append(pnl)

        if pnl < 0:
            self.loss_streak_symbol[symbol] += 1
            self.loss_streak_global += 1
            if self.loss_streak_symbol[symbol] >= 2:
                self.symbol_block_until[symbol] = now_ts + self.symbol_cooldown_sec
        else:
            self.loss_streak_symbol[symbol] = 0
            self.loss_streak_global = 0

        if self.loss_streak_global >= 4:
            self.global_pause_until = now_ts + self.global_pause_sec

    def check_drawdown_control(self, balance: float, peak_balance: float) -> DrawdownControl:
        balance = float(balance or 0.0)
        peak_balance = max(float(peak_balance or 0.0), 1e-9)
        drawdown_pct = max(0.0, (peak_balance - balance) / peak_balance * 100.0)

        if drawdown_pct > self.drawdown_hard:
            return DrawdownControl(allow_trade=False, risk_multiplier=0.0, drawdown_pct=drawdown_pct)
        if drawdown_pct > self.drawdown_soft:
            return DrawdownControl(allow_trade=True, risk_multiplier=0.5, drawdown_pct=drawdown_pct)
        return DrawdownControl(allow_trade=True, risk_multiplier=1.0, drawdown_pct=drawdown_pct)

    def get_profit_protection_multiplier(self, balance: float) -> float:
        """
        利润保护乘数（在阶段风险基础上再叠加一层保护）:
        - 余额未超过 $5000 (< +900% from $500): 全速前进，不限制
        - 余额到达 $5000 (+900%): 风险 × 0.7
        - 余额超过 $10000 (+1900%): 风险 × 0.5（彻底进入保守模式）
        关键变化：不在$2000时触发保护，避免阻碍冲刺目标。
        """
        base = max(self.initial_balance, 1e-9)
        growth = (float(balance or 0.0) - base) / base
        if growth >= 19.0:  # balance ~= $10 000 (from $500)
            self.ultra_low_risk_mode = True
            return 0.5
        if growth >= 9.0:   # balance ~= $5 000 (from $500)
            self.locked_profit_mode = True
            return 0.7
        # TURBO / GROWTH 阶段: 不做额外削减
        self.ultra_low_risk_mode = False
        self.locked_profit_mode = False
        return 1.0

    def get_cycle_lot_multiplier(self) -> float:
        if len(self.trade_outcomes) < 3:
            return 1.0
        wins = sum(1 for x in self.trade_outcomes if x > 0)
        losses = sum(1 for x in self.trade_outcomes if x < 0)
        if wins >= 2:
            return 1.2
        if losses >= 2:
            return 0.7
        return 1.0

    def calculate_dynamic_lot(
        self,
        symbol: str,
        balance: float,
        sl_pips: float,
        pip_value: float,
        risk_percent: float,
        volatility_multiplier: float = 1.0,
    ) -> float:
        if not self.enabled:
            return 0.0
        _ = symbol
        sl_pips = float(sl_pips or 0.0)
        pip_value = float(pip_value or 0.0)
        if sl_pips <= 0 or pip_value <= 0:
            return 0.0

        base = (float(balance or 0.0) * (float(risk_percent or 0.0) / 100.0)) / (sl_pips * pip_value)
        lot = base * self.get_cycle_lot_multiplier() * float(volatility_multiplier or 1.0)
        return max(0.0, lot)

    def trend_direction_allowed(self, direction: str, ema50: float, ema200: float) -> bool:
        direction = (direction or "").upper()
        if ema50 > ema200:
            return direction == "BUY"
        if ema50 < ema200:
            return direction == "SELL"
        return False
