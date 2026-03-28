from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Optional

import MetaTrader5 as mt5


def get_symbol_type(symbol):
    symbol = symbol.upper()

    if "XAU" in symbol or "XAG" in symbol:
        return "metal"

    elif "XBR" in symbol or "WTI" in symbol:
        return "oil"

    elif "BTC" in symbol or "ETH" in symbol:
        return "crypto"

    elif symbol.endswith("USD") and len(symbol) == 6:
        return "forex_major"

    elif len(symbol) == 6:
        return "forex_cross"

    elif any(x in symbol for x in ["US30", "NAS", "SPX", "GER", "UK"]):
        return "index"

    else:
        return "other"


class RiskManager:
    def __init__(self, cfg: Dict[str, Any]):
        module_cfg = cfg.get("risk_manager", {})
        self.enabled = bool(module_cfg.get("enabled", False))

        atr_cfg = module_cfg.get("atr", {})
        self.atr_enabled = bool(atr_cfg.get("enabled", True))
        self.atr_spike_ratio = float(atr_cfg.get("spike_ratio", 2.0))
        self.atr_lookback = int(atr_cfg.get("lookback", 20))
        self.atr_cooldown_min = int(atr_cfg.get("cooldown_min", 20))

        spread_cfg = module_cfg.get("spread", {})
        self.spread_enabled = bool(spread_cfg.get("enabled", True))
        self.spread_abs_max = float(spread_cfg.get("abs_max", 0.0))
        self.spread_spike_ratio = float(spread_cfg.get("spike_ratio", 2.5))
        self.spread_window = int(spread_cfg.get("window", 40))
        self.spread_cooldown_min = int(spread_cfg.get("cooldown_min", 15))

        self._spread_history: Dict[str, Deque[float]] = {}
        self._cooldown_until: Dict[str, datetime] = {}

    def should_block(self, symbol: str, df_m1=None, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"blocked": False, "reason": "risk_manager_disabled"}

        now_utc = now_utc or datetime.now(timezone.utc)

        cooldown_end = self._cooldown_until.get(symbol)
        if cooldown_end and now_utc < cooldown_end:
            return {
                "blocked": True,
                "reason": "risk_cooldown_active",
                "resume_at_utc": cooldown_end.isoformat(),
            }

        atr_result = self._check_atr(symbol, df_m1, now_utc)
        if atr_result["blocked"]:
            return atr_result

        spread_result = self._check_spread(symbol, now_utc)
        if spread_result["blocked"]:
            return spread_result

        return {"blocked": False, "reason": "risk_clear"}

    def _check_atr(self, symbol: str, df_m1, now_utc: datetime) -> Dict[str, Any]:
        if not self.atr_enabled or df_m1 is None or "atr" not in df_m1.columns:
            return {"blocked": False, "reason": "atr_check_skipped"}

        atr_series = df_m1["atr"].dropna()
        if len(atr_series) < max(self.atr_lookback + 1, 5):
            return {"blocked": False, "reason": "atr_data_insufficient"}

        atr_fast = float(atr_series.iloc[-1])
        atr_baseline = float(atr_series.iloc[-(self.atr_lookback + 1): -1].mean())
        if atr_baseline <= 0:
            return {"blocked": False, "reason": "atr_baseline_invalid"}

        ratio = atr_fast / atr_baseline
        if ratio >= self.atr_spike_ratio:
            cooldown_end = now_utc + timedelta(minutes=self.atr_cooldown_min)
            self._cooldown_until[symbol] = cooldown_end
            return {
                "blocked": True,
                "reason": f"atr_spike:{ratio:.2f}x",
                "resume_at_utc": cooldown_end.isoformat(),
            }

        return {"blocked": False, "reason": "atr_normal"}

    def _check_spread(self, symbol: str, now_utc: datetime) -> Dict[str, Any]:
        if not self.spread_enabled:
            return {"blocked": False, "reason": "spread_check_disabled"}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.ask <= 0 or tick.bid <= 0:
            return {"blocked": False, "reason": "spread_tick_unavailable"}

        spread = float(tick.ask - tick.bid)
        history = self._spread_history.setdefault(symbol, deque(maxlen=self.spread_window))
        history.append(spread)

        if len(history) < 5:
            return {"blocked": False, "reason": "spread_warmup"}

        baseline = sum(history) / len(history)

        abs_triggered = self.spread_abs_max > 0 and spread >= self.spread_abs_max
        spike_triggered = baseline > 0 and (spread / baseline) >= self.spread_spike_ratio

        if abs_triggered or spike_triggered:
            cooldown_end = now_utc + timedelta(minutes=self.spread_cooldown_min)
            self._cooldown_until[symbol] = cooldown_end
            reason = (
                f"spread_abs:{spread:.5f}" if abs_triggered else f"spread_spike:{(spread / baseline):.2f}x"
            )
            return {
                "blocked": True,
                "reason": reason,
                "resume_at_utc": cooldown_end.isoformat(),
            }

        return {"blocked": False, "reason": "spread_normal"}


def calculate_lot(symbol, sl_price, entry_price, balance, risk_percent=5):
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return 0.01

    risk_money = balance * (risk_percent / 100)
    if risk_money <= 0:
        return 0.01

    order_type = mt5.ORDER_TYPE_BUY if sl_price < entry_price else mt5.ORDER_TYPE_SELL
    loss_per_lot = abs(
        mt5.order_calc_profit(order_type, symbol, 1.0, entry_price, sl_price) or 0
    )

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if loss_per_lot <= 0:
        sl_distance = abs(entry_price - sl_price)
        if sl_distance == 0 or tick_value == 0 or tick_size == 0:
            return 0.01
        value_per_point = tick_value / tick_size
        loss_per_lot = sl_distance * value_per_point

    lot = risk_money / loss_per_lot

    symbol_type = get_symbol_type(symbol)

    if symbol_type == "forex_major":
        max_lot = 2.0

    elif symbol_type == "forex_cross":
        max_lot = 1.5

    elif symbol_type == "metal":
        max_lot = 1.0

    elif symbol_type == "oil":
        max_lot = 1.0

    elif symbol_type == "index":
        max_lot = 2.0

    elif symbol_type == "crypto":
        max_lot = 0.1

    else:
        max_lot = 1.0

    lot = max(symbol_info.volume_min, lot)
    lot = min(lot, max_lot)
    lot = min(lot, symbol_info.volume_max)

    step = symbol_info.volume_step or 0.01
    lot = round(lot / step) * step
    lot = max(symbol_info.volume_min, min(lot, symbol_info.volume_max))

    precision = 0
    if "." in f"{step:.10f}".rstrip("0"):
        precision = len(f"{step:.10f}".rstrip("0").split(".")[1])

    return round(lot, precision)
