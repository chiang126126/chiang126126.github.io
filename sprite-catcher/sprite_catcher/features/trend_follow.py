"""
趋势跟随信号（Module A 主力策略）。

入场条件（全部满足）：
1. 4H 收盘价 > EMA20 > EMA50（多头排列）
2. 4H EMA20 持续向上 ≥ 3 根
3. 1D 收盘突破前 N 日高点
4. 7d 持有人增速 ≥ 30%（可选输入；不可用时不阻塞但 strength 打折）

注意：
- 持仓时长本身不在这里决定（由策略层根据信号弱化时机决定）
- 这是"是否符合开新仓条件"的判定，而非"何时平仓"
"""

from .. import config
from ..models import Candle, TrendFollowSignal
from .indicators import consecutive_up_bars, ema


def _empty_signal(reason: str) -> TrendFollowSignal:
    return TrendFollowSignal(
        detected=False,
        strength=0.0,
        reason=reason,
        ema20=0.0,
        ema50=0.0,
        ema20_up_bars=0,
        daily_breakout=False,
        holders_growth_pct=0.0,
    )


def detect_trend_follow(
    candles_4h: list[Candle],
    candles_1d: list[Candle],
    *,
    holders_growth_7d_pct: float | None = None,
    ema_fast: int = config.TF_EMA_FAST,
    ema_slow: int = config.TF_EMA_SLOW,
    min_ema20_up_bars: int = config.TF_MIN_EMA_UP_BARS,
    daily_breakout_lookback: int = config.TF_DAILY_BREAKOUT_LOOKBACK,
    min_holders_growth_7d: float = config.TF_MIN_HOLDERS_GROWTH_7D,
) -> TrendFollowSignal:
    """
    判定一个 4H+1D 数据组合是否触发趋势跟随入场。

    candles 必须按时间升序，最后一根是当前 K 线（已收线）。
    """
    # 数据健全性
    if len(candles_4h) < ema_slow + min_ema20_up_bars:
        return _empty_signal("insufficient_4h_data")
    if len(candles_1d) < daily_breakout_lookback + 1:
        return _empty_signal("insufficient_1d_data")

    closes_4h = [c.close for c in candles_4h]
    ema20_series = ema(closes_4h, ema_fast)
    ema50_series = ema(closes_4h, ema_slow)
    if not ema20_series or not ema50_series:
        # 理论上数据健全性已保证非空；保底
        return _empty_signal("ema_empty")

    ema20_now = ema20_series[-1]
    ema50_now = ema50_series[-1]
    price_now = closes_4h[-1]

    # 条件 1: 多头排列
    if not (price_now > ema20_now > ema50_now):
        return _empty_signal(
            f"not_bullish_stack:price={price_now:.4f},"
            f"ema20={ema20_now:.4f},ema50={ema50_now:.4f}"
        )

    # 条件 2: EMA20 连续向上
    up_bars = consecutive_up_bars(ema20_series)
    if up_bars < min_ema20_up_bars:
        return _empty_signal(f"ema20_not_rising:bars={up_bars}")

    # 条件 3: 1D 突破前 N 日高点
    last_close_1d = candles_1d[-1].close
    prior_window = candles_1d[-(daily_breakout_lookback + 1) : -1]
    prior_high = max(c.high for c in prior_window)
    daily_breakout = last_close_1d > prior_high
    if not daily_breakout:
        return _empty_signal(
            f"no_daily_breakout:close={last_close_1d:.4f},"
            f"prior_high={prior_high:.4f}"
        )

    # 条件 4: 持有人增速
    if holders_growth_7d_pct is None:
        holders_strength_factor = 0.7   # 数据缺失时打 70% 折
        holders_for_record = 0.0
    elif holders_growth_7d_pct < min_holders_growth_7d:
        return _empty_signal(
            f"holders_growth_too_low:{holders_growth_7d_pct:.3f}"
        )
    else:
        holders_strength_factor = 1.0
        holders_for_record = holders_growth_7d_pct

    # 综合 strength：EMA20/50 间距 + 持有人因子，截断 0-1
    ema_spread = (ema20_now - ema50_now) / ema50_now if ema50_now > 0 else 0.0
    raw = ema_spread * 5.0 * holders_strength_factor
    strength = max(0.0, min(1.0, raw))

    return TrendFollowSignal(
        detected=True,
        strength=strength,
        reason="TREND_FOLLOW",
        ema20=ema20_now,
        ema50=ema50_now,
        ema20_up_bars=up_bars,
        daily_breakout=True,
        holders_growth_pct=holders_for_record,
    )
