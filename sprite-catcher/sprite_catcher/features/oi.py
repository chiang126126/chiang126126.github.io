"""
OI 分层（主力 OI vs 多头跟随 OI）+ 综合操纵分。

核心思路（来自市场观察）：
- Binance OI 占比越低 → 操纵越可能（操纵者爱用监管弱的所）
- vol/OI 比越高 → 刷量越严重
- 订单簿大单稀少 → 主力主导（主力不愿暴露大额挂单）
- OI 与价格高度同向 → 多头跟随主导；反之 → 主力主导

⚠️ 这是启发式估算，不是精确建模。所有阈值都应在历史样本上回测后调整。
"""

import math

from .. import config
from ..interfaces import OIProvider
from ..models import OIStratification, TimeSeriesPoint


def _pearson_of_diff(
    series_a: list[TimeSeriesPoint],
    series_b: list[TimeSeriesPoint],
) -> float:
    """
    Pearson 相关性，使用一阶差分（returns）而非原始值（levels）。

    为什么用差分：两个都在上升趋势的序列即使无关也会有很高的 levels 相关。
    用差分能去掉趋势，反映"瞬时方向是否同向"。

    返回 NaN 当：
    - 长度不一致
    - 时间戳不对齐
    - 样本不足
    - 任一方差为 0（常量序列）
    """
    if len(series_a) != len(series_b):
        return float("nan")
    if len(series_a) < 3:
        return float("nan")

    for a, b in zip(series_a, series_b):
        if a.ts != b.ts:
            return float("nan")

    diffs_a = [
        series_a[i].value - series_a[i - 1].value for i in range(1, len(series_a))
    ]
    diffs_b = [
        series_b[i].value - series_b[i - 1].value for i in range(1, len(series_b))
    ]

    n = len(diffs_a)
    if n < 2:
        return float("nan")

    mean_a = sum(diffs_a) / n
    mean_b = sum(diffs_b) / n

    var_a = sum((x - mean_a) ** 2 for x in diffs_a)
    var_b = sum((x - mean_b) ** 2 for x in diffs_b)

    if var_a == 0.0 or var_b == 0.0:
        return float("nan")

    cov = sum(
        (diffs_a[i] - mean_a) * (diffs_b[i] - mean_b) for i in range(n)
    )
    return cov / math.sqrt(var_a * var_b)


def stratify_oi(
    symbol: str,
    oi_provider: OIProvider,
    *,
    lookback_hours: int = 24,
) -> OIStratification:
    """
    把 OI 拆成主力 / Follow 两部分，并给出综合操纵分（0-100）。

    边界处理：
    - total_oi <= 0 → 返回全 0 + warning，不抛错
    - 相关性 NaN → 视作 0，添加 warning
    - 单交易所 → warning（无法做跨所判断）
    - Binance 占比 = 0（即不在 Binance）→ warning，但不算作"占比低"
    """
    warnings: list[str] = []

    oi_by_exchange = oi_provider.get_oi_by_exchange(symbol)
    total_oi = sum(oi_by_exchange.values())

    if total_oi <= 0:
        return OIStratification(
            total_oi=0.0,
            binance_share=0.0,
            vol_oi_ratio=0.0,
            book_quality=0.0,
            oi_price_corr=0.0,
            operator_oi=0.0,
            follow_oi=0.0,
            manipulation_level=0.0,
            warnings=("total_oi_zero",),
        )

    # 数据健全性
    if len(oi_by_exchange) < 2:
        warnings.append("single_exchange_only")

    binance_oi = oi_by_exchange.get("binance", 0.0)
    binance_share = binance_oi / total_oi
    binance_share_unavailable = binance_oi == 0.0
    if binance_share_unavailable:
        warnings.append("no_binance_oi")

    vol_24h = oi_provider.get_vol_24h(symbol)
    # total_oi > 0 在前面已确认，但保留显式 guard 防未来重构破坏
    vol_oi_ratio = vol_24h / total_oi if total_oi > 0 else 0.0

    book_quality = oi_provider.get_orderbook_large_order_ratio(symbol)

    oi_series = oi_provider.get_oi_series(symbol, hours=lookback_hours)
    price_series = oi_provider.get_price_series(symbol, hours=lookback_hours)

    oi_price_corr_raw = _pearson_of_diff(oi_series, price_series)
    corr_available = not math.isnan(oi_price_corr_raw)
    if not corr_available:
        warnings.append("corr_undefined")
        oi_price_corr = 0.0
    else:
        oi_price_corr = oi_price_corr_raw

    # follow_pct = 正相关部分（与价格同向变动的 OI）
    # operator_pct = 剩余部分（包括负相关，因为负相关 = 反向操作 = 操纵）
    follow_pct = max(0.0, min(1.0, oi_price_corr))
    operator_pct = 1.0 - follow_pct

    # 综合操纵分。注意：每条规则都有"数据可用"的前提，
    # 不可用时不计入分数（"未知"≠"操纵"）。
    manipulation_level = 0.0
    if (
        not binance_share_unavailable
        and binance_share < config.BINANCE_SHARE_MANIPULATION
    ):
        manipulation_level += config.W_BINANCE_LOW
    if vol_oi_ratio > config.VOL_OI_WASH_THRESHOLD:
        manipulation_level += config.W_VOL_OI_WASH
    if book_quality < config.BOOK_QUALITY_LOW:
        manipulation_level += config.W_BOOK_THIN
    if corr_available and operator_pct > config.OPERATOR_PCT_HIGH:
        manipulation_level += config.W_OPERATOR_HIGH

    return OIStratification(
        total_oi=total_oi,
        binance_share=binance_share,
        vol_oi_ratio=vol_oi_ratio,
        book_quality=book_quality,
        oi_price_corr=oi_price_corr,
        operator_oi=operator_pct * total_oi,
        follow_oi=follow_pct * total_oi,
        manipulation_level=manipulation_level,
        warnings=tuple(warnings),
    )
