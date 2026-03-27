import json
import os

def load_params():
    if os.path.exists("best_params.json"):
        with open("best_params.json") as f:
            return json.load(f)
    return {
        "rsi_buy": 52,
        "rsi_sell": 48,
        "atr_min": 0.25,
        "atr_sl_multiplier": 1.3,
        "rr_ratio": 2.4
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
    prev_rsi = prev['rsi']

    prev_price = prev['close']

    # =========================
    # 🔥 1. 趋势
    # =========================
    trend = ema_fast - ema_slow

    # =========================
    # 🔥 2. H1趋势过滤
    # =========================
    h1_alignment = None
    if df_h1 is not None and len(df_h1) > 50:
        h1_last = df_h1.iloc[-1]
        h1_trend = h1_last['ema_fast'] - h1_last['ema_slow']
        h1_alignment = h1_trend

        if trend > 0 and h1_trend < 0:
            return None
        if trend < 0 and h1_trend > 0:
            return None

    # =========================
    # 🔥 3. 入场逻辑（唯一版本）
    # =========================
    buy_rsi_floor = max(params["rsi_buy"] - 5, 40)
    sell_rsi_ceil = min(params["rsi_sell"] + 5, 60)

    bullish_retest = price > ema_fast and prev_price > ema_fast
    bearish_retest = price < ema_fast and prev_price < ema_fast
    rsi_up = rsi > prev_rsi
    rsi_down = rsi < prev_rsi

    if trend > 0 and buy_rsi_floor <= rsi <= params["rsi_buy"] and bullish_retest and rsi_up:
        direction = "BUY"
        trigger_name = "多头回踩确认"

    elif trend < 0 and params["rsi_sell"] <= rsi <= sell_rsi_ceil and bearish_retest and rsi_down:
        direction = "SELL"
        trigger_name = "空头回踩确认"

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
    sl_distance = atr * params["atr_sl_multiplier"]
    tp_distance = sl_distance * params["rr_ratio"]

    if direction == "BUY":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    trend_strength = abs(trend)
    rsi_momentum = abs(rsi - prev_rsi)
    confidence = int(min(95, max(50, 50 + trend_strength * 200 + rsi_momentum * 1.2)))

    strategy_labels = ["技术面", "趋势跟随", "RSI动量", "ATR风控"]
    if h1_alignment is not None:
        strategy_labels.append("多周期共振")
    if news != "neutral":
        strategy_labels.append("消息面过滤")

    reason_lines = [
        f"触发模式: {trigger_name}",
        f"趋势过滤(M5): ema_fast({ema_fast:.5f}) {'>' if direction == 'BUY' else '<'} ema_slow({ema_slow:.5f})",
        f"RSI条件: 当前 {rsi:.2f}, 前值 {prev_rsi:.2f}, {'上行' if direction == 'BUY' else '下行'}动量成立",
        f"价格位置: 当前 {price:.5f}, 前值 {prev_price:.5f}, 位于ema_fast同侧",
        f"波动率(ATR): {atr:.5f} (阈值 {params['atr_min']})",
        f"风报比: 1:{params['rr_ratio']}"
    ]

    if h1_alignment is not None:
        reason_lines.append(
            f"H1过滤: ema_fast-ema_slow={h1_alignment:.5f}, 与M5方向一致"
        )
    else:
        reason_lines.append("H1过滤: 数据不足，跳过多周期确认")

    if news == "neutral":
        reason_lines.append("消息面: 当前未启用新闻偏置过滤")
    else:
        reason_lines.append(f"消息面: 新闻偏置={news}")

    return {
        "direction": direction,
        "entry": round(price, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "confidence": confidence,
        "reason": " | ".join(reason_lines),
        "strategy_labels": strategy_labels
    }
