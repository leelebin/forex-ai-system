import json
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from account_mode import get_account_mode, get_mode_controls
from data.mt5_connector import connect, get_data
from execution.mt5_trader import manage_positions, place_trade
from loss_guard import LossGuard
from news_filter import NewsFilter
from notifier.telegram import send
from pnl_engine import PnLEngine
from position_manager import PositionManager
from risk_manager import RiskManager, calculate_lot
from risk_model import get_risk_percent
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal
from trend_state import calculate_adx, get_ema_slope, get_trend_state
from volatility_regime import HIGH_VOL, NORMAL, build_dynamic_sl_tp, classify_volatility_regime


def log_with_time(*messages):
    now = datetime.now(timezone.utc).astimezone()
    ts = now.strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"[{ts}]", *messages, flush=True)


log_with_time("🚀 V14 自动交易系统启动（动态TP/SL + M1微结构）")

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)

cooldown = {}
scan_round = 0
news_filter = NewsFilter(cfg)
risk_manager = RiskManager(cfg)
pnl_engine = PnLEngine()
loss_guard = LossGuard(threshold=2)
position_manager = PositionManager()

vol_cfg = cfg.get("volatility_regime", {})
vol_lookback = int(vol_cfg.get("lookback", 60))
vol_low_ratio = float(vol_cfg.get("low_ratio", 0.8))
vol_high_ratio = float(vol_cfg.get("high_ratio", 1.35))
regime_sl_multipliers = vol_cfg.get(
    "sl_multipliers",
    {
        "LOW_VOL": 1.1,
        "NORMAL": 1.3,
        "HIGH_VOL": 1.8,
    },
)

while True:
    scan_round += 1
    manage_positions()

    log_with_time(f"\n🔄 新一轮扫描（第 {scan_round} 轮）")

    # news = fetch_news()
    # bias = analyze_news(news, cfg["ollama_model"])
    bias = "neutral"

    account = mt5.account_info()
    equity = float(getattr(account, "equity", 0.0) or 0.0)
    pnl_snapshot = pnl_engine.update(equity)

    for s in cfg["symbols"]:
        df = pd.DataFrame(get_data(s, "M5"))
        df_h1 = pd.DataFrame(get_data(s, "H1"))
        df_m1 = pd.DataFrame(get_data(s, "M1", n=1500))

        if df.empty or df_h1.empty or df_m1.empty:
            continue

        df = apply_indicators(df)
        df_h1 = apply_indicators(df_h1)
        df_m1 = apply_indicators(df_m1)

        regime_info = classify_volatility_regime(
            df_m1,
            lookback=vol_lookback,
            low_ratio=vol_low_ratio,
            high_ratio=vol_high_ratio,
        )
        volatility_regime = regime_info.get("regime", NORMAL)

        sig = generate_signal(df, bias, s, df_h1=df_h1, df_m1=df_m1, diagnostics=True)

        if sig and sig.get("_debug_no_signal"):
            log_with_time("信号:", s, None, "| 过滤原因:", sig["_debug_no_signal"])
            continue

        log_with_time("信号:", s, sig)

        if sig:
            strategy_text = " / ".join(sig.get("strategy_labels", ["技术面"]))
            reason_text = sig.get("reason", "无详细原因")
            confidence_text = sig.get("confidence", "N/A")
            dynamic_rr = sig.get("dynamic_rr", "N/A")

            atr_now = float(df.iloc[-1]["atr"]) if "atr" in df.columns else None
            sl_dyn, tp_dyn = build_dynamic_sl_tp(
                entry=float(sig["entry"]),
                direction=sig["direction"],
                atr_value=atr_now,
                rr_ratio=float(dynamic_rr) if dynamic_rr != "N/A" else 2.0,
                regime=volatility_regime,
                sl_multipliers=regime_sl_multipliers,
            )
            if sl_dyn is not None and tp_dyn is not None:
                sig["sl"] = sl_dyn
                sig["tp"] = tp_dyn

            setup_msg = f"""
👀 检测到进场机会（第 {scan_round} 轮）

{s} {sig['direction']}
Entry: {sig['entry']}
SL: {sig['sl']}
TP: {sig['tp']}
Confidence: {confidence_text}
Dynamic RR: 1:{dynamic_rr}
VolRegime: {volatility_regime}

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

            if volatility_regime == HIGH_VOL:
                msg = (
                    f"🌪️ {s} 当前波动状态={volatility_regime}，禁止新开仓/加仓。"
                    f" ATR={regime_info.get('atr_current', 'N/A')}, "
                    f"基线={regime_info.get('atr_mean', 'N/A')}"
                )
                send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                continue

            # 1) trend_state
            adx_value = calculate_adx(df)
            ema_slope = get_ema_slope(df, col="ema_fast")
            trend = get_trend_state(adx_value, ema_slope)

            # 2) account_mode
            mode = get_account_mode(equity)
            mode_controls = get_mode_controls(mode)

            # 3) loss_guard
            loss_gate = loss_guard.is_blocked(
                symbol=s,
                direction=sig["direction"],
                trend_id=trend,
            )
            if loss_gate.get("blocked"):
                msg = f"🧯 {s} 信号被连续亏损保护拦截: {loss_gate.get('reason')}"
                send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                continue

            news_gate = news_filter.should_block(s)
            if news_gate.get("blocked"):
                msg = (
                    f"📰 {s} 信号被新闻过滤拦截: {news_gate.get('reason')} "
                    f"(恢复时间: {news_gate.get('resume_at_utc', 'N/A')})"
                )
                send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                continue

            risk_gate = risk_manager.should_block(s, df_m1=df_m1)
            if risk_gate.get("blocked"):
                msg = (
                    f"🛡️ {s} 信号被风控拦截: {risk_gate.get('reason')} "
                    f"(恢复时间: {risk_gate.get('resume_at_utc', 'N/A')})"
                )
                send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                continue

            # 4) pnl_engine + risk_model
            dynamic_risk_percent = get_risk_percent(
                equity=equity,
                drawdown=pnl_snapshot["drawdown"],
                is_new_peak=pnl_snapshot["is_new_peak"],
            )

            # 5) position_manager
            pos_gate = position_manager.can_open(
                symbol=s,
                direction=sig["direction"],
                trend_state=trend,
                account_mode=mode,
                max_positions=mode_controls["max_positions"],
                allow_pyramiding=mode_controls["allow_pyramiding"],
                volatility_regime=volatility_regime,
            )
            if not pos_gate.get("allowed"):
                msg = f"📦 {s} 信号被持仓管理拦截: {pos_gate.get('reason')}"
                send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                continue

            lot = calculate_lot(
                s,
                sig["sl"],
                sig["entry"],
                equity,
                risk_percent=dynamic_risk_percent,
            )

            # 6) execute
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
                fail_code = (
                    trade_result.get("code", "N/A")
                    if isinstance(trade_result, dict)
                    else "N/A"
                )
                fail_msg = f"""
❌ 自动下单失败（第 {scan_round} 轮）

{s} {sig['direction']}
Lot: {lot}
错误码: {fail_code}
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
Risk: {dynamic_risk_percent}%
TrendState: {trend}
AccountMode: {mode}
Drawdown: {pnl_snapshot['drawdown']}%
Confidence: {confidence_text}
Dynamic RR: 1:{dynamic_rr}
VolRegime: {volatility_regime}

策略组合: {strategy_text}
触发原因:
{reason_text}
"""

            send(cfg["telegram_token"], cfg["telegram_chat_id"], success_msg)

            cooldown[s] = now

    time.sleep(15)
