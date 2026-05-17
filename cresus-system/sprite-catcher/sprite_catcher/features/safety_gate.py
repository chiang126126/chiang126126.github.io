"""
L3 安全闸：策略下单前的最后一道一票否决关。

设计哲学：
- 多头池和空头池的安全要求**不一样**，所以两个独立入口
  - evaluate_long_safety：保护 long 持仓不归零（需要 LP 锁、dev 干净、池子够老）
  - evaluate_short_safety：确保 short 能开能平、不被轧爆（需要流动性深、税不卡）
- 共通检查（合约一票否决项、动态蜜罐）抽到私有 helper
- 所有规则都是 **不通过 → 进 rejected_reasons**，通过 → 不加任何项
- warnings 字段用来"通过但要注意"，策略层可降权使用

返回 SafetyReport（passed + rejected_reasons + warnings），不抛错。
"""

from .. import config
from ..models import (
    ChipFeatures,
    DevWalletInfo,
    LiquidityInfo,
    SafetyReport,
    TokenAuditInfo,
    TradeSimulationResult,
)


def _check_audit_one_veto(audit: TokenAuditInfo) -> list[str]:
    """合约审计的一票否决项（多头空头通用）。"""
    rejected: list[str] = []
    if audit.mintable:
        rejected.append("audit_mintable")
    if audit.freezeable:
        rejected.append("audit_freezeable")
    if audit.pausable:
        rejected.append("audit_pausable")
    if audit.has_blacklist:
        rejected.append("audit_has_blacklist")
    # owner_renounced=False 单独不够拒 — 必须同时 has_privileges
    # （有些合约 owner 没放弃但权限已经无害）
    if (not audit.owner_renounced) and audit.owner_has_privileges:
        rejected.append("audit_owner_dangerous")
    return rejected


def _check_honeypot(sim: TradeSimulationResult | None) -> list[str]:
    """模拟试卖：能买不能卖 = 蜜罐。"""
    if sim is None:
        return []  # 不可用时不阻塞，留给调用方决定是否要求
    rejected: list[str] = []
    if not sim.can_buy:
        rejected.append(f"sim_cannot_buy:{sim.error or 'unknown'}")
    if not sim.can_sell:
        rejected.append(f"sim_cannot_sell:{sim.error or 'unknown'}")
    return rejected


