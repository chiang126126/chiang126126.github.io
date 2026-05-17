"""
候选池分流（Friendly / Operator / Neutral / Blacklist）。

规则优先级（从上到下，命中即返回）：
1. BLACKLIST：极端控盘 / 加速期 / 信号源不可靠
2. FRIENDLY：筹码分散 + 操纵分低
3. OPERATOR：筹码集中 + 操纵分高
4. NEUTRAL：以上都不满足，观察不交易

输出 PoolDecision 含 reasons 字段，便于复盘"为什么是这个池"。
"""

from .. import config
from ..models import ChipFeatures, OIStratification, Pool, PoolDecision


def route_to_pool(
    chip: ChipFeatures,
    oi: OIStratification,
    *,
    daily_pump_pct: float = 0.0,
) -> PoolDecision:
    """
    把标的分到一个池。

    参数：
    - daily_pump_pct：24h 价格变化，1.0 = +100%。> 5.0（+500%）触发拉黑。
      默认 0 表示调用方未提供该指标，跳过该规则。
    """
    reasons: list[str] = []

    # === BLACKLIST 规则 ===
    if chip.top10_share > config.TOP10_EXTREME_MIN:
        reasons.append(f"top10_share_extreme={chip.top10_share:.2f}")
        return PoolDecision(
            pool=Pool.BLACKLIST, score=0.0, reasons=tuple(reasons)
        )

    if oi.manipulation_level > config.MANIPULATION_LEVEL_EXTREME:
        reasons.append(f"manipulation_extreme={oi.manipulation_level:.0f}")
        return PoolDecision(
            pool=Pool.BLACKLIST, score=0.0, reasons=tuple(reasons)
        )

    if daily_pump_pct > config.DAILY_PUMP_DANGER:
        reasons.append(f"daily_pump_too_high={daily_pump_pct:.2f}")
        return PoolDecision(
            pool=Pool.BLACKLIST, score=0.0, reasons=tuple(reasons)
        )

    # Binance 占比过低 = 跨所信号不可靠（但占比 = 0 是"还没上 Binance"，区别对待）
    if 0 < oi.binance_share < config.BINANCE_SHARE_TOO_LOW:
        reasons.append(f"binance_share_too_low={oi.binance_share:.2f}")
        return PoolDecision(
            pool=Pool.BLACKLIST, score=0.0, reasons=tuple(reasons)
        )

    if oi.vol_oi_ratio > config.VOL_OI_WASH_THRESHOLD:
        reasons.append(f"vol_oi_wash={oi.vol_oi_ratio:.1f}")
        return PoolDecision(
            pool=Pool.BLACKLIST, score=0.0, reasons=tuple(reasons)
        )

    # === FRIENDLY 规则 (AND) ===
    is_friendly = (
        chip.top10_share <= config.TOP10_FRIENDLY_MAX
        and chip.cluster_factor <= config.CLUSTER_FRIENDLY_MAX
        and oi.manipulation_level < config.FRIENDLY_MANIPULATION_CEILING
    )
    if is_friendly:
        # 分数：top10 越低分越高（最高 100）
        score = max(
            0.0,
            (
                1.0
                - chip.top10_share / max(config.TOP10_FRIENDLY_MAX, 1e-9)
            )
            * 100.0,
        )
        reasons.append("chip_decentralized")
        reasons.append("low_manipulation")
        return PoolDecision(
            pool=Pool.FRIENDLY, score=score, reasons=tuple(reasons)
        )

    # === OPERATOR 规则 (AND) ===
    is_operator = (
        chip.top10_share >= config.TOP10_OPERATOR_MIN
        and oi.manipulation_level >= config.OPERATOR_MANIPULATION_FLOOR
    )
    if is_operator:
        # 分数：综合筹码集中 + 操纵分
        score = min(
            100.0,
            chip.top10_share * 100.0 + oi.manipulation_level * 0.5,
        )
        reasons.append("chip_concentrated")
        reasons.append("oi_manipulated")
        return PoolDecision(
            pool=Pool.OPERATOR, score=score, reasons=tuple(reasons)
        )

    # === NEUTRAL ===
    reasons.append("between_zones")
    return PoolDecision(pool=Pool.NEUTRAL, score=0.0, reasons=tuple(reasons))
