"""V4 Indicators — ATR / RSI / EMA / SMA / Donchian / MACD / Volume MA / Wick ratio.

输入: pandas.DataFrame (open_time index, OHLCV columns) 或 pd.Series (close).
输出: pd.Series 跟输入 index 对齐.

设计原则:
- 纯函数, 无副作用
- 时间框无关 (传 1h df 算 1h indicator, 传 1d df 算 1d indicator)
- 边界: 数据不足时返回 NaN (不抛异常)
- Vectorized (NumPy/Pandas), 不写 Python loop

测试: 跟手算 + 已知数据对比 (TradingView/Investopedia 公式).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


# ── True Range / ATR ────────────────────────────────────────────────

def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|).

    第一行 prev_close NaN → TR = high - low.
    """
    if len(df) == 0:
        return pd.Series(dtype=float, index=df.index)
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # 第一行 NaN (没 prev_close), 用 tr1 兜底
    tr.iloc[0] = tr1.iloc[0]
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing).

    Wilder ATR = EMA with alpha = 1/N (NOT 2/(N+1) like normal EMA).
    pandas equivalent: ewm(alpha=1/N, adjust=False).mean()
    """
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ── RSI ─────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder).

    RS = avg_gain / avg_loss (both Wilder-smoothed)
    RSI = 100 - 100 / (1 + RS)

    边界处理:
      avg_loss == 0 且 avg_gain > 0 → 完全单调上涨 → RSI = 100
      avg_loss == 0 且 avg_gain == 0 → 无波动 → RSI = 50 (中性)
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, np.nan)
    result = 100 - 100 / (1 + rs)
    # avg_loss == 0: 单调走势 → 极端 RSI
    result = result.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    result = result.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return result


# ── EMA / SMA ───────────────────────────────────────────────────────

def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (standard, alpha = 2/(N+1))."""
    return close.ewm(span=period, adjust=False).mean()


def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=period, min_periods=period).mean()


# ── Donchian ────────────────────────────────────────────────────────

def donchian(df: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channel.

    upper = rolling N-bar max(high)
    lower = rolling N-bar min(low)
    mid = (upper + lower) / 2

    注意: 用 rolling N (含当根) 还是 rolling N (不含当根, shift 1)?
      标准 Donchian: 含当根 (TradingView default).
      但用作"突破"判断时, 需要 shift(1) (跟前 N 根比, 不含当前根) 才能避免 self-reference.
      本函数返回含当根版, 调用方按需 shift.
    """
    upper = df["high"].rolling(window=period, min_periods=period).max()
    lower = df["low"].rolling(window=period, min_periods=period).min()
    mid = (upper + lower) / 2.0
    return upper, lower, mid


# ── MACD ────────────────────────────────────────────────────────────

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD (line, signal, histogram).

    line = EMA(fast) - EMA(slow)
    signal_line = EMA(line, signal)
    histogram = line - signal_line
    """
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    line = ema_fast - ema_slow
    signal_line = ema(line, signal)
    histogram = line - signal_line
    return line, signal_line, histogram


# ── Volume MA ───────────────────────────────────────────────────────

def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Volume Moving Average (SMA on volume)."""
    return volume.rolling(window=period, min_periods=period).mean()


# ── Wick ratio (mean reversion 用) ────────────────────────────────

def wick_ratio(df: pd.DataFrame, side: str = "lower") -> pd.Series:
    """单根 K 影线 / body 比.

    side = "lower": lower_wick / max(body, eps), 反映下影 (LONG mean-rev 看大)
    side = "upper": upper_wick / max(body, eps), 反映上影 (SHORT mean-rev 看大)

    body = |close - open|
    lower_wick = min(open, close) - low
    upper_wick = high - max(open, close)

    return: 比值. body 为 0 时返回 NaN (避免除零).
    """
    if side not in ("lower", "upper"):
        raise ValueError(f"side must be 'lower' or 'upper', got {side!r}")
    open_ = df["open"]
    close = df["close"]
    high = df["high"]
    low = df["low"]
    body = (close - open_).abs()
    if side == "lower":
        wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    else:
        wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    body_safe = body.replace(0, np.nan)
    return wick / body_safe


# ── 工具: slope (regime detection 用) ──────────────────────────────

def slope_pct(series: pd.Series, lookback: int) -> pd.Series:
    """N-bar 斜率 (百分比变化).

    slope[t] = (series[t] - series[t-lookback]) / series[t-lookback] × 100
    """
    return (series - series.shift(lookback)) / series.shift(lookback) * 100.0
