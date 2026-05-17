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


class Side(str, Enum):
    """订单方向。"""
    BUY = "buy"
    SELL = "sell"


class EntryType(str, Enum):
    """入场方式。"""
    MARKET = "market"          # 市价单
    LIMIT = "limit"            # 限价单


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
class Candle:
    """OHLCV K 线（可以是任意周期，1m / 5m / 1h / 4h ...）。"""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


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


# === L3 安全闸输入 ===


@dataclass(frozen=True)
class TokenAuditInfo:
    """合约审计静态信息。

    数据源：Binance Query Token Audit Skill / GoPlus / RugCheck。
    任一布尔字段为 True 都可能触发拒绝（视池而定）。
    """
    mintable: bool
    freezeable: bool
    pausable: bool
    has_blacklist: bool
    owner_renounced: bool
    owner_has_privileges: bool
    buy_tax: float              # 0.0 - 1.0
    sell_tax: float


@dataclass(frozen=True)
class LiquidityInfo:
    """流动性 + LP 锁定 + 池子寿命。"""
    liquidity_usd: float
    lp_locked_pct: float        # 0.0 - 1.0
    lp_lock_remaining_days: int
    pool_age_days: int


@dataclass(frozen=True)
class DevWalletInfo:
    """部署者钱包历史。"""
    deployer_address: str
    prior_deploys: int                # 历史部署过多少 token
    has_rug_history: bool             # 是否有 rug 记录
    best_prior_market_cap_usd: float  # 历史上最高 MC


@dataclass(frozen=True)
class TradeSimulationResult:
    """模拟买入+卖出的结果，用来抓动态蜜罐。"""
    can_buy: bool
    can_sell: bool
    effective_buy_tax: float    # 实际成交成本，包含税
    effective_sell_tax: float
    error: str | None = None


@dataclass(frozen=True)
class SafetyReport:
    """L3 安全闸输出。"""
    passed: bool
    rejected_reasons: tuple[str, ...]   # passed=False 时非空
    warnings: tuple[str, ...]            # 通过但策略层应注意


# === Module B 入场信号 ===


@dataclass(frozen=True)
class SupportCollapseSignal:
    """支撑崩塌信号：庄拉完后 K 线结构破位。"""
    detected: bool
    strength: float                # 0-1
    reason: str
    pump_pct: float                # base → peak 涨幅
    bars_since_peak: int           # peak 到 current 的距离
    support_level: float           # 用来判断破位的支撑参考价
    volume_ratio: float            # 当前量 / 历史中位量


@dataclass(frozen=True)
class ShortVacuumSignal:
    """空头真空信号：插针清算空头 + OI 骤降。"""
    detected: bool
    strength: float
    reason: str
    oi_drop_pct: float             # OI 下降百分比
    max_wick_ratio: float          # 窗口内最大上影线比例
    recent_pump_pct: float         # 前置拉盘幅度


# === Module A 入场信号 ===


@dataclass(frozen=True)
class TrendFollowSignal:
    """趋势跟随信号：多头多周期共振 + 持有人增速。"""
    detected: bool
    strength: float
    reason: str
    ema20: float
    ema50: float
    ema20_up_bars: int             # EMA20 连续向上的根数
    daily_breakout: bool           # 是否突破日线 N 日高点
    holders_growth_pct: float      # 7d 持有人增速（可选输入）


# === 仓位计算 ===


@dataclass(frozen=True)
class PositionSize:
    """单笔仓位计算结果。

    qty_quote_usd 是仓位的 USD 名义价值（notional），不是占用的保证金。
    leverage 是 notional / equity 的比值；现货应 ≤ 1，永续可 > 1。
    """
    qty_quote_usd: float
    leverage: float
    risk_usd: float                # 单笔预计最大亏损
    capped_by: str | None          # 哪条上限把仓位卡住了（None = 未被卡）
    reason: str                    # 计算细节的可读描述


@dataclass(frozen=True)
class TradeIntent:
    """L5 策略层输出：一笔可执行的交易意图。

    L6 执行层消费这个 intent，转成实际的交易所订单（OTOCO 或两段挂单）。
    intent 本身没有"挂单时间""有效期"这些；L6 决定怎么挂。
    """
    strategy_id: str               # "trend_follow" / "support_collapse" / ...
    symbol: str
    side: Side
    pool: Pool                     # 来源池，用于追溯

    # 价格
    entry_type: EntryType
    entry_price: float             # MARKET 时是参考价；LIMIT 时是挂单价
    stop_loss_price: float
    take_profit_price: float | None  # None = 用 trailing stop（trend follow 风格）

    # 仓位
    sizing: PositionSize

    # 元数据
    signal_strength: float         # 0-1
    max_holding_seconds: int
    reasons: tuple[str, ...]
