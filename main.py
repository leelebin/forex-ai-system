import json
import time
import traceback
from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd

from account_mode import get_account_mode, get_mode_controls
from aggressive_mode import (
    AggressiveModeController,
    adjust_risk_by_volatility,
    get_risk_percent as get_aggressive_risk_percent,
    should_add_position,
)
from data.mt5_connector import connect, get_data
from execution.mt5_trader import close_positions_by_symbol, manage_positions, place_trade
from extreme_market_protection import extreme_market_protection
from loss_guard import LossGuard
from market_state import EXTREME, HIGH_VOL, NORMAL, get_market_permissions
from news_filter import NewsFilter
from notifier.telegram import send
from pnl_engine import PnLEngine
from position_manager import PositionManager
from daily_loss_guard import DailyLossGuard
from risk_manager import RiskManager, calculate_lot, get_symbol_type, is_currency_overexposed
from risk_model import get_risk_percent
from strategy.indicators import apply_indicators
from strategy.signal_generator import generate_signal
from trade_logger import LOGGER, init_trade_lifecycle
from trend_state import calculate_adx, get_ema_slope, get_trend_state
from utils.monitor import HeartbeatMonitor, setup_logger
from volatility_regime import build_dynamic_sl_tp, classify_volatility_regime

logger = setup_logger("bot.log")
heartbeat = HeartbeatMonitor(path="heartbeat.txt", interval_sec=600)


def log_with_time(*messages):
    now = datetime.now(timezone.utc).astimezone()
    ts = now.strftime("%Y-%m-%d %H:%M:%S %z")
    msg = " ".join(str(m) for m in messages)
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_trade_signal(symbol, data, cfg):
    """
    信号质量过滤：趋势一致 + ATR范围 + RSI范围 + 点差阈值全部满足才允许开仓。
    """
    settings = cfg.get("signal_filters", {})
    if not settings.get("enabled", True):
        return True, "disabled"

    trend_alignment = bool(data.get("trend_alignment", False))
    atr = _safe_float(data.get("atr"))
    rsi = _safe_float(data.get("rsi"))
    spread = _safe_float(data.get("spread"))

    atr_min = _safe_float(settings.get("atr_min"))
    atr_max = _safe_float(settings.get("atr_max"))
    rsi_min = _safe_float(settings.get("rsi_min", 30))
    rsi_max = _safe_float(settings.get("rsi_max", 70))
    spread_max = _safe_float(settings.get("spread_max"))

    if not trend_alignment:
        return False, f"{symbol}:trend_not_aligned"
    if atr is None or (atr_min is not None and atr < atr_min):
        return False, f"{symbol}:atr_too_low"
    if atr_max is not None and atr > atr_max:
        return False, f"{symbol}:atr_too_high"
    if rsi is None or (rsi_min is not None and rsi < rsi_min) or (rsi_max is not None and rsi > rsi_max):
        return False, f"{symbol}:rsi_out_of_range"
    if spread_max is not None and spread is not None and spread > spread_max:
        return False, f"{symbol}:spread_too_wide"

    return True, "pass"


def is_high_volatility(symbol, snapshot, cfg):
    """
    高波动识别：ATR超高、点差突增、单K异常波动任一触发即视为 HIGH_VOL。
    """
    settings = cfg.get("high_volatility_filter", {})
    if not settings.get("enabled", True):
        return {"high_volatility": False, "reasons": []}

    atr = _safe_float(snapshot.get("atr"))
    spread = _safe_float(snapshot.get("spread"))
    spread_mean = _safe_float(snapshot.get("spread_mean"))
    candle_range = _safe_float(snapshot.get("candle_range"))

    reasons = []
    atr_high = _safe_float(settings.get("atr_high_threshold"))
    if atr_high is not None and atr is not None and atr > atr_high:
        reasons.append("atr_above_threshold")

    if spread is not None and spread_mean is not None and spread_mean > 0:
        spike_ratio = float(settings.get("spread_spike_ratio", 2.0))
        if spread > spread_mean * spike_ratio:
            reasons.append("spread_spike")

    if candle_range is not None and atr is not None and atr > 0:
        range_ratio = float(settings.get("abnormal_range_atr_multiplier", 2.5))
        if candle_range > atr * range_ratio:
            reasons.append("abnormal_candle_range")

    return {"high_volatility": bool(reasons), "reasons": reasons}


