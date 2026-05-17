"""
divergence.py 测试。

覆盖：
- 数据不足 / 时间戳错位
- 线性斜率
- 三条全中 → CONFIRMED
- 两条中（无 holders）→ LIKELY
- holders_change_pct=None → 永远拿不到 CONFIRMED
- NO_DIVERGENCE
"""

from datetime import timedelta

import pytest

from sprite_catcher.features.divergence import (
    _linear_slope,
    detect_distribution_divergence,
)
from sprite_catcher.models import TimeSeriesPoint


# === _linear_slope ===


def test_slope_constant_is_zero():
    assert _linear_slope([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_slope_ascending_positive():
    assert _linear_slope([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)


def test_slope_descending_negative():
    assert _linear_slope([4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_slope_single_value_zero():
    assert _linear_slope([5.0]) == 0.0


def test_slope_empty_zero():
    assert _linear_slope([]) == 0.0


# === detect_distribution_divergence ===


def test_divergence_insufficient_data(make_series):
    p = make_series([1.0, 2.0])
    o = make_series([5.0, 4.0])
    sig = detect_distribution_divergence(p, o)
    assert sig.reason == "insufficient_data"
    assert sig.detected is False


def test_divergence_ts_mismatch(base_ts):
    step = timedelta(minutes=5)
    p = [TimeSeriesPoint(ts=base_ts + i * step, value=float(i)) for i in range(5)]
    # 把第一个时间戳错开
    o = [
        TimeSeriesPoint(ts=base_ts + (i + 1) * step, value=float(5 - i))
        for i in range(5)
    ]
    sig = detect_distribution_divergence(p, o)
    assert sig.reason == "ts_mismatch"


def test_divergence_confirmed(make_series):
    """价涨 + OI 跌 + holders 持平 → CONFIRMED。"""
    p = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    o = make_series([10.0, 9.0, 8.0, 7.0, 6.0])
    sig = detect_distribution_divergence(p, o, holders_change_pct=0.02)
    assert sig.detected is True
    assert sig.reason == "DISTRIBUTION_CONFIRMED"
    assert sig.strength == 1.0
    assert sig.price_slope > 0
    assert sig.oi_slope < 0


def test_divergence_likely_when_holders_unknown(make_series):
    """价涨 + OI 跌，但 holders 不可用 → 最多 LIKELY。"""
    p = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    o = make_series([10.0, 9.0, 8.0, 7.0, 6.0])
    sig = detect_distribution_divergence(p, o, holders_change_pct=None)
    assert sig.reason == "DISTRIBUTION_LIKELY"
    assert sig.strength == 0.6


def test_divergence_likely_when_holders_growing(make_series):
    """价涨 + OI 跌 + 但 holders 暴涨（不是出货）→ 只能 LIKELY。"""
    p = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    o = make_series([10.0, 9.0, 8.0, 7.0, 6.0])
    sig = detect_distribution_divergence(p, o, holders_change_pct=0.30)
    assert sig.reason == "DISTRIBUTION_LIKELY"


def test_divergence_no_divergence_both_up(make_series):
    """价涨 + OI 涨 → 健康 → 无信号。"""
    p = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    o = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    sig = detect_distribution_divergence(p, o, holders_change_pct=0.02)
    assert sig.detected is False
    assert sig.reason == "NO_DIVERGENCE"


def test_divergence_no_divergence_price_down(make_series):
    """价跌 + OI 跌 → 不是出货背离。"""
    p = make_series([5.0, 4.0, 3.0, 2.0, 1.0])
    o = make_series([10.0, 9.0, 8.0, 7.0, 6.0])
    sig = detect_distribution_divergence(p, o, holders_change_pct=0.02)
    assert sig.detected is False
    assert sig.reason == "NO_DIVERGENCE"


def test_divergence_operator_oi_fraction_dampens(make_series):
    """
    operator_oi_fraction=0 时，operator_oi_slope 始终为 0
    → 不会满足 oi_down 条件 → 不触发。
    """
    p = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    o = make_series([10.0, 9.0, 8.0, 7.0, 6.0])
    sig = detect_distribution_divergence(
        p, o, holders_change_pct=0.02, operator_oi_fraction=0.0
    )
    assert sig.reason == "NO_DIVERGENCE"
