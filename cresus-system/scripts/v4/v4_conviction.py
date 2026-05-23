"""V4 Conviction Score (0-10) — 基于日级 features 重新校准.

Base: 4 分 (信号 entry 条件满足).
加分项 (最多 +6):
  +1: 1d volume > 2x MA (强量能, vs base 1.5x)
  +1: 4h MACD histogram 方向一致
  +1: BTC 1d 同向涨/跌 > 1.5%
  +1: 该 symbol 30 日历史胜率 > 50% (top decile)
  +1: 周线趋势同向 (5d MA > 20d MA for LONG)
  +1: regime 已确立 > 7 天 (regime stability bonus)

Cap: 10.
Diamond 阈值: ≥ 6 (vs V3 ≥ 5).
"""
from __future__ import annotations
from v4_signals import V4Signal
import pandas as pd


def compute_conviction(
    signal: V4Signal,
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    btc_1d: pd.DataFrame,
    symbol_history: dict,        # {"win_rate_30d": float, "total_n": int}
    regime_duration_hours: int,
) -> int:
    """根据 6 个 feature 加分, 返回最终 conviction 分数 (0-10).

    Args:
        signal: V4Signal 实例 (含 entry features)
        df_1d: 该 symbol 日 K 线 (含到 signal.entry_time)
        df_4h: 该 symbol 4h K 线
        btc_1d: BTC 日 K 线 (跟 signal.entry_time 对齐)
        symbol_history: 该 symbol 最近 30 日历史胜率
        regime_duration_hours: 当前 regime 已持续小时

    Returns:
        int 0-10
    """
    # TODO: 实现 6 个 feature 检测 + 累加
    raise NotImplementedError
