import MetaTrader5 as mt5
import csv
import os
from datetime import datetime


# =========================
# 🔥 下单函数（增强版）
# =========================
def place_trade(symbol, direction, lot, sl, tp):
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print("❌ Symbol not found")
        return

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        print("❌ tick 获取失败")
        return

    price = tick.ask if direction == "BUY" else tick.bid

    if price is None:
        print("❌ price is None")
        return

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

   # 🔥 自动选择 filling mode（兼容所有broker）
    filling = mt5.ORDER_FILLING_RETURN

    try:
         if hasattr(symbol_info, "trade_fill_mode"):
             if symbol_info.trade_fill_mode == mt5.ORDER_FILLING_FOK:
               filling = mt5.ORDER_FILLING_FOK
             elif symbol_info.trade_fill_mode == mt5.ORDER_FILLING_IOC:
              filling = mt5.ORDER_FILLING_IOC
    except: 
        pass

    # =========================
    # 🔥 修复 SL / TP（增强版）
    # =========================
    point = symbol_info.point

    # 有些品种 stops_level = 0，需要手动给最小距离
    min_stop = symbol_info.trade_stops_level * point

    if min_stop == 0:
        min_stop = point * 100  # 🔥 关键（避免ETH/BTC报错）

    buffer = min_stop * 1.5  # 🔥 安全缓冲

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

    # 保留精度
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
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)

    if result is None:
        print("❌ 下单失败: result is None")
    else:
        print("📊 下单返回:", result)
        print("retcode:", result.retcode)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print("❌ 下单失败原因:", result.comment)
        else:
            print("✅ 下单成功")

            log_trade(
                  action="OPEN",
                 symbol=symbol,
                direction=direction,
                lot=lot,
                entry=price,
                sl=sl,
                tp=tp
            )


# =========================
# 🔥 获取持仓
# =========================
def get_positions():
    return mt5.positions_get()


# =========================
# 🔥 修改止损
# =========================
def modify_sl(position, new_sl):
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position.ticket,
        "sl": new_sl,
        "tp": position.tp
    }

    result = mt5.order_send(request)

    if result:
        print(f"🔄 SL已更新: {position.symbol} → {new_sl}")


# =========================
# 🔥 持仓管理（BE + trailing）
# =========================
def manage_positions():
    positions = get_positions()

    if positions is None:
        return

    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)

        if tick is None:
            continue

        price = tick.bid if pos.type == 1 else tick.ask

        entry = pos.price_open
        sl = pos.sl
        tp = pos.tp
        profit = pos.profit

        risk = abs(entry - sl)

        # =========================
        # 🔥 1. TP1 锁利润
        # =========================
        tp1 = entry + risk * 1.5 if pos.type == 0 else entry - risk * 1.5

        if pos.type == 0:  # BUY
            if price >= tp1:
                new_sl = entry + risk * 0.3
                if sl < new_sl:
                    modify_sl(pos, new_sl)
                    print(f"🔒 锁利润 BUY {pos.symbol}")

        else:  # SELL
            if price <= tp1:
                new_sl = entry - risk * 0.3
                if sl > new_sl:
                    modify_sl(pos, new_sl)
                    print(f"🔒 锁利润 SELL {pos.symbol}")

        # =========================
        # 🔥 2. BE（记录一次）
        # =========================
        if pos.type == 0:
            if price - entry >= risk:
                if sl < entry:
                    modify_sl(pos, entry)

                    log_trade(
                        action="BE",
                        symbol=pos.symbol,
                        direction="BUY",
                        lot=pos.volume,
                        entry=entry,
                        sl=entry,
                        tp=tp,
                        profit=profit
                    )

        else:
            if entry - price >= risk:
                if sl > entry:
                    modify_sl(pos, entry)

                    log_trade(
                        action="BE",
                        symbol=pos.symbol,
                        direction="SELL",
                        lot=pos.volume,
                        entry=entry,
                        sl=entry,
                        tp=tp,
                        profit=profit
                    )

        # =========================
        # 🔥 3. trailing（不记录）
        # =========================
        trailing = risk * 0.5

        if pos.type == 0:
            new_sl = price - trailing
            if new_sl > sl:
                modify_sl(pos, new_sl)
        else:
            new_sl = price + trailing
            if new_sl < sl:
                modify_sl(pos, new_sl)

#记录
def log_trade(action, symbol, direction, lot, entry, sl, tp, profit=0):

    file = "trade_log.csv"

    file_exists = os.path.isfile(file)

    with open(file, mode="a", newline="") as f:
        writer = csv.writer(f)

        # 写表头
        if not file_exists:
            writer.writerow([
                "time", "action", "symbol", "direction",
                "lot", "entry", "sl", "tp", "profit"
            ])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action,
            symbol,
            direction,
            lot,
            entry,
            sl,
            tp,
            profit
        ])