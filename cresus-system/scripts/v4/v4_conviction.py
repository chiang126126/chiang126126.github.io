"""V4 Conviction Score (0-10) — 基于日级 features 重新校准.

设计原则: 硬条件 vs 软加分严格分离.
  - 硬条件 (信号触发): 在 v4_signals.py 决定开不开仓
  - 软加分 (本文件): 同一信号的强度, 决定是否过 ≥6 门槛

Base: 3 分 (满足 v4_signals.py entry 硬条件)

软加分 (最多 +7, cap 10):

技术指标 (+3):
  +1: 1d volume > 2x MA (vs 信号触发的 1.5x base)
  +1: 4h MACD histogram 同向
  +1: 周线趋势同向 (5d MA > 20d MA for LONG)

宏观环境 (+2):
  +1: BTC 同向 1d 涨/跌 > 1.5%
  +1: regime 已确立 > 7 天 (regime stability)

资金流 / 主力意图 (+3, V3 验证有 alpha):
  +1: Funding 极端同向 (|funding_8h| ≥ 0.05%, LONG + funding 负 / SHORT + funding 正)
  +1: 24h OI delta 方向同向 (LONG + OI 增 > 2%, SHORT + OI 减 > 2%)
  +1: 24h taker buy ratio 方向同向 (LONG + ratio > 55%, SHORT + ratio < 45%)

历史胜率 (+1):
  +1: 该 symbol 最近 30 天 V4 信号胜率 > 50%
      第一版回测 → 用 V3 paper 30d 胜率作 prior (有偏差, conservatively 使用)
      第二版回测 → 用 V4 rolling 30d 胜率

Diamond 阈值: ≥ 6 (vs V3 ≥ 5).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from v4_signals import V4Signal


# 阈值常量 (调参靶子)
VOLUME_STRONG_MULT = 2.0          # 1d vol > 2x MA → +1 (vs 信号触发 1.5x)
BTC_ALIGN_PCT = 1.5               # BTC 1d 同向涨/跌 ≥ 1.5% → +1
REGIME_STABLE_HOURS = 7 * 24      # regime 持续 ≥ 7d → +1
FUNDING_EXTREME_PCT = 0.05        # |funding_8h| ≥ 0.05% → 极端 → +1 (同向)
OI_DELTA_PCT = 2.0                # |24h OI delta| ≥ 2% → +1 (同向)
TAKER_RATIO_LONG_THRESH = 0.55    # 24h taker buy ratio ≥ 55% → LONG +1
TAKER_RATIO_SHORT_THRESH = 0.45   # ≤ 45% → SHORT +1
HISTORY_WIN_RATE_THRESH = 0.50    # 30d 胜率 > 50% → +1

DIAMOND_THRESHOLD = 6             # ≥6 才 mirror live


@dataclass
class ConvictionBreakdown:
    base: int = 3
    vol_strong: int = 0
    macd_align: int = 0
    weekly_trend: int = 0
    btc_align: int = 0
    regime_stable: int = 0
    funding_extreme: int = 0
    oi_align: int = 0
    taker_align: int = 0
    history_winrate: int = 0

    @property
    def total(self) -> int:
        return min(10, sum([
            self.base, self.vol_strong, self.macd_align, self.weekly_trend,
            self.btc_align, self.regime_stable,
            self.funding_extreme, self.oi_align, self.taker_align,
            self.history_winrate,
        ]))


def compute_conviction(
    signal: V4Signal,
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    btc_1d: pd.DataFrame,
    funding_df: pd.DataFrame,                      # symbol funding 历史
    oi_df: pd.DataFrame,                           # symbol OI 历史 (4h)
    taker_df: pd.DataFrame,                        # symbol taker ratio 历史 (4h)
    symbol_history: dict,                          # {"win_rate_30d": float, "total_n": int}
    regime_duration_hours: int,
) -> tuple[int, ConvictionBreakdown]:
    """根据 9 个 feature 累加, 返回 (score, breakdown).

    Args:
        signal: V4Signal 实例
        df_1d: 该 symbol 1d K 线 (含到 signal.entry_time)
        df_4h: 该 symbol 4h K 线
        btc_1d: BTC 1d K 线
        funding_df: 该 symbol funding rate 历史 (8h 粒度)
        oi_df: 该 symbol OI 历史 (4h 粒度)
        taker_df: 该 symbol taker buy ratio (4h 粒度)
        symbol_history: 历史胜率
        regime_duration_hours: 当前 regime 已持续小时

    Returns:
        (final_score 0-10, breakdown)
    """
    # TODO: 9 个 feature 检测 + 累加, 返回 ConvictionBreakdown
    raise NotImplementedError
