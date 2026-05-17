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


class MarketRegime(str, Enum):
    """大盘态势分级。决定 L7 组合层给 Module A/B 的仓位上限。"""
    BULL = "bull"              # 牛市：多头宽松、空头收紧
    RANGE = "range"            # 震荡：均衡分配
    BEAR = "bear"              # 熊市：空头宽松、多头收紧


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


# === L7 组合层 ===


@dataclass(frozen=True)
class AllocationCaps:
    """某个 regime 下的仓位上限。"""
    module_a_max_pct: float        # Module A 占总资金最大比例
    module_b_max_pct: float        # Module B 占总资金最大比例
    total_max_pct: float           # A+B 合计上限（≤ A+B 加总，留现金缓冲）
    new_positions_allowed: bool    # 极端情况下可以禁开仓但允许平仓


@dataclass(frozen=True)
class RegimeAssessment:
    """市场态势评估结果。"""
    regime: MarketRegime
    caps: AllocationCaps
    btc_above_ema: bool            # BTC 1D close > EMA?
    total_mc_momentum_7d: float    # 总市值 7d 变化（占比，非美元）
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AdmissionDecision:
    """L7 对单个 TradeIntent 的准入判断结果。"""
    admitted: bool
    reason: str | None             # 拒绝原因（admitted=False 时非空）


# === 历史样本（训练 / 回测） ===


class SampleLabel(str, Enum):
    """历史样本的标签——决定它属于哪类妖币。"""
    FRIENDLY_LONG = "friendly_long"    # 适合 Module A 做多
    OPERATOR_SHORT = "operator_short"  # 适合 Module B 做空
    AVOID = "avoid"                    # 两边都不做（爆炸过快 / 死亡螺旋 / 即砸）


@dataclass(frozen=True)
class HistoricalSample:
    """
    单个妖币的样本快照，用于回测和阈值校准。

    所有时间字段必须是 UTC ISO8601；数据缺失用 None 而不是 0。
    """
    # 标识
    token_symbol: str
    chain: str                         # "BTC" / "ETH" / "SOL" / "BSC" / "BASE" / ...
    rally_start_date: datetime         # 本次可交易行情的起点（不是首次 CEX 上线）
    peak_date: datetime
    end_of_window_date: datetime       # 样本观察窗结束

    # 关键价格（USD）
    base_low_usd: float
    peak_high_usd: float
    end_price_usd: float | None        # None = 仍在交易，未到结束

    # 派生指标
    pump_multiplier: float             # peak / base
    sustained_pump_days: int           # 趋势可读时长
    max_drawdown_during_pump: float    # 主升期间最大回撤（占比）

    # 庄家指纹（拿不到的字段用 None）
    top10_share_at_peak: float | None
    binance_oi_share_at_peak: float | None
    vol_oi_ratio_at_peak: float | None

    # 标签
    label: SampleLabel
    operator_archetype: str | None     # "MYX_SQUEEZE" / "COAI_CONTROL" / "LAB_INSIDER" / None
    notes: str
    sources: tuple[str, ...]           # 资料链接
