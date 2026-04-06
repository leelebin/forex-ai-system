from __future__ import annotations

import pandas as pd


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """基于 OHLC 计算最新 ADX（轻量实现）。"""
    if df is None or len(df) < period * 2:
        return 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = low.diff() * -1

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing: EWM with alpha=1/period (standard ADX definition)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA))
    minus_di = 100 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr.replace(0, pd.NA))

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)) * 100
    adx = dx.ewm(alpha=alpha, adjust=False).mean().fillna(0.0)
    return float(adx.iloc[-1])


def get_ema_slope(df: pd.DataFrame, col: str = "ema_fast") -> float:
    if df is None or len(df) < 2 or col not in df.columns:
        return 0.0
    return float(df[col].iloc[-1] - df[col].iloc[-2])


def get_trend_state(adx: float, ema_slope: float) -> str:
    adx = float(adx or 0.0)
    slope = abs(float(ema_slope or 0.0))

    if adx < 18 or slope < 1e-5:
        return "RANGE"
    if adx < 25:
        return "EARLY"
    if adx <= 45:
        return "STRONG"
    return "EXHAUST"
