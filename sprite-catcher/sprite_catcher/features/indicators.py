"""
技术指标工具函数。

放这里的都是无副作用、可单独测试的纯数学工具，被多个信号模块复用。
"""

from ..models import Candle


def ema(values: list[float], period: int) -> list[float]:
    """
    指数移动均线。

    返回长度 = len(values) - period + 1（前 period-1 个点没法算）。
    第一个 EMA 值用前 period 个值的 SMA 做种子，后续按 alpha = 2/(period+1) 滑动。

    边界：
    - period <= 0 → ValueError
    - len(values) < period → 空列表
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(values) < period:
        return []

    alpha = 2.0 / (period + 1)
    sma_seed = sum(values[:period]) / period
    out: list[float] = [sma_seed]
    for v in values[period:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


def true_range(candle: Candle, prev_close: float | None) -> float:
    """
    单根 K 线的 True Range：
        TR = max(high-low, |high-prev_close|, |low-prev_close|)
    prev_close=None（第一根 K 线）时退化为 high - low。

    要求 high >= low（基本数据健全性）；否则返回 0.0。
    """
    if candle.high < candle.low:
        return 0.0
    if prev_close is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(candles: list[Candle], period: int = 14) -> list[float]:
    """
    平均真实波幅 ATR (Wilder's smoothing)。

    输出长度 = len(candles) - period + 1，对齐到 candles[period-1:]。
    种子：前 period 根 TR 的简单平均（对应 candles[period-1]）；
    后续按 Wilder 递推：ATR[t] = (ATR[t-1] × (period-1) + TR[t]) / period

    边界：
    - period <= 0 → ValueError
    - len(candles) < period → 空列表（连种子都算不出）
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(candles) < period:
        return []

    tr_values: list[float] = []
    prev_close: float | None = None
    for c in candles:
        tr_values.append(true_range(c, prev_close))
        prev_close = c.close

    seed = sum(tr_values[:period]) / period
    out: list[float] = [seed]
    for tr in tr_values[period:]:
        out.append((out[-1] * (period - 1) + tr) / period)
    return out


def consecutive_up_bars(values: list[float]) -> int:
    """
    从末尾向前数，连续严格递增的根数（不包括起点本身）。

    例：[1, 2, 3, 4]   → 3（4>3>2>1）
        [1, 2, 3, 3]   → 0（最后一根没涨）
        [1, 2, 2, 3]   → 1（最后一根涨了，再往前 2 没涨）
        [5]            → 0（单点）
        []             → 0
    """
    if len(values) < 2:
        return 0
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] > values[i - 1]:
            count += 1
        else:
            break
    return count
