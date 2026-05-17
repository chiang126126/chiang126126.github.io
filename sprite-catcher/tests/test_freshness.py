"""
freshness.py 测试。
"""

from datetime import datetime, timedelta

import pytest

from sprite_catcher.features.freshness import (
    StaleDataError,
    assert_fresh,
    is_fresh,
)
from sprite_catcher.models import TimeSeriesPoint


NOW = datetime(2025, 1, 1, 12, 0, 0)


def _series(ts_offsets_seconds: list[int]) -> list[TimeSeriesPoint]:
    return [
        TimeSeriesPoint(ts=NOW + timedelta(seconds=off), value=1.0)
        for off in ts_offsets_seconds
    ]


def test_is_fresh_recent():
    series = _series([-60, -30])           # 30 秒前
    assert is_fresh(series, NOW, max_age_seconds=120) is True


def test_is_fresh_too_old():
    series = _series([-3600, -1800])       # 30 分钟前
    assert is_fresh(series, NOW, max_age_seconds=120) is False


def test_is_fresh_empty_returns_false():
    assert is_fresh([], NOW, max_age_seconds=120) is False


def test_is_fresh_future_timestamp_accepted():
    """数据源时钟稍微快一点，未来 30 秒内不应误判过期。"""
    series = _series([30])
    assert is_fresh(series, NOW, max_age_seconds=120) is True


def test_assert_fresh_raises_on_stale():
    series = _series([-3600])
    with pytest.raises(StaleDataError, match="age="):
        assert_fresh(series, NOW, max_age_seconds=120, label="oi")


def test_assert_fresh_includes_label_in_error():
    series = _series([-3600])
    with pytest.raises(StaleDataError, match="my_specific_label"):
        assert_fresh(series, NOW, max_age_seconds=120, label="my_specific_label")


def test_assert_fresh_empty_raises():
    with pytest.raises(StaleDataError, match="empty series"):
        assert_fresh([], NOW, max_age_seconds=120, label="oi")


def test_assert_fresh_passes_when_fresh():
    series = _series([-30])
    # 不抛 = 测试通过
    assert_fresh(series, NOW, max_age_seconds=120, label="oi")


def test_works_with_candles(make_candles):
    """is_fresh 对 Candle 也能工作（duck typing on .ts）。"""
    candles = make_candles([(1, 1, 1, 1, 100)] * 3)
    # make_candles 的 base_ts 是 2025/1/1 0:0:0，所以 NOW=12:00 时已经 12h 前
    assert is_fresh(candles, NOW, max_age_seconds=120) is False
    assert is_fresh(candles, NOW, max_age_seconds=86400) is True
