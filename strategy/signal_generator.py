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
        "rr_ratio": 2.4,
    }


def _calc_dynamic_rr(base_rr, trend_strength, rsi_momentum, atr, atr_baseline):
    if atr_baseline <= 0:
        atr_baseline = atr

    vol_factor = atr / atr_baseline if atr_baseline > 0 else 1.0

    rr = base_rr

    # 趋势越强，允许更高目标；趋势一般时更早锁盈
    if trend_strength > 0.00035:
        rr += 0.8
    elif trend_strength > 0.0002:
        rr += 0.4
    else:
        rr -= 0.3

    # RSI 动量确认
    if rsi_momentum > 3.0:
        rr += 0.3
    elif rsi_momentum < 1.0:
        rr -= 0.2

    # 波动率过高时避免 TP 过远导致利润回吐
    if vol_factor > 1.6:
        rr -= 0.4
    elif vol_factor < 0.9:
        rr += 0.2

    return max(1.4, min(4.2, rr))


def _m1_entry_filter(direction, df_m1):
    if df_m1 is None or len(df_m1) < 40:
        return True, "M1数据不足，跳过微结构过滤"

    m1_last = df_m1.iloc[-1]
    m1_prev = df_m1.iloc[-2]

    m1_fast = m1_last["ema_fast"]
    m1_slow = m1_last["ema_slow"]
    m1_price = m1_last["close"]
    m1_prev_price = m1_prev["close"]
    m1_rsi = m1_last["rsi"]

    if direction == "BUY":
        ok = m1_fast >= m1_slow and m1_price >= m1_fast and m1_rsi >= 48 and m1_price >= m1_prev_price
        msg = (
            f"M1过滤(BUY): ema_fast({m1_fast:.5f})>=ema_slow({m1_slow:.5f}), "
            f"price={m1_price:.5f}, rsi={m1_rsi:.2f}"
        )
    else:
        ok = m1_fast <= m1_slow and m1_price <= m1_fast and m1_rsi <= 52 and m1_price <= m1_prev_price
        msg = (
            f"M1过滤(SELL): ema_fast({m1_fast:.5f})<=ema_slow({m1_slow:.5f}), "
            f"price={m1_price:.5f}, rsi={m1_rsi:.2f}"
        )

    return ok, msg


def generate_signal(df, news, symbol, df_h1=None, df_m1=None, backtest=False):
    params = load_params()

    if len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    ema_fast = last["ema_fast"]
    ema_slow = last["ema_slow"]
    rsi = last["rsi"]
    price = last["close"]
    atr = last["atr"]
    prev_rsi = prev["rsi"]

    prev_price = prev["close"]

    trend = ema_fast - ema_slow

    h1_alignment = None
    if df_h1 is not None and len(df_h1) > 50:
        h1_last = df_h1.iloc[-1]
        h1_trend = h1_last["ema_fast"] - h1_last["ema_slow"]
        h1_alignment = h1_trend

        if trend > 0 and h1_trend < 0:
            return None
        if trend < 0 and h1_trend > 0:
            return None

    if backtest:
        buy_rsi_floor = max(params["rsi_buy"] - 8, 35)
        sell_rsi_ceil = min(params["rsi_sell"] + 8, 65)
        bullish_retest = price > ema_fast or prev_price > ema_fast
        bearish_retest = price < ema_fast or prev_price < ema_fast
        rsi_up = rsi >= prev_rsi - 0.8
        rsi_down = rsi <= prev_rsi + 0.8
    else:
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

    if not backtest and atr < params["atr_min"]:
        return None

    m1_ok, m1_reason = _m1_entry_filter(direction, df_m1)
    if not backtest and not m1_ok:
        return None

    trend_strength = abs(trend)
    rsi_momentum = abs(rsi - prev_rsi)
    atr_baseline = df["atr"].tail(60).mean() if len(df) >= 60 else df["atr"].mean()

    dynamic_rr = _calc_dynamic_rr(
        params["rr_ratio"],
        trend_strength,
        rsi_momentum,
        atr,
        atr_baseline,
    )

    sl_distance = atr * params["atr_sl_multiplier"]
    tp_distance = sl_distance * dynamic_rr

    if direction == "BUY":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    confidence = int(min(95, max(50, 50 + trend_strength * 220 + rsi_momentum * 1.8)))

    strategy_labels = ["技术面", "趋势跟随", "RSI动量", "ATR风控", "动态止盈止损"]
    if h1_alignment is not None:
        strategy_labels.append("多周期共振")
    if df_m1 is not None and len(df_m1) > 0:
        strategy_labels.append("M1微结构择时")
    if news != "neutral":
        strategy_labels.append("消息面过滤")

    reason_lines = [
        f"触发模式: {trigger_name}",
        f"趋势过滤(M5): ema_fast({ema_fast:.5f}) {'>' if direction == 'BUY' else '<'} ema_slow({ema_slow:.5f})",
        f"RSI条件: 当前 {rsi:.2f}, 前值 {prev_rsi:.2f}, {'上行' if direction == 'BUY' else '下行'}动量成立",
        f"价格位置: 当前 {price:.5f}, 前值 {prev_price:.5f}, 位于ema_fast同侧",
        f"波动率(ATR): 当前 {atr:.5f}, 基线 {atr_baseline:.5f}",
        f"动态RR: 1:{dynamic_rr:.2f}",
        m1_reason,
    ]

    if h1_alignment is not None:
        reason_lines.append(f"H1过滤: ema_fast-ema_slow={h1_alignment:.5f}, 与M5方向一致")
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
        "strategy_labels": strategy_labels,
        "dynamic_rr": round(dynamic_rr, 2),
    }
