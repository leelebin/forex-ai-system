import MetaTrader5 as mt5
import pandas as pd

def connect(cfg):
    if not mt5.initialize():
        print("❌ MT5 初始化失败")
        return False

    authorized = mt5.login(
        login=cfg["mt5_login"],
        password=cfg["mt5_password"],
        server=cfg["mt5_server"]
    )

    if not authorized:
        print("❌ MT5 登录失败")
        return False

    print("✅ MT5 连接成功")
    return True


def get_data(symbol, timeframe_str, n=1000):
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1
    }

    timeframe = tf_map.get(timeframe_str, mt5.TIMEFRAME_M5)

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)

    if rates is None:
        print("❌ 获取数据失败:", symbol)
        return []

    df = pd.DataFrame(rates)

    # ✅ 确保字段存在
    required_cols = ['time', 'open', 'high', 'low', 'close']
    for col in required_cols:
        if col not in df.columns:
            print("❌ 数据缺失字段:", df.columns)
            return []

    return df