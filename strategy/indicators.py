import ta

def apply_indicators(df):
    df['rsi'] = ta.momentum.RSIIndicator(df['close']).rsi()
    df['ema_fast'] = ta.trend.EMAIndicator(df['close'], 9).ema_indicator()
    df['ema_slow'] = ta.trend.EMAIndicator(df['close'], 21).ema_indicator()

    # 🔥 新增 ATR
    df['atr'] = ta.volatility.AverageTrueRange(
        df['high'], df['low'], df['close']
    ).average_true_range()

    return df