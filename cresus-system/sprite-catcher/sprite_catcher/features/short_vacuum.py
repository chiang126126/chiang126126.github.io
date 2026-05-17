"""
空头真空信号检测：插针清算空头 + OI 骤降 → 庄失去拉抬动力。

逻辑：
1. 之前发生过明显拉盘（≥ 20%），否则没有"顶"可空
2. 观察窗（默认 30min）内 OI 下降 ≥ 阈值（默认 15%）
3. 同窗口内出现明显的上影线（spike-up wick），证明 OI 下降是来自
   "多头插针清算空头"而非"自然减仓"
4. 三条全满足 → SHORT_VACUUM

设计原则：纯函数，3 路时序数据传入，没有 I/O。
"""

from .. import config
from ..models import Candle, ShortVacuumSignal, TimeSeriesPoint


def _empty_signal(reason: str) -> ShortVacuumSignal:
    return ShortVacuumSignal(
        detected=False,
        strength=0.0,
        reason=reason,
        oi_drop_pct=0.0,
        max_wick_ratio=0.0,
        recent_pump_pct=0.0,
    )


def detect_short_vacuum(
    oi_series: list[TimeSeriesPoint],
    price_series: list[TimeSeriesPoint],
    short_candles: list[Candle],
    *,
    window_minutes: int = config.SV_WINDOW_MINUTES,
    oi_drop_threshold: float = config.SV_OI_DROP_THRESHOLD,
    wick_threshold: float = config.SV_WICK_THRESHOLD,
    min_recent_pump_pct: float = config.SV_MIN_RECENT_PUMP_PCT,
) -> ShortVacuumSignal:
    """
    检测空头真空。

    输入：
    - oi_series: 窗口内的 OI 序列（任意采样频率，但点数 ≥ 2）
    - price_series: 同窗口的价格序列，用于估算前置拉盘幅度
    - short_candles: 1m 级 K 线，用来抓插针。**只读最后 window_minutes 根**。

    注意：
    - oi_series 取窗口首尾两点算下降幅度（不要求等间隔）
    - 不要求 oi_series 和 price_series 严格时间对齐（各自独立判断）
    - short_candles 不足 window_minutes 根则视为数据不够
    """
    if len(oi_series) < 2:
        return _empty_signal("oi_series_too_short")
    if len(price_series) < 2:
        return _empty_signal("price_series_too_short")
    if len(short_candles) < window_minutes:
        return _empty_signal("not_enough_candles")

    # === OI 下降幅度 ===
    oi_start = oi_series[0].value
    oi_end = oi_series[-1].value
    if oi_start <= 0:
        return _empty_signal("oi_start_non_positive")
    oi_drop_pct = (oi_start - oi_end) / oi_start
    if oi_drop_pct < oi_drop_threshold:
        return _empty_signal(f"oi_drop_too_small:{oi_drop_pct:.3f}")

    # === 插针检测：窗口内最大上影线 / close ===
    window_candles = short_candles[-window_minutes:]
    max_wick_ratio = 0.0
    for c in window_candles:
        if c.close <= 0:
            continue
        body_top = max(c.open, c.close)
        wick = c.high - body_top
        if wick <= 0:
            continue
        ratio = wick / c.close
        if ratio > max_wick_ratio:
            max_wick_ratio = ratio

    if max_wick_ratio < wick_threshold:
        return _empty_signal(f"no_significant_wick:{max_wick_ratio:.3f}")

    # === 前置拉盘 ===
    # 用价格序列的 (max - min) / min 估算窗口内的最大涨幅
    price_low = min(p.value for p in price_series)
    price_high = max(p.value for p in price_series)
    if price_low <= 0:
        return _empty_signal("price_low_non_positive")
    recent_pump_pct = (price_high - price_low) / price_low
    if recent_pump_pct < min_recent_pump_pct:
        return _empty_signal(f"no_recent_pump:{recent_pump_pct:.3f}")

    # === 全部通过 ===
    # strength = OI 下降幅度 + 插针强度的加权
    strength = min(
        1.0,
        0.5 * (oi_drop_pct / (oi_drop_threshold * 2.0))
        + 0.5 * (max_wick_ratio / (wick_threshold * 2.0)),
    )

    return ShortVacuumSignal(
        detected=True,
        strength=strength,
        reason="SHORT_VACUUM",
        oi_drop_pct=oi_drop_pct,
        max_wick_ratio=max_wick_ratio,
        recent_pump_pct=recent_pump_pct,
    )
