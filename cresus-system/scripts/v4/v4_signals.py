"""V4 Signals — Hybrid regime-adaptive 信号生成.

3 个 sub-strategy:
  - Up regime → Long Breakout (Donchian 20d 上轨突破 + 量能)
  - Down regime → Short Breakout (Donchian 20d 下轨跌破 + 量能)
  - Chop regime → Mean Reversion (RSI 极端 + 4h 影线 + 1h 量能)

每个 signal 输出统一 V4Signal dataclass, 后续 conviction 评分 + 回测引擎消费.

数据使用规则 (避免 look-ahead bias):
- 所有 df_* 用 .loc[:t] 切片, 只用 t 时刻及之前数据
- Donchian 用 .rolling(20).max/min.shift(1), 比较的是"今日 close vs 过去 20 日 (不含今日)"
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from v4_indicators import atr, rsi


# ── 信号触发阈值 (调参靶子) ────────────────────────────────────────

# Breakout 共用
BREAKOUT_DONCHIAN_PERIOD = 20      # Donchian 20 日
BREAKOUT_VOLUME_MA_PERIOD = 20     # 量能 MA 周期 (1d)
BREAKOUT_VOLUME_MULT = 1.5         # 量能确认: vol > 1.5 × MA

# Mean reversion 共用
MEANREV_RSI_PERIOD = 14            # RSI 周期 (1d)
MEANREV_RSI_OVERSOLD = 30.0        # LONG: RSI < 30
MEANREV_RSI_OVERBOUGHT = 70.0      # SHORT: RSI > 70
MEANREV_WICK_BODY_RATIO = 2.0      # 4h 影线 / body > 2
MEANREV_VOLUME_MA_PERIOD = 24      # 1h 量能 MA 周期 (24h)
MEANREV_VOLUME_MULT = 1.5          # 量能确认: vol > 1.5 × MA

# SL/TP (基于 ATR(4h))
ATR_PERIOD = 14                    # ATR Wilder 周期 (4h)
SL_ATR_MULT = 2.0                  # SL: entry ∓ 2 × ATR
TP1_ATR_MULT = 2.0                 # TP1: entry ± 2 × ATR
TP2_ATR_MULT = 4.0                 # TP2: entry ± 4 × ATR
TP3_ATR_MULT = 6.0                 # TP3: entry ± 6 × ATR


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
    features: dict               # 触发时的特征 dict (供 conviction 评分用)


# ── 辅助 ───────────────────────────────────────────────────────────

def _get_atr_4h(df_4h: pd.DataFrame, t: pd.Timestamp) -> Optional[float]:
    """计算 t 时刻 ATR(4h, 14). 数据不足或 NaN 返回 None."""
    df = df_4h.loc[:t]
    if len(df) < ATR_PERIOD + 1:
        return None
    atr_val = atr(df, period=ATR_PERIOD).iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        return None
    return float(atr_val)


def _build_long_signal(symbol: str, t: pd.Timestamp, entry: float, atr_val: float,
                       sub_strategy: str, btc_regime: str, features: dict) -> V4Signal:
    """LONG signal SL/TP 计算 (entry 下方 SL, entry 上方 TP)."""
    return V4Signal(
        symbol=symbol,
        direction="LONG",
        sub_strategy=sub_strategy,
        entry_time=t,
        entry_price=entry,
        sl_price=entry - SL_ATR_MULT * atr_val,
        tp1_price=entry + TP1_ATR_MULT * atr_val,
        tp2_price=entry + TP2_ATR_MULT * atr_val,
        tp3_price=entry + TP3_ATR_MULT * atr_val,
        atr_4h=atr_val,
        btc_regime=btc_regime,
        features=features,
    )


def _build_short_signal(symbol: str, t: pd.Timestamp, entry: float, atr_val: float,
                        sub_strategy: str, btc_regime: str, features: dict) -> V4Signal:
    """SHORT signal SL/TP 计算 (entry 上方 SL, entry 下方 TP)."""
    return V4Signal(
        symbol=symbol,
        direction="SHORT",
        sub_strategy=sub_strategy,
        entry_time=t,
        entry_price=entry,
        sl_price=entry + SL_ATR_MULT * atr_val,
        tp1_price=entry - TP1_ATR_MULT * atr_val,
        tp2_price=entry - TP2_ATR_MULT * atr_val,
        tp3_price=entry - TP3_ATR_MULT * atr_val,
        atr_4h=atr_val,
        btc_regime=btc_regime,
        features=features,
    )


# ── Long Breakout (Up regime) ──────────────────────────────────

def check_long_breakout(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, t: pd.Timestamp, symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Up regime: 1d 突破 20d Donchian 上轨 + 1d vol > 1.5×MA(20).

    入场: 当日 close 价.
    """
    if btc_regime != "up":
        return None

    df_1d_to_t = df_1d.loc[:t]
    # 需 ≥ 21 根 (20 日 Donchian + 1 shift)
    if len(df_1d_to_t) < BREAKOUT_DONCHIAN_PERIOD + 1:
        return None

    last_close = float(df_1d_to_t["close"].iloc[-1])
    last_volume = float(df_1d_to_t["volume"].iloc[-1])

    # Donchian 上轨 (过去 20 日 high 的最大值, 不含今日)
    upper_donchian = df_1d_to_t["high"].rolling(BREAKOUT_DONCHIAN_PERIOD).max().shift(1).iloc[-1]
    if pd.isna(upper_donchian) or last_close <= upper_donchian:
        return None

    # 量能确认
    vol_ma = df_1d_to_t["volume"].rolling(BREAKOUT_VOLUME_MA_PERIOD).mean().iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0 or last_volume < BREAKOUT_VOLUME_MULT * vol_ma:
        return None

    atr_val = _get_atr_4h(df_4h, t)
    if atr_val is None:
        return None

    return _build_long_signal(
        symbol=symbol, t=t, entry=last_close, atr_val=atr_val,
        sub_strategy="breakout_long", btc_regime=btc_regime,
        features={
            "donchian_upper": float(upper_donchian),
            "volume_ratio": float(last_volume / vol_ma),
            "breakout_close": last_close,
        },
    )


