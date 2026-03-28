import json
import time
import pandas as pd
import MetaTrader5 as mt5

from data.mt5_connector import connect, get_data
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal
# from ai.news_fetcher import fetch_news
# from ai.news_analyzer import analyze_news
from notifier.telegram import send
from execution.mt5_trader import place_trade, manage_positions
from risk_manager import calculate_lot

print("🚀 V14 自动交易系统启动（动态TP/SL + M1微结构）")

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)

cooldown = {}
risk_percent = float(cfg.get("risk_percent", 2.0))
scan_round = 0

while True:
    scan_round += 1
    manage_positions()

    print(f"\n🔄 新一轮扫描（第 {scan_round} 轮）")

    # news = fetch_news()
    # bias = analyze_news(news, cfg["ollama_model"])
    bias = "neutral"

    for s in cfg["symbols"]:
        df = pd.DataFrame(get_data(s, "M5"))
        df_h1 = pd.DataFrame(get_data(s, "H1"))
        df_m1 = pd.DataFrame(get_data(s, "M1", n=1500))

        if df.empty or df_h1.empty or df_m1.empty:
            continue

        df = apply_indicators(df)
        df_h1 = apply_indicators(df_h1)
        df_m1 = apply_indicators(df_m1)

        sig = generate_signal(df, bias, s, df_h1=df_h1, df_m1=df_m1)

        print("信号:", s, sig)

        if sig:
            strategy_text = " / ".join(sig.get("strategy_labels", ["技术面"]))
            reason_text = sig.get("reason", "无详细原因")
            confidence_text = sig.get("confidence", "N/A")
            dynamic_rr = sig.get("dynamic_rr", "N/A")

            setup_msg = f"""
👀 检测到进场机会（第 {scan_round} 轮）

{s} {sig['direction']}
Entry: {sig['entry']}
SL: {sig['sl']}
TP: {sig['tp']}
Confidence: {confidence_text}
Dynamic RR: 1:{dynamic_rr}

策略组合: {strategy_text}
触发原因:
{reason_text}
"""
            send(cfg["telegram_token"], cfg["telegram_chat_id"], setup_msg)

            now = time.time()

            if s in cooldown and now - cooldown[s] < 300:
                remain_sec = int(300 - (now - cooldown[s]))
                cooldown_msg = (
                    f"⏳ {s} 检测到机会但未下单：冷却中，剩余 {remain_sec} 秒。"
                )
                send(cfg["telegram_token"], cfg["telegram_chat_id"], cooldown_msg)
                continue

            account = mt5.account_info()
            balance = account.balance

            lot = calculate_lot(
                s,
                sig["sl"],
                sig["entry"],
                balance,
                risk_percent=risk_percent,
            )

            trade_result = place_trade(
                s,
                sig["direction"],
                lot,
                sig["sl"],
                sig["tp"],
            )

            if not trade_result or not trade_result.get("ok"):
                fail_reason = (
                    trade_result.get("reason", "未知错误")
                    if isinstance(trade_result, dict)
                    else "未知错误"
                )
                fail_msg = f"""
❌ 自动下单失败（第 {scan_round} 轮）

{s} {sig['direction']}
Lot: {lot}
失败原因: {fail_reason}
触发原因:
{reason_text}
"""
                send(cfg["telegram_token"], cfg["telegram_chat_id"], fail_msg)
                continue

            success_msg = f"""
🚨 自动交易执行

{s} {sig['direction']}

Entry: {sig['entry']}
SL: {sig['sl']}
TP: {sig['tp']}
Lot: {lot}
Confidence: {confidence_text}
Dynamic RR: 1:{dynamic_rr}

策略组合: {strategy_text}
触发原因:
{reason_text}
"""

            send(cfg["telegram_token"], cfg["telegram_chat_id"], success_msg)

            cooldown[s] = now

    time.sleep(15)
