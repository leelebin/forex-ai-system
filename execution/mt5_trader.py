import csv
import os
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd


POSITION_STATE = {}


def _safe_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def _resolve_filling_modes(symbol_info):
    """
    Return candidate filling modes ordered by broker preference.

    Some brokers (especially index/CFD symbols) reject unsupported
    filling modes with 'Unsupported filling mode'. We detect available
    modes from symbol metadata and provide fallbacks.
    """
    preferred = []

    trade_fill_mode = getattr(symbol_info, "trade_fill_mode", None)
    if trade_fill_mode in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
        preferred.append(trade_fill_mode)

    # In MT5 Python, symbol_info.filling_mode is usually a bitmask:
    # 1 = FOK, 2 = IOC, 4 = RETURN.
    mask = getattr(symbol_info, "filling_mode", None)
    if isinstance(mask, int):
        bit_to_mode = (
            (1, mt5.ORDER_FILLING_FOK),
            (2, mt5.ORDER_FILLING_IOC),
            (4, mt5.ORDER_FILLING_RETURN),
        )
        for bit, mode in bit_to_mode:
            if mask & bit and mode not in preferred:
                preferred.append(mode)

    for fallback in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        if fallback not in preferred:
            preferred.append(fallback)

    return preferred


def _get_m1_reversal_signal(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 120)
    if rates is None or len(rates) < 40:
        return None

    df = pd.DataFrame(rates)
    df["ema_fast"] = _safe_ema(df["close"], 9)
    df["ema_slow"] = _safe_ema(df["close"], 21)

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean().replace(0, 1e-10)
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    tr1 = (df["high"] - df["low"]).abs()
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()

    last = df.iloc[-1]
    return {
        "ema_fast": float(last["ema_fast"]),
        "ema_slow": float(last["ema_slow"]),
        "rsi": float(last["rsi"]),
        "atr": float(last["atr"]) if pd.notna(last["atr"]) else None,
        "close": float(last["close"]),
    }


def _check_trading_enabled():
    terminal = mt5.terminal_info()
    if terminal is None:
        return {
            "ok": False,
            "reason": "terminal_info 获取失败，无法确认交易开关状态",
            "code": "TERMINAL_INFO_UNAVAILABLE",
        }

    if not getattr(terminal, "trade_allowed", True):
        return {
            "ok": False,
            "reason": (
                "AutoTrading disabled by client。请在 MT5 顶部工具栏开启“Algo Trading/自动交易”按钮，"
                "并在 EA 属性里勾选“允许算法交易”。"
            ),
            "code": "AUTOTRADING_DISABLED_CLIENT",
        }

    account = mt5.account_info()
    if account is None:
        return {
            "ok": False,
            "reason": "account_info 获取失败，无法确认账户交易权限",
            "code": "ACCOUNT_INFO_UNAVAILABLE",
        }

    if not getattr(account, "trade_allowed", True):
        return {
            "ok": False,
            "reason": "账户当前不允许交易（trade_allowed=False），请检查账号权限或联系券商。",
            "code": "ACCOUNT_TRADE_DISABLED",
        }

    if not getattr(account, "trade_expert", True):
        return {
            "ok": False,
            "reason": "账户未允许EA自动交易（trade_expert=False），请在MT5与账户端开启EA交易权限。",
            "code": "ACCOUNT_EXPERT_DISABLED",
        }

    return {"ok": True}


def place_trade(symbol, direction, lot, sl, tp):
    trading_gate = _check_trading_enabled()
    if not trading_gate.get("ok"):
        reason = trading_gate.get("reason", "交易权限检查未通过")
        print(f"❌ 下单前检查失败: {reason}")
        return {
            "ok": False,
            "reason": reason,
            "code": trading_gate.get("code"),
        }

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        reason = "Symbol not found"
        print(f"❌ {reason}")
        return {"ok": False, "reason": reason}

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        reason = "tick 获取失败"
        print(f"❌ {reason}")
        return {"ok": False, "reason": reason}

    price = tick.ask if direction == "BUY" else tick.bid

    if price is None:
        reason = "price is None"
        print(f"❌ {reason}")
        return {"ok": False, "reason": reason}

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    filling_candidates = _resolve_filling_modes(symbol_info)

    point = symbol_info.point

    min_stop = symbol_info.trade_stops_level * point

    if min_stop == 0:
        min_stop = point * 100

    buffer = min_stop * 1.5

    if direction == "BUY":
        if sl and (price - sl) < buffer:
            sl = price - buffer
        if tp and (tp - price) < buffer:
            tp = price + buffer
    else:
        if sl and (sl - price) < buffer:
            sl = price + buffer
        if tp and (price - tp) < buffer:
            tp = price - buffer

    sl = round(sl, 5) if sl else sl
    tp = round(tp, 5) if tp else tp

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 123456,
        "comment": "AI Trade",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    failure = None
    for fill_mode in filling_candidates:
        request["type_filling"] = fill_mode
        result = mt5.order_send(request)

        if result is None:
            failure = {"ok": False, "reason": "result is None", "retcode": None}
            print("❌ 下单失败: result is None")
            continue

        print(f"📊 下单返回(type_filling={fill_mode}):", result)
        print("retcode:", result.retcode)

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print("✅ 下单成功")
            log_trade(
                action="OPEN",
                symbol=symbol,
                direction=direction,
                lot=lot,
                entry=price,
                sl=sl,
                tp=tp,
            )
            return {"ok": True, "reason": "success", "retcode": result.retcode, "filling_mode": fill_mode}

        reason = result.comment or f"retcode={result.retcode}"
        failure = {"ok": False, "reason": reason, "retcode": result.retcode, "filling_mode": fill_mode}
        print(f"❌ 下单失败原因(type_filling={fill_mode}):", reason)

        # Unsupported filling mode => try next candidate
        if "Unsupported filling mode" in reason:
            continue

        break

    return failure or {"ok": False, "reason": "order_send failed with unknown reason"}


