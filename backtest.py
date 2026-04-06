import json
import math

import MetaTrader5 as mt5
import pandas as pd

from data.mt5_connector import connect, get_data
from risk_manager import get_symbol_type
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal


def _bt_risk_pct(balance: float) -> float:
    """Dynamic risk % that mirrors risk_model.py tiers used in live trading."""
    if balance < 5_000:
        return 6.0
    if balance < 20_000:
        return 3.0
    if balance < 100_000:
        return 1.5
    return 0.8


def run_backtest(symbol, cfg):
    """Single-symbol backtest with:
    - Dynamic risk % matching live tiers
    - Per-symbol-group ATR/SL/RR/max_holding_bars from config
    - Slippage + half-spread cost on every entry and exit
    - 50 000 bars (~6 months of M5) by default
    - Extended result stats (win_rate, pnl_pct, max_drawdown_pct, sharpe_approx)
    """
    print(f"\n📈 开始回测: {symbol}")

    bars = int(cfg.get("backtest", {}).get("bars", 50000))
    data = get_data(symbol, "M5", bars)
    df = pd.DataFrame(data)
    if df.empty:
        return _empty_result(symbol)

    df = apply_indicators(df)

    starting_balance = float(cfg.get("backtest", {}).get("starting_balance", 500))
    balance = starting_balance

    # Per-symbol-group parameters
    group = get_symbol_type(symbol)
    gp = cfg.get("symbol_group_params", {}).get(group, {})
    default_hold = int(cfg.get("backtest", {}).get("max_holding_bars", 24))
    max_holding_bars = int(gp.get("max_holding_bars", default_hold))

    # Slippage: assume half-spread on entry + half-spread on exit
    spread_pips = float(gp.get("spread_max_pips", 0.003))
    slippage_each_way = spread_pips * 0.5  # one-way cost in price units

    trades = []
    trade_pnls = []
    peak_balance = balance
    max_drawdown = 0.0
    i = 50

    while i < len(df) - max_holding_bars:
        sub_df = df.iloc[:i]
        signal = generate_signal(sub_df, "neutral", symbol, backtest=True, cfg=cfg)

        if not signal:
            i += 1
            continue

        entry = signal["entry"]
        sl = signal["sl"]
        tp = signal["tp"]
        direction = signal["direction"]

        # Apply entry slippage (worsens fill price)
        if direction == "BUY":
            entry += slippage_each_way
            sl_distance = max(entry - sl, 1e-9)
            tp_distance = tp - entry
        else:
            entry -= slippage_each_way
            sl_distance = max(sl - entry, 1e-9)
            tp_distance = entry - tp

        rr_ratio = tp_distance / sl_distance

        risk_percent = _bt_risk_pct(balance)
        risk_amount = balance * (risk_percent / 100)

        future = df.iloc[i: i + max_holding_bars]
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
            exit_price = float(exit_row["close"])

            if direction == "BUY":
                r_multiple = (exit_price - entry) / sl_distance
            else:
                r_multiple = (entry - exit_price) / sl_distance

            r_multiple = max(-1.0, min(r_multiple, rr_ratio))
            profit = risk_amount * r_multiple
        elif result == 1:
            profit = risk_amount * rr_ratio
        else:
            profit = -risk_amount

        # Deduct exit slippage cost (always paid regardless of outcome)
        exit_cost = risk_amount * (slippage_each_way / sl_distance) if sl_distance > 0 else 0.0
        profit -= exit_cost

        balance += profit
        peak_balance = max(peak_balance, balance)
        drawdown = peak_balance - balance
        max_drawdown = max(max_drawdown, drawdown)

        trades.append(1 if profit > 0 else -1)
        trade_pnls.append(profit)
        i = exit_index + 1

    total = len(trades)
    wins = trades.count(1)
    losses = trades.count(-1)
    winrate = (wins / total * 100) if total > 0 else 0.0
    pnl = balance - starting_balance
    pnl_pct = pnl / starting_balance * 100 if starting_balance > 0 else 0.0
    max_dd_pct = max_drawdown / starting_balance * 100 if starting_balance > 0 else 0.0

    # Approximate Sharpe (annualised, assuming each trade ~2h avg on M5)
    sharpe_approx = 0.0
    if len(trade_pnls) >= 2:
        mean_pnl = sum(trade_pnls) / len(trade_pnls)
        variance = sum((p - mean_pnl) ** 2 for p in trade_pnls) / len(trade_pnls)
        std_pnl = math.sqrt(variance) if variance > 0 else 1e-9
        trades_per_year = 252 * 24 * 60 / 120  # ~1512 trades/year at 2h avg
        sharpe_approx = round((mean_pnl / std_pnl) * math.sqrt(trades_per_year), 2)

    result_dict = {
        "symbol": symbol,
        "group": group,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(winrate, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "drawdown": round(max_drawdown, 2),
        "drawdown_pct": round(max_dd_pct, 2),
        "final_balance": round(balance, 2),
        "sharpe_approx": sharpe_approx,
    }

    print(
        f"  trades={total}, winrate={winrate:.1f}%, pnl={pnl:.2f} ({pnl_pct:.1f}%), "
        f"max_dd={max_drawdown:.2f} ({max_dd_pct:.1f}%), sharpe≈{sharpe_approx}"
    )
    return result_dict


def _empty_result(symbol):
    return {
        "symbol": symbol,
        "group": get_symbol_type(symbol),
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "winrate": 0.0,
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "drawdown": 0.0,
        "drawdown_pct": 0.0,
        "final_balance": 0.0,
        "sharpe_approx": 0.0,
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
    """Run backtest for all configured symbols and print symbol + portfolio stats."""
    symbols = _resolve_backtest_symbols(cfg)
    results = []

    for symbol in symbols:
        result = run_backtest(symbol, cfg)
        results.append(result)

    total_trades = sum(r["total_trades"] for r in results)
    total_wins = sum(r["wins"] for r in results)
    total_pnl = sum(r["pnl"] for r in results)
    max_dd = max((r["drawdown"] for r in results), default=0.0)
    starting_balance = float(cfg.get("backtest", {}).get("starting_balance", 500))

    portfolio = {
        "total_trades": total_trades,
        "wins": total_wins,
        "pnl": round(total_pnl, 2),
        "pnl_pct": round(total_pnl / starting_balance * 100, 2) if starting_balance else 0.0,
        "max_drawdown": round(max_dd, 2),
        "winrate": round(total_wins / total_trades * 100, 2) if total_trades else 0.0,
    }

    print("\n===== Symbol 回测结果 =====")
    for r in results:
        print(
            f"{r['symbol']} [{r['group']}]: trades={r['total_trades']}, "
            f"winrate={r['winrate']:.1f}%, pnl={r['pnl']:.2f} ({r['pnl_pct']:.1f}%), "
            f"max_dd={r['drawdown']:.2f} ({r['drawdown_pct']:.1f}%), sharpe≈{r['sharpe_approx']}"
        )

    print("\n===== Portfolio 汇总 =====")
    print(f"总交易: {portfolio['total_trades']}")
    print(f"胜率: {portfolio['winrate']:.2f}%")
    print(f"总PnL: {portfolio['pnl']:.2f} ({portfolio['pnl_pct']:.1f}%)")
    print(f"最大回撤: {portfolio['max_drawdown']:.2f}")

    return {"symbols": results, "portfolio": portfolio}


if __name__ == "__main__":
    print("📊 V8 回测系统启动（多品种 · 动态风险 · 滑点建模）")
    with open("config.json") as f:
        cfg = json.load(f)

    connect(cfg)
    run_backtest_for_all_symbols(cfg)