def evaluate_long_safety(
    audit: TokenAuditInfo,
    liquidity: LiquidityInfo,
    dev: DevWalletInfo,
    chip: ChipFeatures,
    *,
    sim_result: TradeSimulationResult | None = None,
) -> SafetyReport:
    """
    多头池（Module A）的安全审计。

    必过项（任一失败 → 拒绝）：
    - 合约一票否决：mintable/freezeable/pausable/blacklist/owner_dangerous
    - 模拟试卖通过（如果提供）
    - 买入税 ≤ 5%，卖出税 ≤ 5%
    - LP 锁定 ≥ 90%，剩余锁定 ≥ 180 天
    - 流动性 ≥ $200k
    - 池子年龄 ≥ 14 天
    - top10 ≤ 30%
    - dev 钱包无 rug 记录

    Warnings（通过但策略层应注意）：
    - dev 是新钱包（prior_deploys == 0）
    - 流动性低于 2× 阈值
    """
    rejected: list[str] = []
    warnings: list[str] = []

    rejected.extend(_check_audit_one_veto(audit))
    rejected.extend(_check_honeypot(sim_result))

    if audit.buy_tax > config.A_BUY_TAX_MAX:
        rejected.append(f"buy_tax={audit.buy_tax:.3f}>{config.A_BUY_TAX_MAX}")
    if audit.sell_tax > config.A_SELL_TAX_MAX:
        rejected.append(f"sell_tax={audit.sell_tax:.3f}>{config.A_SELL_TAX_MAX}")

    if liquidity.lp_locked_pct < config.A_LP_LOCKED_PCT_MIN:
        rejected.append(
            f"lp_locked_pct={liquidity.lp_locked_pct:.2f}<"
            f"{config.A_LP_LOCKED_PCT_MIN}"
        )
    if liquidity.lp_lock_remaining_days < config.A_LP_LOCK_DAYS_MIN:
        rejected.append(
            f"lp_lock_remaining_days={liquidity.lp_lock_remaining_days}<"
            f"{config.A_LP_LOCK_DAYS_MIN}"
        )

    if liquidity.liquidity_usd < config.A_LIQUIDITY_USD_MIN:
        rejected.append(
            f"liquidity_usd={liquidity.liquidity_usd:.0f}<"
            f"{config.A_LIQUIDITY_USD_MIN:.0f}"
        )

    if liquidity.pool_age_days < config.A_POOL_AGE_DAYS_MIN:
        rejected.append(
            f"pool_age_days={liquidity.pool_age_days}<{config.A_POOL_AGE_DAYS_MIN}"
        )

    if chip.top10_share > config.A_TOP10_MAX:
        rejected.append(
            f"top10_share={chip.top10_share:.2f}>{config.A_TOP10_MAX}"
        )

    if dev.has_rug_history:
        rejected.append(f"dev_rug_history:{dev.deployer_address}")

    # === Warnings ===
    if dev.prior_deploys == 0:
        warnings.append("dev_first_time")
    if liquidity.liquidity_usd < config.A_LIQUIDITY_USD_MIN * 2:
        warnings.append("liquidity_thin")

    return SafetyReport(
        passed=len(rejected) == 0,
        rejected_reasons=tuple(rejected),
        warnings=tuple(warnings),
    )


def evaluate_short_safety(
    audit: TokenAuditInfo,
    liquidity: LiquidityInfo,
    *,
    sim_result: TradeSimulationResult | None = None,
    has_futures_market: bool = True,
) -> SafetyReport:
    """
    空头池（Module B）的安全审计。

    不检查：
    - LP 锁定（LP 抽走对空头其实有利）
    - dev 钱包（你赌它跌，不在意 dev 是不是惯犯）
    - top10 集中度（已经被分流到 OPERATOR 池就是因为筹码集中）

    必过项：
    - has_futures_market（否则无法做空）
    - 合约一票否决（蜜罐能搞死空头平仓）
    - 流动性 ≥ $500k（深度不够会被轧）
    - 买卖税 ≤ 10%
    - 池子 ≥ 7 天（要先经历过一次主升才有"顶"可空）
    - 模拟试卖通过（如果提供）
    """
    rejected: list[str] = []
    warnings: list[str] = []

    if not has_futures_market:
        rejected.append("no_futures_market")

    rejected.extend(_check_audit_one_veto(audit))
    rejected.extend(_check_honeypot(sim_result))

    if audit.buy_tax > config.B_BUY_TAX_MAX:
        rejected.append(f"buy_tax={audit.buy_tax:.3f}>{config.B_BUY_TAX_MAX}")
    if audit.sell_tax > config.B_SELL_TAX_MAX:
        rejected.append(f"sell_tax={audit.sell_tax:.3f}>{config.B_SELL_TAX_MAX}")

    if liquidity.liquidity_usd < config.B_LIQUIDITY_USD_MIN:
        rejected.append(
            f"liquidity_usd={liquidity.liquidity_usd:.0f}<"
            f"{config.B_LIQUIDITY_USD_MIN:.0f}"
        )

    if liquidity.pool_age_days < config.B_POOL_AGE_DAYS_MIN:
        rejected.append(
            f"pool_age_days={liquidity.pool_age_days}<{config.B_POOL_AGE_DAYS_MIN}"
        )

    return SafetyReport(
        passed=len(rejected) == 0,
        rejected_reasons=tuple(rejected),
        warnings=tuple(warnings),
    )