def _get_today_pnl():
    now_utc = datetime.now(timezone.utc)
    day_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    deals = mt5.history_deals_get(day_start, now_utc) or []
    total = 0.0
    for deal in deals:
        total += float(getattr(deal, "profit", 0.0) or 0.0)
        total += float(getattr(deal, "swap", 0.0) or 0.0)
        total += float(getattr(deal, "commission", 0.0) or 0.0)
    return total


def send_telegram_update(status):
    """
    每隔固定扫描次数发送状态摘要。
    """
    msg = (
        "📡 扫描状态更新\n"
        f"Balance: {status['balance']:.2f}\n"
        f"Open Positions: {status['open_positions']}\n"
        f"Today PnL: {status['today_pnl']:.2f}\n"
        f"Market State: {status['market_state']}"
    )
    send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)


log_with_time("🚀 V14 自动交易系统启动（动态TP/SL + M1微结构）")

with open("config.json") as f:
    cfg = json.load(f)

connect(cfg)
heartbeat.start()

cooldown = {}
scan_round = 0
scan_counter = 0
news_filter = NewsFilter(cfg)
risk_manager = RiskManager(cfg)
pnl_engine = PnLEngine()
loss_guard = LossGuard(threshold=2)
position_manager = PositionManager()
extreme_state_store = {}
aggressive_controller = AggressiveModeController(cfg)
aggr_peak_balance = float(getattr(mt5.account_info(), "equity", 0.0) or 0.0)
processed_deals = set()
daily_loss_guard = DailyLossGuard(
    max_daily_loss_pct=float(cfg.get("daily_loss_limit_pct", 5.0)),
    tiers=cfg.get("daily_loss_tiers", []),
)

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
    try:
        heartbeat.tick()
        scan_round += 1
        scan_counter += 1
        manage_positions(event_callback=LOGGER.record_event_by_ticket)

        log_with_time(f"\n🔄 新一轮扫描（第 {scan_round} 轮）")

        bias = "neutral"

        account = mt5.account_info()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        aggr_peak_balance = max(aggr_peak_balance, equity)
        pnl_snapshot = pnl_engine.update(equity)
        market_snapshots = {}

        # Daily loss guard — update baseline and check before scanning symbols
        daily_loss_guard.update(equity)
        if daily_loss_guard.is_blocked(equity):
            _dloss_pct = daily_loss_guard.daily_loss_pct(equity)
            _dloss_cap = daily_loss_guard.effective_limit_pct
            log_with_time(
                f"🛑 每日亏损限制已触发 ({_dloss_pct:.1f}% >= {_dloss_cap:.1f}%)，本轮跳过所有开仓。"
            )
            send(
                cfg["telegram_token"],
                cfg["telegram_chat_id"],
                f"🛑 每日亏损 {_dloss_pct:.1f}% 已触发上限 {_dloss_cap:.1f}%，今日不再开新仓。",
            )
            time.sleep(15)
            continue

        for s in cfg["symbols"]:
            if aggressive_controller.enabled and not aggressive_controller.is_symbol_allowed(s):
                continue

            # Session gate: skip symbols restricted to certain UTC hours (e.g. indices)
            _s_group_params = cfg.get("symbol_group_params", {}).get(get_symbol_type(s), {})
            _trading_hours = _s_group_params.get("trading_hours_utc")
            if _trading_hours is not None:
                _utc_hour = datetime.now(timezone.utc).hour
                if not (_trading_hours[0] <= _utc_hour < _trading_hours[1]):
                    continue

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

            tick_snapshot = mt5.symbol_info_tick(s)
            spread_snapshot = None
            current_price = None
            if tick_snapshot is not None:
                bid_val = getattr(tick_snapshot, "bid", None)
                ask_val = getattr(tick_snapshot, "ask", None)
                if bid_val is not None and ask_val is not None:
                    spread_snapshot = abs(float(ask_val) - float(bid_val))
                current_price = (
                    float(bid_val)
                    if bid_val is not None
                    else (float(ask_val) if ask_val is not None else None)
                )

            spread_mean = None
            if "spread" in df_m1.columns:
                spread_mean = _safe_float(df_m1["spread"].tail(40).mean())

            prev_close = _safe_float(df_m1.iloc[-2]["close"]) if len(df_m1) > 1 else None
            last_open = _safe_float(df_m1.iloc[-1]["open"]) if "open" in df_m1.columns else None
            gap_ratio = None
            _gap_ratio_max = float(_s_group_params.get("gap_ratio_max", 0.01))
            if prev_close is not None and last_open is not None and prev_close != 0:
                gap_ratio = abs(last_open - prev_close) / abs(prev_close)

            candle_range = abs(float(df_m1.iloc[-1]["high"]) - float(df_m1.iloc[-1]["low"]))

            market_snapshots[s] = {
                "price": current_price if current_price is not None else float(df.iloc[-1]["close"]),
                "atr": float(df.iloc[-1]["atr"]) if "atr" in df.columns else None,
                "atr_mean": regime_info.get("atr_mean"),
                "rsi": float(df.iloc[-1]["rsi"]) if "rsi" in df.columns else None,
                "spread": spread_snapshot,
                "spread_mean": spread_mean,
                "candle_range": candle_range,
                "gap_ratio": gap_ratio,
                "gap_ratio_threshold": _gap_ratio_max,
                "market_state": volatility_regime,
            }

            high_vol_result = is_high_volatility(s, market_snapshots[s], cfg)
            if high_vol_result.get("high_volatility"):
                market_snapshots[s]["market_state"] = HIGH_VOL

            extreme_result = extreme_market_protection(
                symbol=s,
                snapshot=market_snapshots[s],
                cfg=cfg,
                runtime_state=extreme_state_store,
                now_ts=time.time(),
                consecutive_losses=0,
            )
            market_snapshots[s]["market_state"] = extreme_result.get("market_state", NORMAL)
            market_permissions = get_market_permissions(market_snapshots[s]["market_state"])
            if market_snapshots[s]["market_state"] == EXTREME and cfg.get("extreme_market_protection", {}).get("force_close_on_extreme", False):
                close_result = close_positions_by_symbol(s)
                if close_result.get("closed_count", 0) > 0:
                    send(
                        cfg["telegram_token"],
                        cfg["telegram_chat_id"],
                        f"🚨 {s} EXTREME 状态触发强平: {close_result['closed_count']}/{close_result['total']}",
                    )

            sig = generate_signal(df, bias, s, df_h1=df_h1, df_m1=df_m1, diagnostics=True, cfg=cfg)

            if sig and sig.get("_debug_no_signal"):
                log_with_time("信号:", s, None, "| 过滤原因:", sig["_debug_no_signal"])
                continue

            log_with_time("信号:", s, sig)

            if sig:
                logger.info("Signal detected for %s: %s", s, sig.get("direction"))
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
VolRegime: {market_snapshots[s]['market_state']}

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

                if not market_permissions.get("allow_open", True):
                    msg = (
                        f"🛑 {s} 当前市场状态={market_snapshots[s]['market_state']}，禁止新开仓/加仓。"
                        f" 原因={','.join(high_vol_result.get('reasons', []) + extreme_result.get('reasons', [])) or 'state_block'}"
                    )
                    send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                    continue

                trend_state_h1 = "UP" if float(df_h1.iloc[-1]["ema_fast"]) >= float(df_h1.iloc[-1]["ema_slow"]) else "DOWN"
                trend_state_m5 = "UP" if float(df.iloc[-1]["ema_fast"]) >= float(df.iloc[-1]["ema_slow"]) else "DOWN"
                signal_ok, signal_reason = validate_trade_signal(
                    s,
                    {
                        "trend_alignment": trend_state_h1 == trend_state_m5,
                        "atr": market_snapshots[s].get("atr"),
                        "rsi": market_snapshots[s].get("rsi"),
                        "spread": market_snapshots[s].get("spread"),
                    },
                    cfg,
                )
                if not signal_ok:
                    send(cfg["telegram_token"], cfg["telegram_chat_id"], f"🚫 {s} 信号质量过滤拦截: {signal_reason}")
                    continue

                adx_value = calculate_adx(df)
                ema_slope = get_ema_slope(df, col="ema_fast")
                trend = get_trend_state(adx_value, ema_slope)

                mode = get_account_mode(equity)
                mode_controls = get_mode_controls(mode)

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

                # Currency correlation gate: block if any currency in the pair
                # already has too many open positions
                _max_currency_exp = int(cfg.get("max_currency_exposure", 2))
                if is_currency_overexposed(s, mt5.positions_get() or [], _max_currency_exp):
                    log_with_time(f"[GATE] {s} 货币暴露过度，跳过（max={_max_currency_exp}）")
                    continue

                if aggressive_controller.enabled:
                    time_gate = aggressive_controller.can_trade_now(s, now_ts=time.time())
                    if time_gate.get("blocked"):
                        send(cfg["telegram_token"], cfg["telegram_chat_id"], f"⏸️ {s} Aggressive风控暂停: {time_gate.get('reason')}")
                        continue

                    drawdown_gate = aggressive_controller.check_drawdown_control(
                        balance=equity,
                        peak_balance=aggr_peak_balance,
                    )
                    if not drawdown_gate.allow_trade:
                        send(cfg["telegram_token"], cfg["telegram_chat_id"], f"🛑 回撤超限，停止交易。DD={drawdown_gate.drawdown_pct:.2f}%")
                        continue

                    dynamic_risk_percent = get_aggressive_risk_percent(equity)
                    dynamic_risk_percent *= drawdown_gate.risk_multiplier
                    dynamic_risk_percent *= aggressive_controller.get_profit_protection_multiplier(equity)
                    if aggressive_controller.ultra_low_risk_mode:
                        dynamic_risk_percent = 1.0
                else:
                    dynamic_risk_percent = get_risk_percent(
                        equity=equity,
                        drawdown=pnl_snapshot["drawdown"],
                        is_new_peak=pnl_snapshot["is_new_peak"],
                    )

                pos_gate = position_manager.can_open(
                    symbol=s,
                    direction=sig["direction"],
                    trend_state=trend,
                    account_mode=mode,
                    max_positions=mode_controls["max_positions"],
                    allow_pyramiding=mode_controls["allow_pyramiding"],
                    volatility_regime=market_snapshots[s]["market_state"],
                )
                if not pos_gate.get("allowed"):
                    msg = f"📦 {s} 信号被持仓管理拦截: {pos_gate.get('reason')}"
                    send(cfg["telegram_token"], cfg["telegram_chat_id"], msg)
                    continue

                ema50 = float(df_h1["close"].ewm(span=50, adjust=False).mean().iloc[-1])
                ema200 = float(df_h1["close"].ewm(span=200, adjust=False).mean().iloc[-1])
                if aggressive_controller.enabled and not aggressive_controller.trend_direction_allowed(sig["direction"], ema50, ema200):
                    send(cfg["telegram_token"], cfg["telegram_chat_id"], f"🚫 {s} 趋势方向不一致，跳过。EMA50={ema50:.5f}, EMA200={ema200:.5f}")
                    continue

                vol_adj = adjust_risk_by_volatility(
                    atr=market_snapshots[s].get("atr"),
                    atr_avg=market_snapshots[s].get("atr_mean"),
                )
                if aggressive_controller.enabled and not vol_adj.get("allow_trade", True):
                    send(cfg["telegram_token"], cfg["telegram_chat_id"], f"🛑 {s} ATR极端波动，禁止交易。")
                    continue

                lot = calculate_lot(
                    s,
                    sig["sl"],
                    sig["entry"],
                    equity,
                    risk_percent=dynamic_risk_percent,
                )
                if aggressive_controller.enabled:
                    lot *= float(vol_adj.get("lot_multiplier", 1.0))
                    lot *= float(aggressive_controller.get_cycle_lot_multiplier())

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
                    logger.error(
                        "Trade failed for %s %s, code=%s reason=%s",
                        s,
                        sig["direction"],
                        fail_code,
                        fail_reason,
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

                logger.info("Order executed for %s %s lot=%s", s, sig["direction"], lot)
                executed_price = (
                    trade_result.get("executed_price")
                    if isinstance(trade_result, dict)
                    else sig["entry"]
                )
                order_payload = {
                    "symbol": s,
                    "open_price": float(executed_price),
                    "volume": float(lot),
                    "direction": str(sig["direction"]).lower(),
                    "position_ticket": trade_result.get("position_ticket"),
                    "sl": sig["sl"],
                    "tp": sig["tp"],
                    "entry_reason": reason_text,
                    "trend_state": "UP" if ema50 > ema200 else "DOWN",
                    "atr_level": vol_adj.get("atr_level", "normal"),
                    "position_stage": "initial",
                    "signal_score": confidence_text,
                    "volatility_flag": market_snapshots[s]["market_state"] != NORMAL,
                    "initial_features": {
                        "atr": float(df.iloc[-1]["atr"]) if "atr" in df.columns else None,
                        "rsi": float(df.iloc[-1]["rsi"]) if "rsi" in df.columns else None,
                        "spread": spread_snapshot,
                        "trend_direction": trend,
                        "trend_state": "UP" if ema50 > ema200 else "DOWN",
                        "atr_level": vol_adj.get("atr_level", "normal"),
                        "market_state": market_snapshots[s]["market_state"],
                        "position_stage": "initial",
                    },
                }
                init_trade_lifecycle(order_payload)
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
VolRegime: {market_snapshots[s]['market_state']}

策略组合: {strategy_text}
触发原因:
{reason_text}
"""

                send(cfg["telegram_token"], cfg["telegram_chat_id"], success_msg)
                cooldown[s] = now

        if aggressive_controller.enabled:
            open_positions = mt5.positions_get() or []
            for p in open_positions:
                if not aggressive_controller.is_symbol_allowed(p.symbol):
                    continue
                info = {
                    "profit": float(getattr(p, "profit", 0.0) or 0.0),
                    "add_count": aggressive_controller.position_add_count[p.ticket],
                }
                risk_unit = abs(float(p.price_open) - float(p.sl or p.price_open))
                if risk_unit <= 0:
                    continue
                tick = mt5.symbol_info_tick(p.symbol)
                if tick is None:
                    continue
                mark = float(tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask)
                favorable = (mark - p.price_open) if p.type == mt5.ORDER_TYPE_BUY else (p.price_open - mark)
                info["r_multiple"] = favorable / risk_unit
                if not should_add_position(info):
                    continue

                add_mult = 0.5 if aggressive_controller.position_add_count[p.ticket] == 0 else 0.3
                add_lot = max(0.01, round(float(p.volume) * add_mult, 2))
                add_dir = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
                add_result = place_trade(p.symbol, add_dir, add_lot, p.sl, p.tp)
                if add_result and add_result.get("ok"):
                    aggressive_controller.position_add_count[p.ticket] += 1

        open_positions = mt5.positions_get() or []
        LOGGER.sync_open_positions(open_positions, market_snapshots)

        if aggressive_controller.enabled:
            now_utc = datetime.now(timezone.utc)
            hist = mt5.history_deals_get(now_utc.replace(hour=0, minute=0, second=0, microsecond=0), now_utc) or []
            for deal in hist[-20:]:
                if getattr(deal, "entry", None) != mt5.DEAL_ENTRY_OUT:
                    continue
                deal_ticket = int(getattr(deal, "ticket", 0) or 0)
                if deal_ticket in processed_deals:
                    continue
                symbol = getattr(deal, "symbol", "")
                pnl_val = float(getattr(deal, "profit", 0.0) or 0.0) + float(getattr(deal, "commission", 0.0) or 0.0) + float(getattr(deal, "swap", 0.0) or 0.0)
                aggressive_controller.record_trade_result(symbol=symbol, pnl=pnl_val)
                processed_deals.add(deal_ticket)

        tg_cfg = cfg.get("telegram_scan_update", {})
        if tg_cfg.get("enabled", True):
            interval = int(tg_cfg.get("interval_scans", 200))
            if interval > 0 and scan_counter % interval == 0:
                account_now = mt5.account_info()
                market_state = NORMAL
                if any(snap.get("market_state") == EXTREME for snap in market_snapshots.values()):
                    market_state = EXTREME
                elif any(snap.get("market_state") == HIGH_VOL for snap in market_snapshots.values()):
                    market_state = HIGH_VOL
                send_telegram_update(
                    {
                        "balance": float(getattr(account_now, "balance", 0.0) or 0.0),
                        "open_positions": len(open_positions),
                        "today_pnl": _get_today_pnl(),
                        "market_state": market_state,
                    }
                )

        time.sleep(15)

    except Exception:
        error_trace = traceback.format_exc()
        logger.exception("Unhandled exception in main loop")
        crash_msg = (
            "⚠️ 交易系统出现异常，5秒后自动恢复。\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"原因:\n{error_trace[-1500:]}"
        )
        try:
            send(cfg["telegram_token"], cfg["telegram_chat_id"], crash_msg)
        except Exception:
            logger.exception("Failed to send crash message to Telegram")

        time.sleep(5)
