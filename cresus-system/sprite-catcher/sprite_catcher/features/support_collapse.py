"""
支撑崩塌信号检测：庄拉完后支撑守不住。

逻辑：
1. 在过去 N 根 K 线里找出 peak（最高 high）
2. peak 之前要有过明显的拉盘（base_low → peak.high 涨幅 ≥ 40%）
3. peak 之后至少经过 min_bars_since_peak 根 K 线（让庄家有时间出货 / 散户接盘）
4. 把 peak 之后（不含当前）的 lows 最小值作为"派发区支撑"
5. 当前 K 线收盘跌破该支撑
6. 当前 K 线量能放大确认（不是缩量假跌）

设计原则：纯函数，K 线列表传入，没有 I/O。
"""

from .. import config
from ..models import Candle, SupportCollapseSignal


def _empty_signal(reason: str) -> SupportCollapseSignal:
    return SupportCollapseSignal(
        detected=False,
        strength=0.0,
        reason=reason,
        pump_pct=0.0,
        bars_since_peak=0,
        support_level=0.0,
        volume_ratio=0.0,
    )


def detect_support_collapse(
    candles: list[Candle],
    *,
    pump_lookback_bars: int = config.SC_PUMP_LOOKBACK_BARS,
    min_pump_pct: float = config.SC_MIN_PUMP_PCT,
    min_bars_since_peak: int = config.SC_MIN_BARS_SINCE_PEAK,
    volume_multiplier: float = config.SC_VOLUME_MULTIPLIER,
    volume_lookback: int = config.SC_VOLUME_LOOKBACK,
) -> SupportCollapseSignal:
    """
    在 candles 末尾检测"支撑崩塌"。

    candles 必须按时间升序，最后一根是当前 K 线。
    建议周期：1H K 线（既能反映结构又不会太慢）。

    每条规则的失败原因都会写入 reason 字段。
    """
    # 数据健全性：需要至少 pump_lookback_bars + volume_lookback 根
    required = max(pump_lookback_bars, volume_lookback + 1)
    if len(candles) < required:
        return _empty_signal("insufficient_data")

    recent = candles[-pump_lookback_bars:]
    current = candles[-1]

    # === 找 peak（按 high 最大） ===
    peak_idx = max(range(len(recent)), key=lambda i: recent[i].high)
    peak = recent[peak_idx]

    bars_since_peak = len(recent) - 1 - peak_idx
    if bars_since_peak < min_bars_since_peak:
        return _empty_signal(f"peak_too_recent:bars={bars_since_peak}")

    # === pump 幅度：从 peak 之前的 base_low 到 peak.high ===
    before_peak = recent[: peak_idx + 1]
    base_low = min(c.low for c in before_peak)
    if base_low <= 0:
        return _empty_signal("invalid_base_low")
    pump_pct = (peak.high - base_low) / base_low
    if pump_pct < min_pump_pct:
        return _empty_signal(f"pump_too_small:{pump_pct:.3f}")

    # === 派发区支撑：peak 后（不含 current）的最低 low ===
    after_peak_excl_current = recent[peak_idx + 1 : -1]
    if not after_peak_excl_current:
        # 理论上 bars_since_peak >= min_bars_since_peak >= 1 时不会发生
        return _empty_signal("no_support_history")
    support_level = min(c.low for c in after_peak_excl_current)
    if current.close >= support_level:
        return _empty_signal(
            f"support_holds:close={current.close:.4f}>={support_level:.4f}"
        )

    # === 量能放大确认（用 close 前的 volume_lookback 根作为基准）===
    historic = candles[-(volume_lookback + 1) : -1]
    if not historic:
        return _empty_signal("no_volume_history")
    sorted_vols = sorted(c.volume for c in historic)
    median_vol = sorted_vols[len(sorted_vols) // 2]
    if median_vol <= 0:
        return _empty_signal("zero_median_volume")
    volume_ratio = current.volume / median_vol
    if volume_ratio < volume_multiplier:
        return _empty_signal(f"volume_too_low:{volume_ratio:.2f}")

    # === 全部通过 ===
    # strength: 越是放量破位越强；用 vol_ratio 归一化，上限 1.0
    strength = min(1.0, volume_ratio / (volume_multiplier * 2.0))

    return SupportCollapseSignal(
        detected=True,
        strength=strength,
        reason="SUPPORT_COLLAPSE",
        pump_pct=pump_pct,
        bars_since_peak=bars_since_peak,
        support_level=support_level,
        volume_ratio=volume_ratio,
    )
