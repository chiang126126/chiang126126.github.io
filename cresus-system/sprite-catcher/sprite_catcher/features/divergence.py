"""
三层背离检测：价格 / OI / 持有人。

逻辑：
- 价格在涨（slope > 0）
- OI 在跌（slope < 0）→ 意味着杠杆头寸在减少
- 持有人增速很弱（变化 ≤ 阈值）→ 没有真实新人接盘

三条全中 → DISTRIBUTION_CONFIRMED（聪明钱在出货，散户没接盘）
两条中（价 + OI）→ DISTRIBUTION_LIKELY
否则 → NO_DIVERGENCE

这个函数是**纯计算**：所有时序数据从外部传入，不做 I/O。
holders_change_pct 也是外部传入，因为它需要历史快照支持（不在本模块职责内）。
"""

from .. import config
from ..models import DivergenceSignal, TimeSeriesPoint


def _linear_slope(values: list[float]) -> float:
    """
    对一个数组做最小二乘线性回归，返回斜率。x = 0, 1, 2, ..., n-1。

    返回 0 当：
    - 样本数 < 2
    - x 方差为 0（不可能发生，除非 n=1，已被前面拦下）
    """
    n = len(values)
    if n < 2:
        return 0.0

    sum_x = n * (n - 1) / 2  # 0 + 1 + ... + (n-1)
    sum_y = sum(values)
    sum_xy = sum(i * values[i] for i in range(n))
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6  # 0² + 1² + ... + (n-1)²

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denom


def detect_distribution_divergence(
    price_series: list[TimeSeriesPoint],
    oi_series: list[TimeSeriesPoint],
    *,
    holders_change_pct: float | None = None,
    operator_oi_fraction: float = 1.0,
) -> DivergenceSignal:
    """
    检测出货背离。

    参数：
    - price_series / oi_series：时间戳必须严格对齐
    - holders_change_pct：窗口内持有人变化百分比；None 表示不可用，
      此时最高只能输出 DISTRIBUTION_LIKELY，不能 CONFIRMED
    - operator_oi_fraction：估算的"主力 OI 占总 OI 的比例"。
      默认 1.0 意味着用总 OI 斜率作为主力 OI 斜率的代理。
      如果上游 stratify_oi 已经分层，可以传 0.6 等更精细的值。

    返回 DivergenceSignal，含原始斜率以便复盘。
    """
    if len(price_series) < 3 or len(oi_series) < 3:
        return DivergenceSignal(
            price_slope=0.0,
            oi_slope=0.0,
            holders_change_pct=holders_change_pct or 0.0,
            detected=False,
            strength=0.0,
            reason="insufficient_data",
        )

    if [p.ts for p in price_series] != [o.ts for o in oi_series]:
        return DivergenceSignal(
            price_slope=0.0,
            oi_slope=0.0,
            holders_change_pct=holders_change_pct or 0.0,
            detected=False,
            strength=0.0,
            reason="ts_mismatch",
        )

    price_slope = _linear_slope([p.value for p in price_series])
    oi_slope = _linear_slope([o.value for o in oi_series])
    operator_oi_slope = oi_slope * operator_oi_fraction

    cond_price_up = price_slope > config.DIVERGENCE_PRICE_SLOPE_MIN
    cond_oi_down = operator_oi_slope < config.DIVERGENCE_OI_SLOPE_MAX

    # holders 条件只有在数据可用时才参与判定
    if holders_change_pct is None:
        cond_holders_flat = False
        holders_value_for_record = 0.0
    else:
        cond_holders_flat = (
            abs(holders_change_pct) <= config.DIVERGENCE_HOLDERS_FLAT_MAX
        )
        holders_value_for_record = holders_change_pct

    if cond_price_up and cond_oi_down and cond_holders_flat:
        return DivergenceSignal(
            price_slope=price_slope,
            oi_slope=oi_slope,
            holders_change_pct=holders_value_for_record,
            detected=True,
            strength=1.0,
            reason="DISTRIBUTION_CONFIRMED",
        )

    if cond_price_up and cond_oi_down:
        return DivergenceSignal(
            price_slope=price_slope,
            oi_slope=oi_slope,
            holders_change_pct=holders_value_for_record,
            detected=True,
            strength=0.6,
            reason="DISTRIBUTION_LIKELY",
        )

    return DivergenceSignal(
        price_slope=price_slope,
        oi_slope=oi_slope,
        holders_change_pct=holders_value_for_record,
        detected=False,
        strength=0.0,
        reason="NO_DIVERGENCE",
    )
