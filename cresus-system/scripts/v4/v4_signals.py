"""V4 Signals — Hybrid regime-adaptive 信号生成.

3 个 sub-strategy:
  - Up regime → Long Breakout (Donchian 20d 上轨突破 + 量能)
  - Down regime → Short Breakout (Donchian 20d 下轨跌破 + 量能)
  - Chop regime → Mean Reversion (RSI 极端 + 影线确认)

每个 signal 输出统一 Signal dataclass, 后续 conviction 评分 + 回测引擎消费.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class V4Signal:
    symbol: str
    direction: str               # "LONG" / "SHORT"
    sub_strategy: str            # "breakout_long" / "breakout_short" / "mean_rev_long" / "mean_rev_short"
    entry_time: pd.Timestamp
    entry_price: float
    sl_price: float              # entry ∓ 2.0 × ATR(4h)
    tp1_price: float             # entry ± 2.0 × ATR(4h)
    tp2_price: float             # entry ± 4.0 × ATR(4h)
    tp3_price: float             # entry ± 6.0 × ATR(4h)
    atr_4h: float                # 用于后续 trail 计算
    btc_regime: str
    features: dict               # 信号触发时的特征 dict (供 conviction 评分用)


def check_long_breakout(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, t: pd.Timestamp, symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Up regime: 1d 突破 20d Donchian 上轨 + 1d vol > 1.5x MA(20)."""
    # TODO: 检 Donchian 突破 + volume 确认 + BTC 同向, 返回 Signal or None
    raise NotImplementedError


def check_short_breakout(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, t: pd.Timestamp, symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Down regime: 1d 跌破 20d Donchian 下轨 + 1d vol > 1.5x MA(20)."""
    # TODO: 镜像 long breakout
    raise NotImplementedError


def check_mean_rev_long(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Chop regime: RSI(14, 1d) < 30 + 4h 下影线 (lower wick > body × 2)."""
    # TODO: 检 RSI 超卖 + 影线确认, 返回 Signal or None
    raise NotImplementedError


def check_mean_rev_short(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Chop regime: RSI(14, 1d) > 70 + 4h 上影线."""
    # TODO: 镜像 mean_rev_long
    raise NotImplementedError


def check_all_signals(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> list[V4Signal]:
    """主调度: 按 regime 调对应 sub-strategy. 返回所有触发的 signal (通常 0-1 个)."""
    signals = []
    if btc_regime == "up":
        s = check_long_breakout(df_1d, df_4h, t, symbol, btc_regime)
        if s: signals.append(s)
    elif btc_regime == "down":
        s = check_short_breakout(df_1d, df_4h, t, symbol, btc_regime)
        if s: signals.append(s)
    elif btc_regime == "chop":
        for fn in (check_mean_rev_long, check_mean_rev_short):
            s = fn(df_1d, df_4h, df_1h, t, symbol, btc_regime)
            if s: signals.append(s)
    return signals
