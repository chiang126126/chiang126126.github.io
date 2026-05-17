"""
short_vacuum.py 测试。

覆盖：
- 各路数据不足
- OI 下降不够
- 无插针
- 无前置拉盘
- 完整触发
- 边界：oi_start / price_low ≤ 0
"""

import pytest

from sprite_catcher.features.short_vacuum import detect_short_vacuum


def test_sv_oi_series_too_short(make_series, make_candles):
    sig = detect_short_vacuum(
        oi_series=make_series([100.0]),
        price_series=make_series([1.0, 1.2, 1.5]),
        short_candles=make_candles([(1, 1.5, 1, 1, 100)] * 30),
    )
    assert sig.detected is False
    assert sig.reason == "oi_series_too_short"


def test_sv_price_series_too_short(make_series, make_candles):
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 80.0]),
        price_series=make_series([1.0]),
        short_candles=make_candles([(1, 1.5, 1, 1, 100)] * 30),
    )
    assert sig.detected is False
    assert sig.reason == "price_series_too_short"


def test_sv_not_enough_candles(make_series, make_candles):
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 80.0]),
        price_series=make_series([1.0, 1.5]),
        short_candles=make_candles([(1, 1.5, 1, 1, 100)] * 10),  # < 30
    )
    assert sig.detected is False
    assert sig.reason == "not_enough_candles"


def test_sv_oi_drop_too_small(make_series, make_candles):
    """OI 只跌 5% → 不到 15% 阈值。"""
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 95.0]),
        price_series=make_series([1.0, 1.5]),
        short_candles=make_candles(
            [(1.0, 1.1, 1.0, 1.0, 100)] * 30  # 无插针
        ),
    )
    assert sig.detected is False
    assert "oi_drop_too_small" in sig.reason


def test_sv_no_wick(make_series, make_candles):
    """OI 下降够了，前置拉盘也够了，但没有插针。"""
    # 30 根全是实体 K 线，上影线为 0
    candles = make_candles([(1.5, 1.5, 1.5, 1.5, 100)] * 30)
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 70.0]),  # -30%
        price_series=make_series([1.0, 1.5]),  # +50%
        short_candles=candles,
    )
    assert sig.detected is False
    assert "no_significant_wick" in sig.reason


def test_sv_no_recent_pump(make_series, make_candles):
    """OI 大跌 + 有插针，但前期没拉过 → 不是真空场景。"""
    # 制造一根明显插针
    ohlcv: list[tuple[float, float, float, float, float]] = (
        [(1.0, 1.0, 1.0, 1.0, 100)] * 29
    )
    ohlcv.append((1.0, 1.10, 1.0, 1.0, 500))  # 上影 0.10 / close 1.0 = 10%
    candles = make_candles(ohlcv)
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 70.0]),
        price_series=make_series([1.0, 1.05]),  # +5%, < 20% 阈值
        short_candles=candles,
    )
    assert sig.detected is False
    assert "no_recent_pump" in sig.reason


def test_sv_full_trigger(make_series, make_candles):
    """三条全满足：插针 + OI 骤降 + 前置拉盘。"""
    ohlcv: list[tuple[float, float, float, float, float]] = (
        [(1.0, 1.0, 1.0, 1.0, 100)] * 25
    )
    # 第 26 根：插针，上影 0.15 / close 1.0 = 15% > 3%
    ohlcv.append((1.0, 1.15, 1.0, 1.0, 800))
    # 后续 4 根回落
    ohlcv.extend([(1.0, 1.0, 0.95, 0.95, 200)] * 4)
    candles = make_candles(ohlcv)
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 70.0]),  # -30%
        price_series=make_series([1.0, 1.5]),  # +50%
        short_candles=candles,
    )
    assert sig.detected is True
    assert sig.reason == "SHORT_VACUUM"
    assert sig.oi_drop_pct == pytest.approx(0.3)
    assert sig.max_wick_ratio == pytest.approx(0.15)
    assert sig.recent_pump_pct == pytest.approx(0.5)
    assert 0.0 < sig.strength <= 1.0


def test_sv_oi_start_zero(make_series, make_candles):
    """OI 起点为 0 → 不触发，防除零。"""
    sig = detect_short_vacuum(
        oi_series=make_series([0.0, 0.0]),
        price_series=make_series([1.0, 1.5]),
        short_candles=make_candles([(1.0, 1.1, 1.0, 1.0, 100)] * 30),
    )
    assert sig.detected is False
    assert sig.reason == "oi_start_non_positive"


def test_sv_price_low_zero(make_series, make_candles):
    """price 序列里有 0 → low 是 0 → 不触发，防除零。"""
    ohlcv = [(1.0, 1.15, 1.0, 1.0, 100)] * 30
    sig = detect_short_vacuum(
        oi_series=make_series([100.0, 70.0]),
        price_series=make_series([0.0, 1.5]),
        short_candles=make_candles(ohlcv),
    )
    assert sig.detected is False
    assert sig.reason == "price_low_non_positive"


def test_sv_strength_monotonic_in_oi_drop(make_series, make_candles):
    """OI 跌得越多，strength 应不下降（在其它条件相同时）。"""
    ohlcv: list[tuple[float, float, float, float, float]] = (
        [(1.0, 1.0, 1.0, 1.0, 100)] * 25
    )
    ohlcv.append((1.0, 1.15, 1.0, 1.0, 800))
    ohlcv.extend([(1.0, 1.0, 0.95, 0.95, 200)] * 4)
    candles = make_candles(ohlcv)

    sig_mild = detect_short_vacuum(
        oi_series=make_series([100.0, 80.0]),  # -20%
        price_series=make_series([1.0, 1.5]),
        short_candles=candles,
    )
    sig_severe = detect_short_vacuum(
        oi_series=make_series([100.0, 50.0]),  # -50%
        price_series=make_series([1.0, 1.5]),
        short_candles=candles,
    )
    assert sig_mild.detected is True
    assert sig_severe.detected is True
    assert sig_severe.strength >= sig_mild.strength
