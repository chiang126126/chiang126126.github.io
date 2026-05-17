"""
精灵捕手 — 阈值集中管理。

所有"魔法数字"都汇集在这里，方便审计、调参、写回归测试。
任何 features/ 下的代码不应该出现裸数字常量。
"""

# === 筹码集中度 ===
TOP10_FRIENDLY_MAX = 0.20      # top10 占比 ≤ 20% 视为友好池候选
TOP10_OPERATOR_MIN = 0.50      # top10 占比 ≥ 50% 视为操纵池候选
TOP10_EXTREME_MIN = 0.85       # top10 占比 ≥ 85% 视为极端控盘 → 拉黑
CLUSTER_FRIENDLY_MAX = 3.0     # cluster_factor ≤ 3 视为散户主导
CLUSTER_OPERATOR_MIN = 5.0     # cluster_factor ≥ 5 视为高度同源

# === OI 分层 ===
BINANCE_SHARE_MANIPULATION = 0.30  # Binance OI 占比 < 30% 视为操纵程度高
BINANCE_SHARE_TOO_LOW = 0.10       # Binance 占比 < 10% 信号不可靠 → 拉黑
VOL_OI_WASH_THRESHOLD = 20.0       # vol/OI > 20 视为刷量
BOOK_QUALITY_LOW = 0.05            # 订单簿大单占比 < 5% = 主力主导
OPERATOR_PCT_HIGH = 0.60           # operator_pct > 0.6 计入操纵分

# === 操纵分组合权重 (总分 100) ===
W_BINANCE_LOW = 30.0
W_VOL_OI_WASH = 25.0
W_BOOK_THIN = 20.0
W_OPERATOR_HIGH = 25.0

# === 三层背离 ===
DIVERGENCE_PRICE_SLOPE_MIN = 0.0      # 价格斜率 > 0 视为上行
DIVERGENCE_OI_SLOPE_MAX = 0.0         # OI 斜率 < 0 视为下行
DIVERGENCE_HOLDERS_FLAT_MAX = 0.05    # 持有人变化 ≤ 5% 视为持平

# === 分流决策 ===
FRIENDLY_MANIPULATION_CEILING = 40.0  # 友好池要求 manipulation_level < 40
OPERATOR_MANIPULATION_FLOOR = 50.0    # 操纵池要求 manipulation_level ≥ 50
MANIPULATION_LEVEL_EXTREME = 85.0     # > 85 拉黑（不做空，不做多）
DAILY_PUMP_DANGER = 5.0               # 24h 涨幅 > 500% 视为加速期 → 拉黑

# === 安全闸：多头池 (Module A) 要求 ===
A_LP_LOCKED_PCT_MIN = 0.90            # LP 锁定 ≥ 90%
A_LP_LOCK_DAYS_MIN = 180              # 锁定剩余 ≥ 6 个月
A_LIQUIDITY_USD_MIN = 200_000.0       # 流动性下限
A_POOL_AGE_DAYS_MIN = 14              # 池子至少 14 天，过滤极早期
A_BUY_TAX_MAX = 0.05                  # 买入税 ≤ 5%
A_SELL_TAX_MAX = 0.05                 # 卖出税 ≤ 5%
A_TOP10_MAX = 0.30                    # top10 ≤ 30%（比分流的 20% 宽一点）

# === 安全闸：空头池 (Module B) 要求 ===
# 设计哲学：空头不太关心 dev 是否惯犯（你赌它跌）也不在乎 LP 锁不锁（LP 抽走对空头有利）；
# 但要确保你能"卖空"和"买回平仓"，且流动性深到不会被轧爆。
B_LIQUIDITY_USD_MIN = 500_000.0       # 空头需要更深流动性
B_BUY_TAX_MAX = 0.10                  # 买回平仓税 ≤ 10%
B_SELL_TAX_MAX = 0.10
B_POOL_AGE_DAYS_MIN = 7               # 池子 ≥ 7 天（要先有过一次主升）
