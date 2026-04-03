import json

import MetaTrader5 as mt5
import pandas as pd

from data.mt5_connector import connect, get_data
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal


def run_backtest(symbol, cfg):
    """
    保持原有单品种回测逻辑，用于被批量回测复用。
    """
    print(f"\n📈 开始回测: {symbol}")

    data = get_data(symbol, "M5", 10000)
    df = pd.DataFrame(data)
    if df.empty:
        return {
            "symbol": symbol,
            "total_trades": 0,
            "winrate": 0.0,
            "pnl": 0.0,
            "drawdown": 0.0,
            "final_balance": 0.0,
            "wins": 0,
            "losses": 0,
        }

    df = apply_indicators(df)

    balance = float(cfg.get("backtest", {}).get("starting_balance", 500))
    risk_percent = float(cfg.get("backtest", {}).get("risk_percent", 1))
    max_holding_bars = int(cfg.get("backtest", {}).get("max_holding_bars", 24))

    trades = []
    peak_balance = balance
    max_drawdown = 0.0
    i = 50

    while i < len(df) - 50:
        sub_df = df.iloc[:i]
        signal = generate_signal(sub_df, "USD: 中性", symbol, backtest=True)

        if not signal:
            i += 1
            continue

        entry = signal["entry"]
        sl = signal["sl"]
        tp = signal["tp"]
        direction = signal["direction"]

        risk_amount = balance * (risk_percent / 100)
        future = df.iloc[i:i + max_holding_bars]
        rr_ratio = abs(tp - entry) / max(abs(entry - sl), 1e-9)

        result = None
        exit_index = i

        for j, row in future.iterrows():
            high = row["high"]
            low = row["low"]

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
            if len(future) == 0:
                i += 1
                continue
            exit_row = future.iloc[-1]
            exit_index = future.index[-1]
            exit_price = exit_row["close"]

            if direction == "BUY":
                r_multiple = (exit_price - entry) / max(entry - sl, 1e-9)
            else:
                r_multiple = (entry - exit_price) / max(sl - entry, 1e-9)

            r_multiple = max(-1.0, min(r_multiple, rr_ratio))
            result = 1 if r_multiple > 0 else -1
            profit = risk_amount * r_multiple
        elif result == 1:
            profit = risk_amount * rr_ratio
        else:
            profit = -risk_amount

        balance += profit
        peak_balance = max(peak_balance, balance)
        drawdown = peak_balance - balance
        max_drawdown = max(max_drawdown, drawdown)

        trades.append(result)
        i = exit_index + 1

    total = len(trades)
    wins = trades.count(1)
    losses = trades.count(-1)
    winrate = (wins / total * 100) if total > 0 else 0.0

    return {
        "symbol": symbol,
        "total_trades": total,
        "winrate": winrate,
        "pnl": balance - float(cfg.get("backtest", {}).get("starting_balance", 500)),
        "drawdown": max_drawdown,
        "final_balance": balance,
        "wins": wins,
        "losses": losses,
    }


def _resolve_backtest_symbols(cfg):
    backtest_cfg = cfg.get("backtest", {})
    if backtest_cfg.get("use_mt5_symbols", False):
        symbols = mt5.symbols_get() or []
        selected = [s.name for s in symbols if getattr(s, "visible", True)]
        if selected:
            return selected
    return cfg.get("symbols", [])


def run_backtest_for_all_symbols(cfg):
    """
    自动遍历全部品种回测，并输出 symbol 级与 portfolio 级统计。
    """
    symbols = _resolve_backtest_symbols(cfg)
    results = []

    for symbol in symbols:
        result = run_backtest(symbol, cfg)
        results.append(result)

    portfolio = {
        "total_trades": sum(r["total_trades"] for r in results),
        "pnl": sum(r["pnl"] for r in results),
        "max_drawdown": max((r["drawdown"] for r in results), default=0.0),
    }
    total_wins = sum(r["wins"] for r in results)
    portfolio["winrate"] = (total_wins / portfolio["total_trades"] * 100) if portfolio["total_trades"] else 0.0

    print("\n===== Symbol 回测结果 =====")
    for r in results:
        print(
            f"{r['symbol']}: trades={r['total_trades']}, "
            f"winrate={r['winrate']:.2f}%, pnl={r['pnl']:.2f}, drawdown={r['drawdown']:.2f}"
        )

    print("\n===== Portfolio 汇总 =====")
    print(f"总交易: {portfolio['total_trades']}")
    print(f"胜率: {portfolio['winrate']:.2f}%")
    print(f"总PnL: {portfolio['pnl']:.2f}")
    print(f"最大回撤: {portfolio['max_drawdown']:.2f}")

    return {"symbols": results, "portfolio": portfolio}


if __name__ == "__main__":
    print("📊 V7 回测系统启动（多品种）")
    with open("config.json") as f:
        cfg = json.load(f)

    connect(cfg)
    run_backtest_for_all_symbols(cfg)
