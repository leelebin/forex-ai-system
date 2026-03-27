import pandas as pd
from data.mt5_connector import connect, get_data
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal
import json

print("📊 V7 回测系统启动（真实模拟）")

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)

symbol = "XAUUSD"
print(f"回测品种: {symbol}")

data = get_data(symbol, "M5", 10000)
df = pd.DataFrame(data)

df = apply_indicators(df)

balance = 500
risk_percent = 1
max_holding_bars = 24

trades = []

peak_balance = balance
max_drawdown = 0

i = 50

while i < len(df) - 50:

    sub_df = df.iloc[:i]

    signal = generate_signal(sub_df, "USD: 中性", symbol, backtest=True)

    if not signal:
        i += 1
        continue

    entry = signal['entry']
    sl = signal['sl']
    tp = signal['tp']
    direction = signal['direction']

    risk_amount = balance * (risk_percent / 100)

    future = df.iloc[i:i+max_holding_bars]
    rr_ratio = abs(tp - entry) / max(abs(entry - sl), 1e-9)

    result = None
    exit_index = i

    for j, row in future.iterrows():
        high = row['high']
        low = row['low']

        if direction == "BUY":
            if low <= sl:
                result = -1
                exit_index = j
                break
            if high >= tp:
                result = 1
                exit_index = j
                break

        else:
            if high >= sl:
                result = -1
                exit_index = j
                break
            if low <= tp:
                result = 1
                exit_index = j
                break

    if result is None:
        # 到期平仓：不再直接跳过，减少“无交易统计”，同时平滑回撤曲线
        if len(future) == 0:
            i += 1
            continue
        exit_row = future.iloc[-1]
        exit_index = future.index[-1]
        exit_price = exit_row['close']

        if direction == "BUY":
            r_multiple = (exit_price - entry) / max(entry - sl, 1e-9)
        else:
            r_multiple = (entry - exit_price) / max(sl - entry, 1e-9)

        # 限制到合理区间，避免极端波动影响胜率与回撤稳定性
        r_multiple = max(-1.0, min(r_multiple, rr_ratio))
        result = 1 if r_multiple > 0 else -1
        profit = risk_amount * r_multiple
    elif result == 1:
        profit = risk_amount * rr_ratio
    else:
        profit = -risk_amount

    balance += profit

    if balance > peak_balance:
        peak_balance = balance

    drawdown = peak_balance - balance

    if drawdown > max_drawdown:
        max_drawdown = drawdown

    trades.append(result)

    i = exit_index + 1


total = len(trades)
wins = trades.count(1)
losses = trades.count(-1)

winrate = (wins / total * 100) if total > 0 else 0

print("\n===== 回测结果 =====")
print(f"总交易: {total}")
print(f"胜率: {winrate:.2f}%")
print(f"盈利: {wins}")
print(f"亏损: {losses}")
print(f"最终余额: {balance:.2f}")
print(f"最大回撤: {max_drawdown:.2f}")
