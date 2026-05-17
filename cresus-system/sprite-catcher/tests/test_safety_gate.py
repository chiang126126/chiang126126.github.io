"""
safety_gate.py 测试。

覆盖：
- 多头通过 / 空头通过 (baseline)
- 合约一票否决项 (mintable/freezeable/pausable/blacklist/owner)
- owner_renounced=False 但 has_privileges=False 不应触发
- 模拟试卖蜜罐
- 税率上限差异（多头 5% vs 空头 10%）
- LP 锁定 / 流动性 / 池龄
- dev rug history（仅多头）
- top10 上限（仅多头）
- 空头：无期货市场
- warnings：dev 新钱包、流动性偏薄
- 多项失败应全部出现在 reasons 中（不是 short-circuit）
"""

from sprite_catcher.features.safety_gate import (
    evaluate_long_safety,
    evaluate_short_safety,
)
from sprite_catcher.models import (
    ChipFeatures,
    DevWalletInfo,
    LiquidityInfo,
    TokenAuditInfo,
    TradeSimulationResult,
)


# === 工厂函数：构造"完全干净"的基线，按需修改字段 ===


def _good_audit(**overrides) -> TokenAuditInfo:
    defaults = dict(
        mintable=False,
        freezeable=False,
        pausable=False,
        has_blacklist=False,
        owner_renounced=True,
        owner_has_privileges=False,
        buy_tax=0.0,
        sell_tax=0.0,
    )
    defaults.update(overrides)
    return TokenAuditInfo(**defaults)


def _good_liquidity(**overrides) -> LiquidityInfo:
    defaults = dict(
        liquidity_usd=1_000_000.0,
        lp_locked_pct=0.95,
        lp_lock_remaining_days=365,
        pool_age_days=30,
    )
    defaults.update(overrides)
    return LiquidityInfo(**defaults)


def _good_dev(**overrides) -> DevWalletInfo:
    defaults = dict(
        deployer_address="0xdev",
        prior_deploys=3,
        has_rug_history=False,
        best_prior_market_cap_usd=10_000_000.0,
    )
    defaults.update(overrides)
    return DevWalletInfo(**defaults)


def _good_chip(**overrides) -> ChipFeatures:
    defaults = dict(
        top10_share=0.15,
        top50_share=0.40,
        independent_clusters=80,
        cluster_factor=2.0,
        excluded_count=5,
    )
    defaults.update(overrides)
    return ChipFeatures(**defaults)


def _good_sim(**overrides) -> TradeSimulationResult:
    defaults = dict(
        can_buy=True,
        can_sell=True,
        effective_buy_tax=0.0,
        effective_sell_tax=0.0,
        error=None,
    )
    defaults.update(overrides)
    return TradeSimulationResult(**defaults)


# === evaluate_long_safety ===


def test_long_baseline_passes():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
        sim_result=_good_sim(),
    )
    assert r.passed is True
    assert r.rejected_reasons == ()


def test_long_rejects_mintable():
    r = evaluate_long_safety(
        _good_audit(mintable=True),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert "audit_mintable" in r.rejected_reasons


def test_long_rejects_freezeable_and_pausable_and_blacklist():
    """每个一票否决项都能独立拒绝。"""
    for field in ["freezeable", "pausable", "has_blacklist"]:
        r = evaluate_long_safety(
            _good_audit(**{field: True}),
            _good_liquidity(),
            _good_dev(),
            _good_chip(),
        )
        assert r.passed is False
        assert f"audit_{field}" in r.rejected_reasons


def test_long_owner_not_renounced_but_no_privileges_passes():
    """owner 没放弃但权限无害 → 不应拒绝（这是上一版我修过的"过严"问题）。"""
    r = evaluate_long_safety(
        _good_audit(owner_renounced=False, owner_has_privileges=False),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is True


def test_long_owner_dangerous_rejected():
    """owner 没放弃 AND 有权限 → 拒绝。"""
    r = evaluate_long_safety(
        _good_audit(owner_renounced=False, owner_has_privileges=True),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert "audit_owner_dangerous" in r.rejected_reasons


def test_long_rejects_high_taxes():
    r = evaluate_long_safety(
        _good_audit(buy_tax=0.08, sell_tax=0.06),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert any("buy_tax" in x for x in r.rejected_reasons)
    assert any("sell_tax" in x for x in r.rejected_reasons)


def test_long_rejects_lp_unlocked():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(lp_locked_pct=0.50),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert any("lp_locked_pct" in x for x in r.rejected_reasons)


def test_long_rejects_lp_lock_too_short():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(lp_lock_remaining_days=30),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert any("lp_lock_remaining_days" in x for x in r.rejected_reasons)


def test_long_rejects_thin_liquidity():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(liquidity_usd=50_000.0),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert any("liquidity_usd" in x for x in r.rejected_reasons)


def test_long_rejects_young_pool():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(pool_age_days=5),
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is False
    assert any("pool_age_days" in x for x in r.rejected_reasons)


def test_long_rejects_concentrated_chip():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(),
        _good_chip(top10_share=0.45),
    )
    assert r.passed is False
    assert any("top10_share" in x for x in r.rejected_reasons)


def test_long_rejects_dev_rug_history():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(has_rug_history=True),
        _good_chip(),
    )
    assert r.passed is False
    assert any("dev_rug_history" in x for x in r.rejected_reasons)


def test_long_rejects_honeypot_cannot_sell():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
        sim_result=_good_sim(can_sell=False, error="reverted"),
    )
    assert r.passed is False
    assert any("sim_cannot_sell" in x for x in r.rejected_reasons)


def test_long_no_sim_does_not_block():
    """模拟试卖不可用时不阻塞通过（调用方决定要不要强制要求）。"""
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(),
        _good_chip(),
        sim_result=None,
    )
    assert r.passed is True