# ── Short Breakout (Down regime) ─────────────────────────────────

def check_short_breakout(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, t: pd.Timestamp, symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Down regime: 1d 跌破 20d Donchian 下轨 + 1d vol > 1.5×MA(20)."""
    if btc_regime != "down":
        return None

    df_1d_to_t = df_1d.loc[:t]
    if len(df_1d_to_t) < BREAKOUT_DONCHIAN_PERIOD + 1:
        return None

    last_close = float(df_1d_to_t["close"].iloc[-1])
    last_volume = float(df_1d_to_t["volume"].iloc[-1])

    lower_donchian = df_1d_to_t["low"].rolling(BREAKOUT_DONCHIAN_PERIOD).min().shift(1).iloc[-1]
    if pd.isna(lower_donchian) or last_close >= lower_donchian:
        return None

    vol_ma = df_1d_to_t["volume"].rolling(BREAKOUT_VOLUME_MA_PERIOD).mean().iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0 or last_volume < BREAKOUT_VOLUME_MULT * vol_ma:
        return None

    atr_val = _get_atr_4h(df_4h, t)
    if atr_val is None:
        return None

    return _build_short_signal(
        symbol=symbol, t=t, entry=last_close, atr_val=atr_val,
        sub_strategy="breakout_short", btc_regime=btc_regime,
        features={
            "donchian_lower": float(lower_donchian),
            "volume_ratio": float(last_volume / vol_ma),
            "breakdown_close": last_close,
        },
    )


# ── Mean Reversion (Chop regime) ─────────────────────────────────

def _check_mean_rev_common(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    btc_regime: str, side: str,
) -> Optional[tuple]:
    """Mean rev 共用前置检查 (regime / RSI / wick / 量能).

    Returns:
        None — 不触发
        (rsi_val, wick_ratio, vol_ratio, entry_price, atr_val) — 触发, 给 build_*_signal 用
    """
    if btc_regime != "chop":
        return None

    # RSI(14, 1d)
    df_1d_to_t = df_1d.loc[:t]
    if len(df_1d_to_t) < MEANREV_RSI_PERIOD + 1:
        return None
    rsi_val = rsi(df_1d_to_t["close"], period=MEANREV_RSI_PERIOD).iloc[-1]
    if pd.isna(rsi_val):
        return None
    if side == "long" and rsi_val >= MEANREV_RSI_OVERSOLD:
        return None
    if side == "short" and rsi_val <= MEANREV_RSI_OVERBOUGHT:
        return None

    # 4h 影线
    df_4h_to_t = df_4h.loc[:t]
    if len(df_4h_to_t) < 1:
        return None
    bar_4h = df_4h_to_t.iloc[-1]
    o, c, h, l = float(bar_4h["open"]), float(bar_4h["close"]), float(bar_4h["high"]), float(bar_4h["low"])
    body = abs(c - o)
    if body <= 0:
        return None
    if side == "long":
        wick = min(o, c) - l   # 下影
    else:
        wick = h - max(o, c)   # 上影
    if wick < 0:
        return None
    wick_body_ratio = wick / body
    if wick_body_ratio < MEANREV_WICK_BODY_RATIO:
        return None

    # 1h 量能
    df_1h_to_t = df_1h.loc[:t]
    if len(df_1h_to_t) < MEANREV_VOLUME_MA_PERIOD:
        return None
    last_vol = float(df_1h_to_t["volume"].iloc[-1])
    vol_ma = df_1h_to_t["volume"].rolling(MEANREV_VOLUME_MA_PERIOD).mean().iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0 or last_vol < MEANREV_VOLUME_MULT * vol_ma:
        return None

    # 入场: 1h K 收盘价
    entry_price = float(df_1h_to_t["close"].iloc[-1])

    # ATR(4h) for SL/TP
    atr_val = _get_atr_4h(df_4h, t)
    if atr_val is None:
        return None

    return (float(rsi_val), wick_body_ratio, last_vol / vol_ma, entry_price, atr_val)


def check_mean_rev_long(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Chop regime: RSI(14,1d) < 30 + 4h 下影/body > 2 + 1h vol > 1.5×MA(24)."""
    result = _check_mean_rev_common(df_1d, df_4h, df_1h, t, btc_regime, "long")
    if result is None:
        return None
    rsi_val, wick_ratio_v, vol_ratio, entry_price, atr_val = result
    return _build_long_signal(
        symbol=symbol, t=t, entry=entry_price, atr_val=atr_val,
        sub_strategy="mean_rev_long", btc_regime=btc_regime,
        features={
            "rsi_14_1d": rsi_val,
            "lower_wick_ratio": wick_ratio_v,
            "volume_ratio_1h": vol_ratio,
        },
    )


def check_mean_rev_short(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> Optional[V4Signal]:
    """Chop regime: RSI(14,1d) > 70 + 4h 上影/body > 2 + 1h vol > 1.5×MA(24)."""
    result = _check_mean_rev_common(df_1d, df_4h, df_1h, t, btc_regime, "short")
    if result is None:
        return None
    rsi_val, wick_ratio_v, vol_ratio, entry_price, atr_val = result
    return _build_short_signal(
        symbol=symbol, t=t, entry=entry_price, atr_val=atr_val,
        sub_strategy="mean_rev_short", btc_regime=btc_regime,
        features={
            "rsi_14_1d": rsi_val,
            "upper_wick_ratio": wick_ratio_v,
            "volume_ratio_1h": vol_ratio,
        },
    )


# ── 主调度 ─────────────────────────────────────────────────────────

def check_all_signals(
    df_1d: pd.DataFrame, df_4h: pd.DataFrame, df_1h: pd.DataFrame, t: pd.Timestamp,
    symbol: str, btc_regime: str,
) -> list[V4Signal]:
    """主调度: 按 regime 调对应 sub-strategy. 返回所有触发的 signal (通常 0-1 个)."""
    signals: list[V4Signal] = []
    if btc_regime == "up":
        s = check_long_breakout(df_1d, df_4h, t, symbol, btc_regime)
        if s:
            signals.append(s)
    elif btc_regime == "down":
        s = check_short_breakout(df_1d, df_4h, t, symbol, btc_regime)
        if s:
            signals.append(s)
    elif btc_regime == "chop":
        for fn in (check_mean_rev_long, check_mean_rev_short):
            s = fn(df_1d, df_4h, df_1h, t, symbol, btc_regime)
            if s:
                signals.append(s)
    return signals
