"""V4 Day-Scale Regime Detection — BTC 1h EMA-based regime + hysteresis.

跟 V3 regime_rules.py 的区别:
- V3 用 30min BTC close 跟 EMA(20) 比, 频繁切换
- V4 用 1h BTC close 跟 EMA(50) 比 + slope, 加 3-bar hysteresis 避免 flip-flop
- V4 加 ATR 检测 chop regime (低波动横盘)

输出: "up" / "chop" / "down" + 持续时长 + confidence.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class RegimeSnapshot:
    regime: str                  # "up" / "chop" / "down"
    confidence: float            # 0-1, 基于 ema_distance + slope 强度
    duration_hours: int          # 当前 regime 已持续小时数
    btc_close: float
    ema_50: float
    ema_slope_pct: float         # EMA 50 最近 6h 斜率 %
    atr_pct: float               # ATR(14) / close
    detected_at: str             # ISO ts


def detect_regime(btc_1h: pd.DataFrame, lookback: int = 200) -> RegimeSnapshot:
    """计算当前 (最后一根 K 线时刻) BTC regime.

    判定:
      up: close > ema50 * 1.005 + ema_slope > 0 + 3 根连续
      down: close < ema50 * 0.995 + ema_slope < 0 + 3 根连续
      chop: 其它 (或 atr% < 阈值标记为低波动 chop)
    """
    # TODO: 计算 EMA(50) + slope + ATR, 返回 RegimeSnapshot
    raise NotImplementedError


def regime_series(btc_1h: pd.DataFrame) -> pd.Series:
    """对整段 BTC 1h 历史每个时点算 regime, 返回 Series (index=open_time, value=regime str).

    用于回测时按时点 lookup. 含 hysteresis (3-bar 确认才切换).
    """
    # TODO: vectorized 算 EMA / slope, 状态机走 hysteresis
    raise NotImplementedError