def get_positions():
    return mt5.positions_get()


def modify_sltp(position, new_sl=None, new_tp=None):
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "sl": new_sl if new_sl is not None else position.sl,
        "tp": new_tp if new_tp is not None else position.tp,
    }

    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        if new_sl is not None:
            print(f"🔄 SL已更新: {position.symbol} → {new_sl}")
        if new_tp is not None:
            print(f"🎯 TP已更新: {position.symbol} → {new_tp}")
        return True

    return False


def _init_position_state(pos):
    if pos.ticket in POSITION_STATE:
        return

    if pos.sl is None or pos.sl == 0:
        return

    risk = abs(pos.price_open - pos.sl)
    if risk <= 0:
        return

    POSITION_STATE[pos.ticket] = {
        "initial_risk": risk,
        "peak_price": pos.price_open,
        "trough_price": pos.price_open,
        "last_stage": "INIT",
    }


def manage_positions():
    positions = get_positions()

    if not positions:
        return

    live_tickets = {p.ticket for p in positions}
    stale_tickets = [t for t in POSITION_STATE.keys() if t not in live_tickets]
    for ticket in stale_tickets:
        POSITION_STATE.pop(ticket, None)

    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            continue

        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        entry = pos.price_open
        current_sl = pos.sl

        _init_position_state(pos)
        state = POSITION_STATE.get(pos.ticket)
        if state is None:
            continue

        risk = state["initial_risk"]
        if risk <= 0:
            continue

        if is_buy:
            state["peak_price"] = max(state["peak_price"], price)
            favorable = price - entry
        else:
            state["trough_price"] = min(state["trough_price"], price)
            favorable = entry - price

        r_multiple = favorable / risk
        reversal = _get_m1_reversal_signal(pos.symbol)

        target_sl = current_sl

        if r_multiple >= 0.8:
            be_plus = entry + risk * 0.1 if is_buy else entry - risk * 0.1
            if is_buy:
                target_sl = max(target_sl, be_plus)
            else:
                target_sl = min(target_sl, be_plus)

        if r_multiple >= 1.6:
            lock_60 = entry + risk * 0.6 if is_buy else entry - risk * 0.6
            if is_buy:
                target_sl = max(target_sl, lock_60)
            else:
                target_sl = min(target_sl, lock_60)

        if reversal is not None and r_multiple >= 1.2:
            reversed_now = (
                reversal["ema_fast"] < reversal["ema_slow"] and reversal["rsi"] < 47
                if is_buy
                else reversal["ema_fast"] > reversal["ema_slow"] and reversal["rsi"] > 53
            )
            if reversed_now:
                protect = entry + risk * 0.9 if is_buy else entry - risk * 0.9
                if is_buy:
                    target_sl = max(target_sl, protect)
                else:
                    target_sl = min(target_sl, protect)
                if state["last_stage"] != "REVERSAL_PROTECT":
                    print(f"⚠️ {pos.symbol} 检测到M1反转，提前锁盈")
                    state["last_stage"] = "REVERSAL_PROTECT"

        atr = reversal["atr"] if reversal else None
        if atr and atr > 0 and r_multiple >= 2.0:
            if is_buy:
                atr_trail = state["peak_price"] - atr * 1.2
                target_sl = max(target_sl, atr_trail)
            else:
                atr_trail = state["trough_price"] + atr * 1.2
                target_sl = min(target_sl, atr_trail)

        precision = 5
        target_sl = round(target_sl, precision)

        if is_buy and target_sl > current_sl:
            if modify_sltp(pos, new_sl=target_sl):
                log_trade(
                    action="SL_UPDATE",
                    symbol=pos.symbol,
                    direction="BUY",
                    lot=pos.volume,
                    entry=entry,
                    sl=target_sl,
                    tp=pos.tp,
                    profit=pos.profit,
                )
        elif (not is_buy) and target_sl < current_sl:
            if modify_sltp(pos, new_sl=target_sl):
                log_trade(
                    action="SL_UPDATE",
                    symbol=pos.symbol,
                    direction="SELL",
                    lot=pos.volume,
                    entry=entry,
                    sl=target_sl,
                    tp=pos.tp,
                    profit=pos.profit,
                )


def log_trade(action, symbol, direction, lot, entry, sl, tp, profit=0):
    file = "trade_log.csv"

    file_exists = os.path.isfile(file)

    with open(file, mode="a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(["time", "action", "symbol", "direction", "lot", "entry", "sl", "tp", "profit"])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action,
            symbol,
            direction,
            lot,
            entry,
            sl,
            tp,
            profit,
        ])
