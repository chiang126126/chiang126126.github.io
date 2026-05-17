"""
精灵捕手 — 数据模型。

所有模型都是 frozen dataclass：
- 强制不可变 → 不会被下游意外篡改
- 没有方法、没有副作用 → 容易序列化、容易测试
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Pool(str, Enum):
    """候选池分类。一个标的在同一时刻只能属于一个池。"""
    FRIENDLY = "friendly"      # 多头候选（Module A）
    OPERATOR = "operator"      # 空头候选（Module B）
    NEUTRAL = "neutral"        # 既不做多也不做空，观察
    BLACKLIST = "blacklist"    # 极端控盘 / 加速期 / 信号不可靠，完全跳过


@dataclass(frozen=True)
class HolderSnapshot:
    """单个持有人的快照。balance 用 Decimal 防浮点精度丢失。"""
    address: str
    balance: Decimal


@dataclass(frozen=True)
class TransferEdge:
    """链上转账的一条边：from_addr -> to_addr。"""
    from_addr: str
    to_addr: str


@dataclass(frozen=True)
class TimeSeriesPoint:
    """时间序列上的一个点。ts 必须是 UTC。"""
    ts: datetime
    value: float


@dataclass(frozen=True)
class ChipFeatures:
    """筹码集中度特征。"""
    top10_share: float          # top10 持仓 / 流通供应量
    top50_share: float          # top50 持仓 / 流通供应量
    independent_clusters: int   # funder 去重后的独立簇数
    cluster_factor: float       # 样本数 / clusters，越大越同源
    excluded_count: int         # 被识别为 LP/burn/CEX 而排除的地址数


@dataclass(frozen=True)
class OIStratification:
    """OI 分层结果。"""
    total_oi: float
    binance_share: float        # Binance OI / 总 OI
    vol_oi_ratio: float         # 24h 成交量 / 总 OI
    book_quality: float         # 订单簿大单占比
    oi_price_corr: float        # OI 和价格的一阶差分相关性（-1 ~ +1）
    operator_oi: float          # 估算的主力 OI
    follow_oi: float            # 估算的多头跟随 OI
    manipulation_level: float   # 综合操纵分 0-100
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DivergenceSignal:
    """三层背离检测结果。"""
    price_slope: float
    oi_slope: float
    holders_change_pct: float
    detected: bool
    strength: float             # 0-1，1 = 三条全中
    reason: str                 # "DISTRIBUTION_CONFIRMED" / "DISTRIBUTION_LIKELY"
                                # / "NO_DIVERGENCE" / "insufficient_data" / "ts_mismatch"


@dataclass(frozen=True)
class PoolDecision:
    """分流决策结果。"""
    pool: Pool
    score: float                # 该池内的相对优先级（越大越优先）
    reasons: tuple[str, ...]    # 命中的规则列表，便于复盘
