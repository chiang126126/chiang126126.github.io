"""V4 Indicators — ATR / RSI / EMA / Donchian / MACD on arbitrary timeframe.

输入: pandas.DataFrame (open_time index, OHLCV columns).
输出: pandas.Series 跟输入 index 对齐.

设计原则:
- 纯函数, 无副作用
- 时间框无关 (传 1h df 算 1h indicator, 传 1d df 算 1d indicator)
- 边界: 数据不足时返回 NaN (不抛异常)
- Vectorized (NumPy/Pandas), 不写 Python loop

测试: tests/test_indicators.py 用已知数据 (TradingView 数值) 对比.
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing).

    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = Wilder EMA of TR (period N).
    """
    # TODO: implement
    raise NotImplementedError


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder)."""
    # TODO: implement
    raise NotImplementedError


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    # TODO: implement (pandas .ewm)
    raise NotImplementedError


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    # TODO: implement
    raise NotImplementedError


def donchian(df: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channel.

    Returns:
        (upper, lower, mid):
            upper = rolling N-bar max(high)
            lower = rolling N-bar min(low)
            mid = (upper + lower) / 2
    """
    # TODO: implement
    raise NotImplementedError


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (line, signal, histogram).

    line = EMA(fast) - EMA(slow)
    signal = EMA(line, signal_period)
    histogram = line - signal
    """
    # TODO: implement
    raise NotImplementedError


def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Volume Moving Average."""
    # TODO: implement
    raise NotImplementedError


def wick_ratio(df: pd.DataFrame, side: str = "lower") -> pd.Series:
    """单根 K 线影线比 (lower wick / body 或 upper wick / body).

    LONG mean-rev 用 lower wick ratio (下影长 → bullish reversal).
    SHORT mean-rev 用 upper wick ratio.
    """
    # TODO: implement
    raise NotImplementedError
