"""
技术指标工具函数。

放这里的都是无副作用、可单独测试的纯数学工具，被多个信号模块复用。
"""


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
