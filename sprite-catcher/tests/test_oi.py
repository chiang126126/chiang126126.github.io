"""
oi.py 测试。

覆盖：
- total_oi == 0 graceful
- 跨所 binance_share
- vol/OI 比
- Pearson 相关（同向 / 反向 / 不相关 / 常量序列）
- 时间戳不对齐返回 NaN
- 操纵分组合
- 单交易所 warning
- 不在 Binance 上时不算"占比过低"
"""

import math
from datetime import datetime, timedelta

import pytest

from sprite_catcher.features.oi import _pearson_of_diff, stratify_oi
from sprite_catcher.models import TimeSeriesPoint

from .conftest import FakeOIProvider


def _mk_oi(
    *,
    oi=None,
    vol_24h=1.0,
    oi_series=None,
    price_series=None,
    large_order_ratio=0.2,
):
    return FakeOIProvider(
        oi_by_exchange=oi or {"binance": 100.0, "okx": 100.0},
        vol_24h=vol_24h,
        oi_series=oi_series or [],
        price_series=price_series or [],
        large_order_ratio=large_order_ratio,
    )


# === _pearson_of_diff ===


def test_pearson_perfectly_positive(make_series):
    """两个一阶差分成比例的序列 → r = 1。"""
    # diffs_a = [1, 2, 1, 2]; diffs_b = 3 × diffs_a
    a = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    b = make_series([0.0, 3.0, 9.0, 12.0, 18.0])
    r = _pearson_of_diff(a, b)
    assert r == pytest.approx(1.0)


def test_pearson_perfectly_negative(make_series):
    """diffs_b = -3 × diffs_a → r = -1。"""
    a = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    b = make_series([20.0, 17.0, 11.0, 8.0, 2.0])
    r = _pearson_of_diff(a, b)
    assert r == pytest.approx(-1.0)


def test_pearson_linear_trend_yields_nan(make_series):
    """
    重要不变式：两个完全线性递增的序列即使"看起来同向"，
    它们的一阶差分都是常量 → 方差 0 → NaN。
    这正是我们用 diff 而不是 levels 的原因——避免趋势造成的虚假相关。
    """
    a = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    b = make_series([10.0, 12.0, 14.0, 16.0, 18.0])
    assert math.isnan(_pearson_of_diff(a, b))


def test_pearson_constant_series_returns_nan(make_series):
    """一边常量 → 方差 0 → NaN。"""
    a = make_series([0.0, 1.0, 3.0, 4.0])
    b = make_series([5.0, 5.0, 5.0, 5.0])
    r = _pearson_of_diff(a, b)
    assert math.isnan(r)


def test_pearson_length_mismatch_nan(make_series):
    a = make_series([1.0, 2.0, 3.0])
    b = make_series([1.0, 2.0])
    assert math.isnan(_pearson_of_diff(a, b))


def test_pearson_ts_mismatch_nan(base_ts):
    """时间戳错位 → NaN。"""
    step = timedelta(minutes=5)
    a = [
        TimeSeriesPoint(ts=base_ts + i * step, value=float(i)) for i in range(5)
    ]
    b = [
        TimeSeriesPoint(ts=base_ts + (i + 1) * step, value=float(i)) for i in range(5)
    ]
    assert math.isnan(_pearson_of_diff(a, b))


def test_pearson_insufficient_samples(make_series):
    a = make_series([1.0, 2.0])  # 只能算 1 个 diff
    b = make_series([1.0, 2.0])
    assert math.isnan(_pearson_of_diff(a, b))


# === stratify_oi ===


def test_stratify_total_oi_zero(make_series):
    prov = _mk_oi(oi={"binance": 0.0}, oi_series=make_series([1, 2, 3, 4]))
    result = stratify_oi("BTCUSDT", prov)
    assert result.total_oi == 0.0
    assert result.manipulation_level == 0.0
    assert "total_oi_zero" in result.warnings


def test_stratify_binance_share_calculation(make_series):
    """Binance OI 100, 其他 300 → binance_share = 0.25"""
    series = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    prov = _mk_oi(
        oi={"binance": 100.0, "bybit": 100.0, "okx": 100.0, "hyperliquid": 100.0},
        vol_24h=1000.0,
        oi_series=series,
        price_series=series,
    )
    result = stratify_oi("X", prov)
    assert result.binance_share == pytest.approx(0.25)
    assert result.vol_oi_ratio == pytest.approx(1000.0 / 400.0)


