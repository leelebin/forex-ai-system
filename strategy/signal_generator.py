import json
import os

def load_params():
    if os.path.exists("best_params.json"):
        with open("best_params.json") as f:
            return json.load(f)
    return {
        "rsi_buy": 52,
        "rsi_sell": 48,
        "atr_min": 0.25
    }


def generate_signal(df, news, symbol, df_h1=None, backtest=False):
    params = load_params()

    if len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    ema_fast = last['ema_fast']
    ema_slow = last['ema_slow']
    rsi = last['rsi']
    price = last['close']
    atr = last['atr']

    prev_price = prev['close']

    # =========================
    # 🔥 1. 趋势
    # =========================
    trend = ema_fast - ema_slow

    # =========================
    # 🔥 2. H1趋势过滤
    # =========================
    if df_h1 is not None and len(df_h1) > 50:
        h1_last = df_h1.iloc[-1]
        h1_trend = h1_last['ema_fast'] - h1_last['ema_slow']

        if trend > 0 and h1_trend < 0:
            return None
        if trend < 0 and h1_trend > 0:
            return None

    # =========================
    # 🔥 3. 入场逻辑（唯一版本）
    # =========================
    if trend > 0 and 45 < rsi < 55 and price > ema_fast and prev_price > ema_fast:
        direction = "BUY"

    elif trend < 0 and 45 < rsi < 55 and price < ema_fast and prev_price < ema_fast:
        direction = "SELL"

    else:
        return None

    # =========================
    # 🔥 4. ATR过滤（只用于实盘）
    # =========================
    if not backtest:
        if atr < params["atr_min"]:
            return None

    # =========================
    # 🔥 5. TP / SL（固定结构）
    # =========================
    sl_distance = atr * 1.2
    tp_distance = atr * 3.5

    if direction == "BUY":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    return {
        "direction": direction,
        "entry": round(price, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "confidence": 50,
        "reason": "趋势突破确认"
    }