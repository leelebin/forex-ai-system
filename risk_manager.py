import MetaTrader5 as mt5


def get_symbol_type(symbol):
    symbol = symbol.upper()

    if "XAU" in symbol or "XAG" in symbol:
        return "metal"

    elif "XBR" in symbol or "WTI" in symbol:
        return "oil"

    elif "BTC" in symbol or "ETH" in symbol:
        return "crypto"

    elif symbol.endswith("USD") and len(symbol) == 6:
        return "forex_major"

    elif len(symbol) == 6:
        return "forex_cross"

    elif any(x in symbol for x in ["US30", "NAS", "SPX", "GER", "UK"]):
        return "index"

    else:
        return "other"


def calculate_lot(symbol, sl_price, entry_price, balance, risk_percent=5):
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return 0.01

    risk_money = balance * (risk_percent / 100)
    if risk_money <= 0:
        return 0.01

    order_type = mt5.ORDER_TYPE_BUY if sl_price < entry_price else mt5.ORDER_TYPE_SELL
    loss_per_lot = abs(
        mt5.order_calc_profit(order_type, symbol, 1.0, entry_price, sl_price) or 0
    )

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if loss_per_lot <= 0:
        sl_distance = abs(entry_price - sl_price)
        if sl_distance == 0 or tick_value == 0 or tick_size == 0:
            return 0.01
        value_per_point = tick_value / tick_size
        loss_per_lot = sl_distance * value_per_point

    lot = risk_money / loss_per_lot

    # =========================
    # 🔥 品种分类限制（核心）
    # =========================
    symbol_type = get_symbol_type(symbol)

    if symbol_type == "forex_major":
        max_lot = 2.0

    elif symbol_type == "forex_cross":
        max_lot = 1.5

    elif symbol_type == "metal":
        max_lot = 1.0

    elif symbol_type == "oil":
        max_lot = 1.0

    elif symbol_type == "index":
        max_lot = 2.0

    elif symbol_type == "crypto":
        max_lot = 0.1

    else:
        max_lot = 1.0

    # =========================
    # 🔥 最终限制（防爆）
    # =========================
    lot = max(symbol_info.volume_min, lot)
    lot = min(lot, max_lot)
    lot = min(lot, symbol_info.volume_max)

    step = symbol_info.volume_step or 0.01
    lot = round(lot / step) * step
    lot = max(symbol_info.volume_min, min(lot, symbol_info.volume_max))

    precision = 0
    if "." in f"{step:.10f}".rstrip("0"):
        precision = len(f"{step:.10f}".rstrip("0").split(".")[1])

    return round(lot, precision)