def test_stratify_single_exchange_warning(make_series):
    series = make_series([1.0, 2.0, 3.0, 4.0, 5.0])
    prov = _mk_oi(
        oi={"binance": 100.0},
        oi_series=series,
        price_series=series,
    )
    result = stratify_oi("X", prov)
    assert "single_exchange_only" in result.warnings


def test_stratify_not_on_binance_no_low_warning(make_series):
    """不在 Binance（占比 = 0）→ warning，但不触发"占比低"惩罚。"""
    # 使用差分有变化的序列，避免相关性 NaN
    price = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    oi_seq = make_series([100.0, 103.0, 109.0, 112.0, 118.0])  # 与 price 同向
    prov = _mk_oi(
        oi={"okx": 100.0, "bybit": 100.0},
        vol_24h=10.0,
        oi_series=oi_seq,
        price_series=price,
    )
    result = stratify_oi("X", prov)
    assert result.binance_share == 0.0
    assert "no_binance_oi" in result.warnings
    # vol/oi = 0.05 < 20; book_quality 0.2 > 0.05; corr = 1 → follow 主导
    # 不在 Binance 不算"占比低"；其它项都正常 → 操纵分应该是 0
    assert result.manipulation_level == 0.0


def test_stratify_operator_when_oi_anti_correlated(make_series):
    """OI 与价格反向（diff 反向）→ operator_pct = 1 → 触发 W_OPERATOR_HIGH。"""
    price = make_series([0.0, 1.0, 3.0, 4.0, 6.0])     # diffs [1, 2, 1, 2]
    oi_seq = make_series([100.0, 97.0, 91.0, 88.0, 82.0])  # diffs [-3, -6, -3, -6]
    prov = _mk_oi(
        oi={"binance": 100.0, "okx": 100.0},  # binance_share 0.5 → 无惩罚
        vol_24h=10.0,                          # vol_oi_ratio 0.05 → 无惩罚
        oi_series=oi_seq,
        price_series=price,
        large_order_ratio=0.5,                 # > 0.05 → 无惩罚
    )
    result = stratify_oi("X", prov)
    assert result.oi_price_corr == pytest.approx(-1.0)
    assert result.operator_oi == pytest.approx(result.total_oi)
    assert result.follow_oi == 0.0
    assert result.manipulation_level == pytest.approx(25.0)  # 仅 W_OPERATOR_HIGH


def test_stratify_full_manipulation_score(make_series):
    """
    构造"全中"恶劣样本，验证 4 项惩罚都能被累加到 100。
    """
    price = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    oi_seq = make_series([100.0, 97.0, 91.0, 88.0, 82.0])  # 与 price 反向
    prov = _mk_oi(
        oi={"binance": 10.0, "okx": 90.0},  # binance 10% < 30%      → +30
        vol_24h=2500.0,                      # 2500/100 = 25 > 20    → +25
        oi_series=oi_seq,
        price_series=price,
        large_order_ratio=0.01,              # < 0.05                 → +20
    )                                        # operator_pct = 1       → +25
    result = stratify_oi("X", prov)
    assert result.manipulation_level == pytest.approx(100.0)


def test_stratify_friendly_score_zero(make_series):
    """构造完全健康样本：操纵分 = 0。"""
    price = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    oi_seq = make_series([100.0, 103.0, 109.0, 112.0, 118.0])  # 同向，r=1
    prov = _mk_oi(
        oi={"binance": 600.0, "okx": 400.0},  # binance 60% > 30%
        vol_24h=2000.0,                        # vol/oi = 2 < 20
        oi_series=oi_seq,
        price_series=price,
        large_order_ratio=0.30,                # > 0.05
    )
    result = stratify_oi("X", prov)
    assert result.manipulation_level == 0.0


def test_stratify_nan_corr_does_not_penalize(make_series):
    """
    关键不变式：相关性不可用（NaN）≠ 主力主导。
    应该添加 warning 但 *不* 触发 W_OPERATOR_HIGH。
    （这是修复后的行为；修复前会错误地扣 25 分）
    """
    flat_oi = make_series([10.0, 10.0, 10.0, 10.0, 10.0])  # 常量 → NaN
    price = make_series([0.0, 1.0, 3.0, 4.0, 6.0])
    prov = _mk_oi(
        oi={"binance": 600.0, "okx": 400.0},
        vol_24h=100.0,
        oi_series=flat_oi,
        price_series=price,
    )
    result = stratify_oi("X", prov)
    assert "corr_undefined" in result.warnings
    assert result.oi_price_corr == 0.0
    assert result.manipulation_level == 0.0  # 数据不够，不扣分
