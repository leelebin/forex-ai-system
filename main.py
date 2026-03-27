import json
import time
import pandas as pd
import MetaTrader5 as mt5

from data.mt5_connector import connect, get_data
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal
#from ai.news_fetcher import fetch_news
#from ai.news_analyzer import analyze_news
from notifier.telegram import send
from execution.mt5_trader import place_trade, manage_positions
from risk_manager import calculate_lot

print("🚀 V13 自动交易系统启动")

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)

cooldown = {}

while True:
    manage_positions()

    print("\n🔄 新一轮扫描")

    #news = fetch_news()
    #bias = analyze_news(news, cfg["ollama_model"])
    bias = "neutral"

    for s in cfg["symbols"]:
        df = pd.DataFrame(get_data(s, "M5"))
        df_h1 = pd.DataFrame(get_data(s, "H1"))

        if df.empty or df_h1.empty:
            continue

        df = apply_indicators(df)
        df_h1 = apply_indicators(df_h1)

        sig = generate_signal(df, bias, s, df_h1=df_h1)

        print("信号:", s, sig)

        if sig:
            now = time.time()

            if s in cooldown and now - cooldown[s] < 300:
                continue

            account = mt5.account_info()
            balance = account.balance

            lot = calculate_lot(
                s,
                sig['sl'],
                sig['entry'],
                balance,
                risk_percent=1
            )

            place_trade(
                s,
                sig['direction'],
                lot,
                sig['sl'],
                sig['tp']
            )

            strategy_text = " / ".join(sig.get("strategy_labels", ["技术面"]))
            reason_text = sig.get("reason", "无详细原因")
            confidence_text = sig.get("confidence", "N/A")
            msg = f"""
🚨 自动交易执行

{s} {sig['direction']}

Entry: {sig['entry']}
SL: {sig['sl']}
TP: {sig['tp']}
Lot: {lot}
Confidence: {confidence_text}

策略组合: {strategy_text}
触发原因:
{reason_text}
"""

            send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)

            cooldown[s] = now

    time.sleep(15)
