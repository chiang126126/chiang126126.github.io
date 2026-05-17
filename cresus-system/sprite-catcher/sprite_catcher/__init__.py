"""
精灵捕手（Sprite Catcher）— 妖币交易体系的特征工程层。

公开 API：
- compute_chip_features      筹码集中度 + funder 去重
- stratify_oi                OI 分层（主力 / Follow）+ 综合操纵分
- detect_distribution_divergence   价 / OI / 持有人 三层背离
- route_to_pool              候选池分流

所有计算函数都是纯函数：
- 不做 I/O
- 不读全局状态
- 同样输入 → 同样输出

I/O 通过 interfaces.py 中的 Protocol 注入。
"""

from .features.chip import compute_chip_features, funder_dedupe
from .features.divergence import detect_distribution_divergence
from .features.oi import stratify_oi
from .features.pool_router import route_to_pool
from .features.safety_gate import evaluate_long_safety, evaluate_short_safety
from .features.short_vacuum import detect_short_vacuum
from .features.support_collapse import detect_support_collapse
from .models import (
    Candle,
    ChipFeatures,
    DevWalletInfo,
    DivergenceSignal,
    HolderSnapshot,
    LiquidityInfo,
    OIStratification,
    Pool,
    PoolDecision,
    SafetyReport,
    ShortVacuumSignal,
    SupportCollapseSignal,
    TimeSeriesPoint,
    TokenAuditInfo,
    TradeSimulationResult,
    TransferEdge,
)

__all__ = [
    # 计算函数 (L2)
    "compute_chip_features",
    "funder_dedupe",
    "stratify_oi",
    "detect_distribution_divergence",
    "route_to_pool",
    # 计算函数 (L3)
    "evaluate_long_safety",
    "evaluate_short_safety",
    # 入场信号 (Module B)
    "detect_support_collapse",
    "detect_short_vacuum",
    # 数据模型 (L2)
    "Candle",
    "ChipFeatures",
    "DivergenceSignal",
    "HolderSnapshot",
    "OIStratification",
    "Pool",
    "PoolDecision",
    "TimeSeriesPoint",
    "TransferEdge",
    # 数据模型 (L3)
    "TokenAuditInfo",
    "LiquidityInfo",
    "DevWalletInfo",
    "TradeSimulationResult",
    "SafetyReport",
    # 数据模型 (Module B 信号)
    "SupportCollapseSignal",
    "ShortVacuumSignal",
]
