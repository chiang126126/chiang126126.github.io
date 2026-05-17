"""
pool_router.py 测试。

覆盖：
- 友好池 / 操纵池 / 中性 / 拉黑 的边界
- 多条拉黑规则的优先级
- Binance 占比 = 0 vs < 10% 的差异
"""

from sprite_catcher.features.pool_router import route_to_pool
from sprite_catcher.models import ChipFeatures, OIStratification, Pool


def _chip(
    *,
    top10=0.15,
    top50=0.30,
    clusters=100,
    cluster_factor=2.0,
    excluded=0,
) -> ChipFeatures:
    return ChipFeatures(
        top10_share=top10,
        top50_share=top50,
        independent_clusters=clusters,
        cluster_factor=cluster_factor,
        excluded_count=excluded,
    )


def _oi(
    *,
    total_oi=1_000_000.0,
    binance_share=0.5,
    vol_oi_ratio=2.0,
    book_quality=0.3,
    oi_price_corr=0.8,
    operator_oi=200_000.0,
    follow_oi=800_000.0,
    manipulation_level=0.0,
) -> OIStratification:
    return OIStratification(
        total_oi=total_oi,
        binance_share=binance_share,
        vol_oi_ratio=vol_oi_ratio,
        book_quality=book_quality,
        oi_price_corr=oi_price_corr,
        operator_oi=operator_oi,
        follow_oi=follow_oi,
        manipulation_level=manipulation_level,
    )


def test_pool_friendly_baseline():
    """筹码分散 + 操纵分低 → FRIENDLY。"""
    chip = _chip(top10=0.1, cluster_factor=1.5)
    oi = _oi(manipulation_level=10.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.FRIENDLY
    assert d.score > 0


def test_pool_operator_baseline():
    """筹码集中 + 操纵分高 → OPERATOR。"""
    chip = _chip(top10=0.65, cluster_factor=8.0)
    oi = _oi(manipulation_level=70.0, binance_share=0.4, oi_price_corr=-0.5)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.OPERATOR
    assert "chip_concentrated" in d.reasons


def test_pool_neutral_when_between():
    """中间地带：不极端、不友好、不操纵 → NEUTRAL。"""
    chip = _chip(top10=0.30, cluster_factor=4.0)  # 不友好（>20%）
    oi = _oi(manipulation_level=45.0)  # 不到 50
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.NEUTRAL


def test_pool_blacklist_extreme_top10():
    """top10 占比 > 85% → BLACKLIST（LAB / RAVE 级别）。"""
    chip = _chip(top10=0.92, cluster_factor=10.0)
    oi = _oi()
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.BLACKLIST
    assert any("top10_share_extreme" in r for r in d.reasons)


def test_pool_blacklist_extreme_manipulation():
    """操纵分 > 85 → BLACKLIST。"""
    chip = _chip(top10=0.6)
    oi = _oi(manipulation_level=90.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.BLACKLIST


def test_pool_blacklist_daily_pump():
    """24h 涨幅 > 500% → BLACKLIST（不接刀）。"""
    chip = _chip(top10=0.6)
    oi = _oi(manipulation_level=70.0)
    d = route_to_pool(chip, oi, daily_pump_pct=8.0)
    assert d.pool is Pool.BLACKLIST


def test_pool_blacklist_binance_share_too_low():
    """Binance 占比 5% (在 Binance 但占比极低) → 信号不可靠 → BLACKLIST。"""
    chip = _chip(top10=0.6)
    oi = _oi(binance_share=0.05, manipulation_level=70.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.BLACKLIST


def test_pool_blacklist_vol_oi_wash():
    """vol/OI > 20 (刷量过高) → BLACKLIST。"""
    chip = _chip(top10=0.6)
    oi = _oi(vol_oi_ratio=25.0, manipulation_level=70.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.BLACKLIST
    assert any("vol_oi_wash" in r for r in d.reasons)


def test_pool_not_on_binance_is_not_blacklist():
    """
    binance_share = 0 (还没上 Binance) 不应该被当成"占比过低"拉黑。
    其他指标决定它去哪个池。
    """
    chip = _chip(top10=0.10, cluster_factor=2.0)
    oi = _oi(binance_share=0.0, manipulation_level=20.0)
    d = route_to_pool(chip, oi)
    # 不是 BLACKLIST，且因为筹码分散 → FRIENDLY
    assert d.pool is Pool.FRIENDLY


def test_pool_priority_blacklist_over_friendly():
    """即使筹码很友好，遇到极端操纵分也必须拉黑（不要做多）。"""
    chip = _chip(top10=0.1, cluster_factor=1.5)
    oi = _oi(manipulation_level=95.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.BLACKLIST


def test_pool_friendly_requires_low_manipulation():
    """
    筹码很分散但操纵分中等（50）→ 不进 FRIENDLY，也不进 OPERATOR（筹码不够集中）
    → NEUTRAL。
    """
    chip = _chip(top10=0.1, cluster_factor=1.5)
    oi = _oi(manipulation_level=50.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.NEUTRAL


def test_pool_operator_requires_both_chip_and_oi():
    """
    筹码集中但操纵分低 → 不进 OPERATOR（OI 没起来，可能还在吸筹早期）→ NEUTRAL。
    """
    chip = _chip(top10=0.65, cluster_factor=8.0)
    oi = _oi(manipulation_level=30.0)
    d = route_to_pool(chip, oi)
    assert d.pool is Pool.NEUTRAL
