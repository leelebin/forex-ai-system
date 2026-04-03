from __future__ import annotations

from typing import Dict

from market_state import EXTREME, HIGH_VOL, NORMAL


def extreme_market_protection(
    symbol: str,
    snapshot: dict,
    cfg: dict,
    runtime_state: Dict[str, dict],
    now_ts: float,
    consecutive_losses: int = 0,
) -> dict:
    """
    极端行情防护：
    任一触发条件满足即进入 EXTREME，并进入 cooldown。
    """
    settings = cfg.get("extreme_market_protection", {})
    if not settings.get("enabled", True):
        return {"market_state": snapshot.get("market_state", NORMAL), "triggered": False, "reasons": []}

    per_symbol = runtime_state.setdefault(symbol, {"cooldown_until": 0.0})
    if now_ts < float(per_symbol.get("cooldown_until", 0.0)):
        return {
            "market_state": EXTREME,
            "triggered": True,
            "reasons": ["cooldown_active"],
            "cooldown_until": per_symbol.get("cooldown_until"),
        }

    reasons = []
    spread = snapshot.get("spread")
    atr_now = snapshot.get("atr")
    atr_mean = snapshot.get("atr_mean")
    gap_ratio = snapshot.get("gap_ratio")

    if spread is not None and float(spread) > float(settings.get("extreme_spread_threshold", 0.0)):
        reasons.append("spread_extreme")
    if (
        atr_now is not None
        and atr_mean is not None
        and float(atr_mean) > 0
        and float(atr_now) > float(settings.get("atr_spike_ratio", 2.0)) * float(atr_mean)
    ):
        reasons.append("atr_spike_extreme")
    if gap_ratio is not None and float(gap_ratio) >= float(settings.get("gap_ratio_threshold", 1.5)):
        reasons.append("gap_extreme")
    if int(consecutive_losses) >= int(settings.get("max_consecutive_losses", 3)):
        reasons.append("loss_streak_extreme")

    if reasons:
        cooldown_sec = int(settings.get("cooldown_sec", 1800))
        per_symbol["cooldown_until"] = now_ts + cooldown_sec
        return {
            "market_state": EXTREME,
            "triggered": True,
            "reasons": reasons,
            "cooldown_until": per_symbol["cooldown_until"],
        }

    current_state = snapshot.get("market_state", NORMAL)
    return {
        "market_state": HIGH_VOL if current_state == HIGH_VOL else NORMAL,
        "triggered": False,
        "reasons": [],
    }