def test_long_warnings_dev_first_time():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(),
        _good_dev(prior_deploys=0),
        _good_chip(),
    )
    assert r.passed is True
    assert "dev_first_time" in r.warnings


def test_long_warnings_thin_liquidity():
    r = evaluate_long_safety(
        _good_audit(),
        _good_liquidity(liquidity_usd=250_000.0),  # > 200k 但 < 400k
        _good_dev(),
        _good_chip(),
    )
    assert r.passed is True
    assert "liquidity_thin" in r.warnings


def test_long_multiple_failures_all_reported():
    """关键不变式：所有失败原因都要出现在 reasons 里，不能 short-circuit。"""
    r = evaluate_long_safety(
        _good_audit(mintable=True, freezeable=True),
        _good_liquidity(liquidity_usd=10_000.0, pool_age_days=2),
        _good_dev(has_rug_history=True),
        _good_chip(top10_share=0.95),
    )
    assert r.passed is False
    assert "audit_mintable" in r.rejected_reasons
    assert "audit_freezeable" in r.rejected_reasons
    assert any("liquidity_usd" in x for x in r.rejected_reasons)
    assert any("pool_age_days" in x for x in r.rejected_reasons)
    assert any("dev_rug_history" in x for x in r.rejected_reasons)
    assert any("top10_share" in x for x in r.rejected_reasons)
    # 6 个独立失败项 — 全部上报
    assert len(r.rejected_reasons) >= 6


# === evaluate_short_safety ===


def test_short_baseline_passes():
    r = evaluate_short_safety(
        _good_audit(),
        _good_liquidity(liquidity_usd=2_000_000.0),
        sim_result=_good_sim(),
    )
    assert r.passed is True


def test_short_rejects_no_futures():
    r = evaluate_short_safety(
        _good_audit(),
        _good_liquidity(liquidity_usd=2_000_000.0),
        has_futures_market=False,
    )
    assert r.passed is False
    assert "no_futures_market" in r.rejected_reasons


def test_short_higher_tax_tolerance():
    """空头允许 10% 税（多头只允许 5%），测试这条边界。"""
    audit = _good_audit(buy_tax=0.07, sell_tax=0.07)
    short_r = evaluate_short_safety(
        audit,
        _good_liquidity(liquidity_usd=2_000_000.0),
    )
    assert short_r.passed is True  # 7% < 10% → 通过

    # 但同样的 7% 税多头不能接受
    long_r = evaluate_long_safety(
        audit, _good_liquidity(), _good_dev(), _good_chip(),
    )
    assert long_r.passed is False


def test_short_higher_liquidity_floor():
    """空头要求 $500k 流动性（多头只要 $200k），测试这条边界。"""
    liq = _good_liquidity(liquidity_usd=300_000.0)
    # 多头能过 $200k 阈值
    long_r = evaluate_long_safety(
        _good_audit(), liq, _good_dev(), _good_chip(),
    )
    assert long_r.passed is True
    # 但空头要 $500k，过不了
    short_r = evaluate_short_safety(_good_audit(), liq)
    assert short_r.passed is False
    assert any("liquidity_usd" in x for x in short_r.rejected_reasons)


def test_short_does_not_check_lp_lock():
    """LP 未锁，空头不在意（LP 抽走对空头有利）。"""
    r = evaluate_short_safety(
        _good_audit(),
        _good_liquidity(
            liquidity_usd=2_000_000.0,
            lp_locked_pct=0.0,             # 完全未锁
            lp_lock_remaining_days=0,
        ),
    )
    assert r.passed is True


def test_short_does_not_check_chip_concentration():
    """空头池本来就是因为筹码集中才被分流过来的，不应在这里再 reject。"""
    # evaluate_short_safety 签名里就没有 chip 参数 — 这个测试是反向验证设计
    import inspect
    sig = inspect.signature(evaluate_short_safety)
    assert "chip" not in sig.parameters


def test_short_pool_age_lower_threshold():
    """空头池子年龄要求 7 天（多头 14 天）。"""
    liq = _good_liquidity(liquidity_usd=2_000_000.0, pool_age_days=10)
    # 多头不行（< 14 天）
    long_r = evaluate_long_safety(
        _good_audit(), liq, _good_dev(), _good_chip(),
    )
    assert long_r.passed is False
    # 空头可以（> 7 天）
    short_r = evaluate_short_safety(_good_audit(), liq)
    assert short_r.passed is True


def test_short_honeypot_kills():
    """空头也不能碰蜜罐：你的回购平仓单可能被卡。"""
    r = evaluate_short_safety(
        _good_audit(),
        _good_liquidity(liquidity_usd=2_000_000.0),
        sim_result=_good_sim(can_sell=False, error="honeypot"),
    )
    assert r.passed is False


def test_short_audit_one_veto_still_applies():
    """合约一票否决项对空头同样有效（pausable 能让你平仓平不掉）。"""
    r = evaluate_short_safety(
        _good_audit(pausable=True),
        _good_liquidity(liquidity_usd=2_000_000.0),
    )
    assert r.passed is False
    assert "audit_pausable" in r.rejected_reasons
