import json
from data.mt5_connector import get_data, connect
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)

def random_range(a, b):
    import random
    return round(random.uniform(a, b), 3)

def backtest(symbol):
    df = get_data(symbol, "M5", 2000)

    if df is None or len(df) == 0:
        return 0, 0

    df = apply_indicators(df)

    balance = 1000
    trades = 0

    for i in range(50, len(df)):
        sub = df.iloc[:i]

        sig = generate_signal(sub, "USD: 中性", symbol, backtest=True)

        if sig:
            trades += 1

            entry = sig['entry']
            tp = sig['tp']
            sl = sig['sl']

            future = df.iloc[i:i+10]

            for _, row in future.iterrows():
                price = row['close']

                if sig['direction'] == "BUY":
                    if price >= tp:
                        balance += 10
                        break
                    if price <= sl:
                        balance -= 10
                        break
                else:
                    if price <= tp:
                        balance += 10
                        break
                    if price >= sl:
                        balance -= 10
                        break

    return balance, trades


def optimize():
    best = None
    best_balance = -999

    for _ in range(20):

        params = {
            "rsi_buy": random_range(48, 55),
            "rsi_sell": random_range(45, 52),
            "atr_min": random_range(0.2, 0.5)
        }

        with open("best_params.json", "w") as f:
            json.dump(params, f)

        balance, trades = backtest("EURUSD")

        print("测试:", params, "结果:", balance, "交易数:", trades)

        if trades < 10:
            continue

        if balance > best_balance:
            best_balance = balance
            best = params

    if best:
        with open("best_params.json", "w") as f:
            json.dump(best, f, indent=2)

    print("\n🔥 最优参数:", best)


if __name__ == "__main__":
    optimize()