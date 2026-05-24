"""V4 Conviction Score (0-10) — 基于日级 features 重新校准.

设计原则: 硬条件 vs 软加分严格分离.
  - 硬条件 (信号触发): 在 v4_signals.py 决定开不开仓
  - 软加分 (本文件): 同一信号的强度, 决定是否过 ≥6 门槛

Base: 3 分 (满足 v4_signals.py entry 硬条件)

软加分 (最多 +7, cap 10):

技术指标 (+3):
  +1 vol_strong:   1d volume > 2× MA(20) (vs 信号触发的 1.5x base)
  +1 macd_align:   4h MACD histogram 同向 (LONG+pos, SHORT+neg)
  +1 weekly_trend: 周线趋势同向 (5d MA 比 20d MA, LONG 高 / SHORT 低)

宏观环境 (+2):
  +1 btc_align:    BTC 同向 1d 涨/跌 ≥ 1.5%
  +1 regime_stable: regime 已确立 ≥ 7 天 (168 小时)

资金流 / 主力意图 (+3, V3 验证有 alpha):
  +1 funding_extreme: |funding_8h| ≥ 0.05% 反向 (LONG+负费率 / SHORT+正费率)
  +1 oi_align:     24h OI delta 方向同向 (LONG+OI 增 ≥ 2%, SHORT+OI 减 ≥ 2%)
  +1 taker_align:  24h taker_buy_ratio 方向同向 (LONG ≥ 55%, SHORT ≤ 45%)

历史胜率 (+1):
  +1 history_winrate: symbol 最近 30 天信号胜率 > 50% (≥5 笔样本)

Diamond 阈值: ≥ 6 (vs V3 ≥ 5).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import pandas as pd

from v4_signals import V4Signal
from v4_indicators import macd, sma


# 阈值常量
VOLUME_STRONG_MULT = 2.0          # 1d vol > 2× MA → +1
MACD_FAST = 12                    # 4h MACD 快线 (跟 v4_indicators 默认一致)
MACD_SLOW = 26
MACD_SIGNAL = 9
WEEKLY_MA_SHORT = 5               # 周线趋势短期 MA (5d)
WEEKLY_MA_LONG = 20               # 周线趋势长期 MA (20d)
BTC_ALIGN_PCT = 1.5               # BTC 1d 同向 ≥ 1.5% → +1
REGIME_STABLE_HOURS = 7 * 24      # regime 持续 ≥ 168h → +1
FUNDING_EXTREME_PCT = 0.05        # |funding| ≥ 0.05% → +1 (反向)
OI_DELTA_PCT = 2.0                # |24h OI delta| ≥ 2% 同向 → +1
TAKER_RATIO_LONG_THRESH = 0.55    # LONG taker_buy_ratio ≥ 55% → +1
TAKER_RATIO_SHORT_THRESH = 0.45   # SHORT taker_buy_ratio ≤ 45% → +1
HISTORY_WIN_RATE_THRESH = 0.50    # 30d 胜率 > 50% → +1
HISTORY_MIN_SAMPLES = 5           # 至少 5 笔历史才算

DIAMOND_THRESHOLD = 6              # ≥6 才 mirror live
CAP = 10                           # 最大 conviction


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
        return min(CAP, sum([
            self.base, self.vol_strong, self.macd_align, self.weekly_trend,
            self.btc_align, self.regime_stable,
            self.funding_extreme, self.oi_align, self.taker_align,
            self.history_winrate,
        ]))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d


# ── Per-feature 检测函数 (各自独立, 便于测试) ──────────────────────

def _check_vol_strong(df_1d_to_t: pd.DataFrame) -> int:
    if len(df_1d_to_t) < WEEKLY_MA_LONG:
        return 0
    last_vol = df_1d_to_t["volume"].iloc[-1]
    vol_ma = df_1d_to_t["volume"].rolling(WEEKLY_MA_LONG).mean().iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0:
        return 0
    return 1 if last_vol >= VOLUME_STRONG_MULT * vol_ma else 0


def _check_macd_align(df_4h_to_t: pd.DataFrame, direction: str) -> int:
    if len(df_4h_to_t) < MACD_SLOW + MACD_SIGNAL:
        return 0
    _, _, hist = macd(df_4h_to_t["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    last_hist = hist.iloc[-1]
    if pd.isna(last_hist):
        return 0
    if direction == "LONG" and last_hist > 0:
        return 1
    if direction == "SHORT" and last_hist < 0:
        return 1
    return 0


def _check_weekly_trend(df_1d_to_t: pd.DataFrame, direction: str) -> int:
    if len(df_1d_to_t) < WEEKLY_MA_LONG:
        return 0
    ma_short = sma(df_1d_to_t["close"], WEEKLY_MA_SHORT).iloc[-1]
    ma_long = sma(df_1d_to_t["close"], WEEKLY_MA_LONG).iloc[-1]
    if pd.isna(ma_short) or pd.isna(ma_long):
        return 0
    if direction == "LONG" and ma_short > ma_long:
        return 1
    if direction == "SHORT" and ma_short < ma_long:
        return 1
    return 0


def _check_btc_align(btc_1d_to_t: pd.DataFrame, direction: str) -> int:
    if len(btc_1d_to_t) < 2:
        return 0
    btc_change_1d = (btc_1d_to_t["close"].iloc[-1] / btc_1d_to_t["close"].iloc[-2] - 1) * 100
    if direction == "LONG" and btc_change_1d >= BTC_ALIGN_PCT:
        return 1
    if direction == "SHORT" and btc_change_1d <= -BTC_ALIGN_PCT:
        return 1
    return 0


def _check_regime_stable(regime_duration_hours: int) -> int:
    return 1 if regime_duration_hours >= REGIME_STABLE_HOURS else 0


def _check_funding_extreme(funding_to_t: pd.DataFrame, direction: str) -> int:
    """LONG 期望负费率 (空方付钱, 多方有利, 反向极端).
    SHORT 期望正费率 (多方付钱, 空方有利)."""
    if funding_to_t.empty:
        return 0
    last_funding = funding_to_t["funding_rate"].iloc[-1]   # decimal (0.0001 = 0.01%)
    if pd.isna(last_funding):
        return 0
    threshold_decimal = FUNDING_EXTREME_PCT / 100   # 0.0005
    if direction == "LONG" and last_funding <= -threshold_decimal:
        return 1
    if direction == "SHORT" and last_funding >= threshold_decimal:
        return 1
    return 0


def _check_oi_align(oi_to_t: pd.DataFrame, t: pd.Timestamp, direction: str) -> int:
    """24h OI delta 方向同向."""
    if oi_to_t.empty:
        return 0
    cutoff = t - pd.Timedelta(hours=24)
    oi_24h_ago = oi_to_t.loc[:cutoff]
    if oi_24h_ago.empty:
        return 0
    oi_now = oi_to_t["sum_oi"].iloc[-1]
    oi_then = oi_24h_ago["sum_oi"].iloc[-1]
    if pd.isna(oi_now) or pd.isna(oi_then) or oi_then <= 0:
        return 0
    oi_change_pct = (oi_now / oi_then - 1) * 100
    if direction == "LONG" and oi_change_pct >= OI_DELTA_PCT:
        return 1
    if direction == "SHORT" and oi_change_pct <= -OI_DELTA_PCT:
        return 1
    return 0


def _check_taker_align(taker_to_t: pd.DataFrame, direction: str) -> int:
    if taker_to_t.empty:
        return 0
    last_ratio = taker_to_t["taker_buy_ratio"].iloc[-1]
    if pd.isna(last_ratio):
        return 0
    if direction == "LONG" and last_ratio >= TAKER_RATIO_LONG_THRESH:
        return 1
    if direction == "SHORT" and last_ratio <= TAKER_RATIO_SHORT_THRESH:
        return 1
    return 0


def _check_history_winrate(symbol_history: dict) -> int:
    win_rate = symbol_history.get("win_rate_30d", 0.0)
    n = symbol_history.get("total_n", 0)
    if n < HISTORY_MIN_SAMPLES:
        return 0
    return 1 if win_rate > HISTORY_WIN_RATE_THRESH else 0


# ── 主入口 ─────────────────────────────────────────────────────

def compute_conviction(
    signal: V4Signal,
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    btc_1d: pd.DataFrame,
    funding_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    taker_df: pd.DataFrame,
    symbol_history: dict,
    regime_duration_hours: int,
) -> tuple[int, ConvictionBreakdown]:
    """根据 9 个 feature 累加, 返回 (total_score, breakdown).

    各 df 不足 / 空 → 对应 feature = 0 (优雅 degrade).

    Returns:
        (final_score 0-10, ConvictionBreakdown)
    """
    t = signal.entry_time
    direction = signal.direction

    # 切片到 t 时刻 (避免 look-ahead)
    df_1d_to_t = df_1d.loc[:t] if not df_1d.empty else df_1d
    df_4h_to_t = df_4h.loc[:t] if not df_4h.empty else df_4h
    btc_1d_to_t = btc_1d.loc[:t] if not btc_1d.empty else btc_1d
    funding_to_t = funding_df.loc[:t] if not funding_df.empty else funding_df
    oi_to_t = oi_df.loc[:t] if not oi_df.empty else oi_df
    taker_to_t = taker_df.loc[:t] if not taker_df.empty else taker_df

    breakdown = ConvictionBreakdown(
        base=3,
        vol_strong=_check_vol_strong(df_1d_to_t),
        macd_align=_check_macd_align(df_4h_to_t, direction),
        weekly_trend=_check_weekly_trend(df_1d_to_t, direction),
        btc_align=_check_btc_align(btc_1d_to_t, direction),
        regime_stable=_check_regime_stable(regime_duration_hours),
        funding_extreme=_check_funding_extreme(funding_to_t, direction),
        oi_align=_check_oi_align(oi_to_t, t, direction),
        taker_align=_check_taker_align(taker_to_t, direction),
        history_winrate=_check_history_winrate(symbol_history),
    )

    return breakdown.total, breakdown
