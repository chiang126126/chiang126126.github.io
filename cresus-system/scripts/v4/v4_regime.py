"""V4 Day-Scale Regime Detection — BTC 1h EMA-based regime + hysteresis.

跟 V3 regime_rules.py 的区别:
- V3 用 30min BTC close 跟 EMA(20) 比, 频繁切换
- V4 用 1h BTC close 跟 EMA(50) 比 + slope, 加 3-bar hysteresis 避免 flip-flop
- V4 加 ATR 检测 chop regime (低波动横盘)

输出: "up" / "chop" / "down" + 持续时长 + confidence.

判定条件 (跟 V4_SPEC §2.1 一致):
  Up:   close > ema_50 × 1.005  AND  ema_slope(6h) > 0
  Down: close < ema_50 × 0.995  AND  ema_slope(6h) < 0
  Chop: 其它
  Hysteresis: 连续 3 根 1h K 线满足相同 raw regime 才切换 current regime
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd

from v4_indicators import ema, slope_pct, atr


# 配置常量 (调参靶子)
EMA_PERIOD = 50                  # BTC 1h EMA 周期
EMA_DISTANCE_BAND_PCT = 0.005    # close 跟 ema 偏离 ≥ 0.5% 才有方向意义
SLOPE_LOOKBACK_BARS = 6          # EMA slope 用 6 根 1h (= 6h)
HYSTERESIS_BARS = 3              # 连续 N 根 raw 一致才切换 current regime
ATR_PERIOD = 14                  # ATR(14) for atr_pct


@dataclass
class RegimeSnapshot:
    regime: str                  # "up" / "chop" / "down"
    confidence: float            # 0-1, 基于 ema_distance + slope 强度
    duration_hours: int          # 当前 regime 已持续小时数
    btc_close: float
    ema_50: float
    ema_slope_pct: float         # EMA 50 最近 6h 斜率 %
    atr_pct: float               # ATR(14) / close × 100
    detected_at: str             # ISO ts


def _compute_raw_regime(btc_1h: pd.DataFrame) -> pd.Series:
    """每个时点的 raw regime (无 hysteresis).

    数据不足 / NaN 时默认 'chop' (NaN 比较返回 False, mask 落到 chop).
    """
    close = btc_1h["close"]
    ema_50 = ema(close, EMA_PERIOD)
    ema_slope = slope_pct(ema_50, SLOPE_LOOKBACK_BARS)

    raw = pd.Series("chop", index=close.index, dtype="object")

    up_mask = (close > ema_50 * (1 + EMA_DISTANCE_BAND_PCT)) & (ema_slope > 0)
    down_mask = (close < ema_50 * (1 - EMA_DISTANCE_BAND_PCT)) & (ema_slope < 0)

    raw[up_mask] = "up"
    raw[down_mask] = "down"

    return raw


def regime_series(btc_1h: pd.DataFrame) -> pd.Series:
    """对整段 BTC 1h 历史每个时点算 regime, 返回 Series.

    含 hysteresis: 连续 HYSTERESIS_BARS 根 raw 一致才切换 current regime.

    Returns:
        pd.Series with index=open_time, value in {"up", "chop", "down"}.
    """
    if btc_1h.empty:
        return pd.Series([], dtype="object", index=btc_1h.index)

    raw = _compute_raw_regime(btc_1h)

    # Hysteresis 状态机
    current = "chop"
    streak_regime = "chop"
    streak_count = 0
    result = []

    for r in raw:
        if r == streak_regime:
            streak_count += 1
        else:
            streak_regime = r
            streak_count = 1

        if streak_count >= HYSTERESIS_BARS and streak_regime != current:
            current = streak_regime

        result.append(current)

    return pd.Series(result, index=raw.index, dtype="object")


def detect_regime(btc_1h: pd.DataFrame, lookback: int = 200) -> RegimeSnapshot:
    """计算最后一根 K 线时刻的 BTC regime snapshot.

    取尾部 `lookback` 根算, EMA(50) 需要 ≥ 50 根才稳定.

    Raises:
        ValueError: 输入 DataFrame 为空.
    """
    if btc_1h.empty:
        raise ValueError("btc_1h is empty")

    df = btc_1h.tail(lookback)
    series = regime_series(df)

    current_regime = series.iloc[-1]

    # duration_hours: 从尾部回看, 连续 current_regime 的根数
    duration_hours = 0
    for r in reversed(series.tolist()):
        if r == current_regime:
            duration_hours += 1
        else:
            break

    # 提取指标值用于 snapshot
    close = df["close"]
    ema_50_series = ema(close, EMA_PERIOD)
    ema_slope_series = slope_pct(ema_50_series, SLOPE_LOOKBACK_BARS)
    atr_series = atr(df, period=ATR_PERIOD)

    last_close = float(close.iloc[-1])
    last_ema = ema_50_series.iloc[-1]
    last_slope = ema_slope_series.iloc[-1]
    last_atr = atr_series.iloc[-1]

    # Confidence (0-1): 偏离 + slope 强度的合成
    if pd.notna(last_ema) and last_ema > 0:
        ema_dist_pct = abs(last_close - last_ema) / last_ema * 100
    else:
        ema_dist_pct = 0.0
    slope_magnitude = abs(last_slope) if pd.notna(last_slope) else 0.0
    confidence = min(1.0, (ema_dist_pct / 2.0 + slope_magnitude / 1.0) / 2.0)

    atr_pct = (last_atr / last_close * 100) if pd.notna(last_atr) and last_close > 0 else 0.0

    detected_at = df.index[-1].isoformat() if hasattr(df.index[-1], "isoformat") else str(df.index[-1])

    return RegimeSnapshot(
        regime=current_regime,
        confidence=float(confidence),
        duration_hours=duration_hours,
        btc_close=last_close,
        ema_50=float(last_ema) if pd.notna(last_ema) else 0.0,
        ema_slope_pct=float(last_slope) if pd.notna(last_slope) else 0.0,
        atr_pct=float(atr_pct),
        detected_at=detected_at,
    )
