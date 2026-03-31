from __future__ import annotations

from typing import Any, Dict


LOW_VOL = "LOW_VOL"
NORMAL = "NORMAL"
HIGH_VOL = "HIGH_VOL"


def classify_volatility_regime(
    df,
    lookback: int = 60,
    low_ratio: float = 0.8,
    high_ratio: float = 1.35,
) -> Dict[str, Any]:
    if df is None or "atr" not in df.columns:
        return {"regime": NORMAL, "reason": "atr_missing"}

    atr_series = df["atr"].dropna()
    need_bars = max(lookback + 1, 20)
    if len(atr_series) < need_bars:
        return {"regime": NORMAL, "reason": "atr_insufficient"}

    atr_current = float(atr_series.iloc[-1])
    atr_mean = float(atr_series.iloc[-(lookback + 1):-1].mean())
    if atr_mean <= 0:
        return {"regime": NORMAL, "reason": "atr_mean_invalid"}

    ratio = atr_current / atr_mean
    if ratio >= high_ratio:
        regime = HIGH_VOL
    elif ratio <= low_ratio:
        regime = LOW_VOL
    else:
        regime = NORMAL

    return {
        "regime": regime,
        "atr_current": atr_current,
        "atr_mean": atr_mean,
        "atr_ratio": ratio,
        "reason": "ok",
    }


def build_dynamic_sl_tp(
    entry: float,
    direction: str,
    atr_value: float,
    rr_ratio: float,
    regime: str,
    sl_multipliers: Dict[str, float],
):
    if atr_value is None or atr_value <= 0:
        return None, None

    sl_multiplier = float(sl_multipliers.get(regime, sl_multipliers.get(NORMAL, 1.3)))
    sl_distance = atr_value * sl_multiplier
    tp_distance = sl_distance * float(rr_ratio)

    if direction == "BUY":
        sl = entry - sl_distance
        tp = entry + tp_distance
    else:
        sl = entry + sl_distance
        tp = entry - tp_distance

    return round(sl, 5), round(tp, 5)
