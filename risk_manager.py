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

    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size

    if tick_value == 0 or tick_size == 0:
        return 0.01

    sl_distance = abs(entry_price - sl_price)

    if sl_distance == 0:
        return 0.01

    value_per_point = tick_value / tick_size

    lot = risk_money / (sl_distance * value_per_point)

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

    return round(lot, 2)