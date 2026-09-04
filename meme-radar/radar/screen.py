# -*- coding: utf-8 -*-
"""screen.py — 第二层：硬过滤（直接剔除）+ 软评分（0~100）+ 决策（SKIP / WATCH / PAPER_BUY）。

目标不是"猜中百倍币"，而是在 100 个早期项目里先剔掉七八十个明显有问题的，
再从剩下的里面用很小的资金寻找少数非对称机会。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .models import Candidate, ForensicsResult, PoolSnapshot, SecurityInfo, SmartMoneySignal
from .util import clamp


# ---------------------------------------------------------------- 硬过滤
def hard_filter(snap: PoolSnapshot, security: Optional[SecurityInfo], forensics: Optional[ForensicsResult],
                rules: Dict[str, Any]) -> List[str]:
    hf = rules.get("hard_filters") or {}
    uni = rules.get("universe") or {}
    kills: List[str] = []
    liq = snap.liquidity_usd or 0.0
    if liq < float(hf.get("min_liquidity_usd", 15000)):
        kills.append(f"liquidity<{hf.get('min_liquidity_usd')}")
    age = snap.age_hours
    if age is not None:
        if age * 60 < float(uni.get("min_age_minutes", 20)):
            kills.append("too_new")
        if age > float(uni.get("max_age_hours", 72)):
            kills.append("too_old")
    tx24 = snap.tx("h24", "buys") + snap.tx("h24", "sells")
    if tx24 < int(hf.get("min_txns_24h", 50)):
        kills.append(f"txns24<{hf.get('min_txns_24h')}")
    buyers24 = snap.tx("h24", "buyers")
    if snap.txns.get("h24", {}).get("buyers") is not None and buyers24 < int(hf.get("min_unique_buyers_24h", 30)):
        kills.append(f"buyers24<{hf.get('min_unique_buyers_24h')}")
    vol24 = snap.vol("h24")
    if liq > 0:
        r = vol24 / liq
        if r > float(hf.get("max_vol_to_liq_24h", 60)):
            kills.append("vol/liq_too_high(wash?)")
        if r < float(hf.get("min_vol_to_liq_24h", 0.2)):
            kills.append("vol/liq_too_low(dead)")
    chg24 = snap.chg("h24")
    if chg24 is not None and chg24 > float(hf.get("kill_if_h24_change_pct_above", 3000)):
        kills.append("already_pumped_24h")
    if security:
        if hf.get("kill_if_honeypot", True) and security.is_honeypot:
            kills.append("honeypot")
        if security.sell_tax_pct is not None and security.sell_tax_pct > float(hf.get("max_sell_tax_pct", 10)):
            kills.append("sell_tax_too_high")
        if security.buy_tax_pct is not None and security.buy_tax_pct > float(hf.get("max_buy_tax_pct", 10)):
            kills.append("buy_tax_too_high")
        for f in ("cannot_sell_all", "is_blacklisted", "transfer_pausable", "explorer_scam_tag"):
            if f in security.flags:
                kills.append(f)
    if forensics and forensics.quality in ("full", "partial"):
        if forensics.top10_eoa_pct > float(hf.get("max_top10_holder_pct", 50)):
            kills.append("top10_too_concentrated")
        if forensics.creator_pct > float(hf.get("max_creator_pct", 10)):
            kills.append("creator_holds_too_much")
        if forensics.top1_eoa_pct > float(hf.get("max_single_holder_pct", 25)):
            kills.append("single_holder_too_large")
        if forensics.sybil_score > float(hf.get("kill_if_sybil_score_above", 0.75)):
            kills.append("sybil_score_too_high")
    return kills


# ---------------------------------------------------------------- 软评分
def _tier(v: float, tiers: List[Tuple[float, float]], default: float = 0.0) -> float:
    for th, s in tiers:
        if v >= th:
            return s
    return default


def score(snap: PoolSnapshot, security: Optional[SecurityInfo], forensics: Optional[ForensicsResult],
          smart: Optional[SmartMoneySignal], cross: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    w = rules.get("score_weights") or {}
    b: Dict[str, float] = {}
    liq = snap.liquidity_usd or 0.0
    vol24 = snap.vol("h24")
    tm = (cross or {}).get("metrics") or {}

    # 1 流动性健康
    s = _tier(liq, [(100_000, 1.0), (50_000, 0.8), (25_000, 0.6), (15_000, 0.4), (5_000, 0.2)])
    if liq and vol24 / liq > 40:
        s *= 0.5
    b["liquidity_health"] = s

    # 2 筹码分布
    if forensics and forensics.quality != "none":
        s = 1.0 - clamp((forensics.top10_eoa_pct - 15.0) / 45.0, 0.0, 1.0)
        if forensics.top1_eoa_pct > 15:
            s -= 0.2
        if forensics.holders_total:
            s += _tier(forensics.holders_total, [(1000, 0.2), (500, 0.15), (200, 0.05)])
        if forensics.curve_status == "graduated" or forensics.burn_pct >= 50:
            s += 0.05
        b["distribution"] = clamp(s, 0.0, 1.0)
    else:
        b["distribution"] = 0.5

    # 3 有机增长（参与者）
    buyers1, buyers6 = snap.tx("h1", "buyers"), snap.tx("h6", "buyers")
    have_buyers = snap.txns.get("h1", {}).get("buyers") is not None
    if have_buyers:
        s = _tier(buyers1, [(30, 1.0), (15, 0.7), (5, 0.4)], 0.1)
        if buyers6 and buyers1 > 1.5 * (buyers6 / 6.0):
            s += 0.2
    else:
        s = 0.7 * _tier(snap.tx("h1", "buys"), [(40, 1.0), (20, 0.7), (8, 0.4)], 0.1)
    buys6, sells6 = snap.tx("h6", "buys"), snap.tx("h6", "sells")
    if buys6 + sells6 >= 20:
        ratio = buys6 / max(sells6, 1)
        if not (0.9 <= ratio <= 3.0):
            s -= 0.2
    if tm.get("unique_buyers_trades", 0) >= 40:
        s += 0.1
    b["organic_growth"] = clamp(s, 0.0, 1.0)

    # 4 钱包独立性（取证）
    if forensics and forensics.quality != "none":
        s = 1.0 - forensics.sybil_score
        if forensics.quality in ("partial", "lite"):
            s = 0.5 * s + 0.25
    else:
        s = 0.5
    b["sybil_integrity"] = clamp(s, 0.0, 1.0)

    # 5 聪明钱
    if smart and smart.registry_size >= 10:
        s = _tier(smart.count, [(3, 1.0), (2, 0.75), (1, 0.5)], 0.15)
        if smart.net_buy_usd < 0:
            s *= 0.5
    else:
        s = 0.4   # 库太小不做判断
    b["smart_money"] = clamp(s, 0.0, 1.0)

    # 6 价格结构（不追高、不接飞刀）
    chg1, chg24 = snap.chg("h1"), snap.chg("h24")
    s = 1.0
    if chg1 is not None:
        if chg1 > 150:
            s = 0.2
        elif chg1 > 60:
            s = 0.6
        elif chg1 < -30:
            s = 0.3
    if chg24 is not None and chg24 > 1500:
        s = min(s, 0.2)
    if snap.age_hours is not None and snap.age_hours < 1:
        s = min(s, 0.6)
    b["price_structure"] = clamp(s, 0.0, 1.0)

    # 7 叙事 / 社交
    info = snap.info or {}
    socials = info.get("socials") or []
    s = 0.2
    if info.get("websites"):
        s += 0.3
    if any("twitter" in str(x).lower() or "x.com" in str(x).lower() for x in socials) or info.get("twitter"):
        s += 0.3
    if any("telegram" in str(x).lower() or "discord" in str(x).lower() for x in socials):
        s += 0.2
    if info.get("boosts_active"):
        s = min(s, 0.5)
    b["narrative_social"] = clamp(s, 0.0, 1.0)

    total = sum(b[k] * float(w.get(k, 0)) for k in b)
    return round(total, 2), {k: round(b[k] * float(w.get(k, 0)), 2) for k in b}


# ---------------------------------------------------------------- 决策
def decide(cand: Candidate, regime: Dict[str, Any], rules: Dict[str, Any],
           new_today: int, open_positions: int) -> Tuple[str, List[str], float]:
    dc = rules.get("decision") or {}
    acct = rules.get("experiment_account") or {}
    reasons: List[str] = []
    if cand.killed_by:
        return "SKIP", [f"硬过滤: {', '.join(cand.killed_by)}"], 0.0
    red = cand.flags.get("red") or []
    if cand.score_total < float(dc.get("watch_threshold", 60)):
        return "SKIP", [f"评分 {cand.score_total:.0f} 低于观察线 {dc.get('watch_threshold')}"], 0.0
    decision = "WATCH"
    reasons.append(f"评分 {cand.score_total:.0f} ≥ 观察线")
    if cand.score_total >= float(dc.get("paper_buy_threshold", 72)):
        blockers = []
        if len(red) > int(dc.get("max_red_flags_for_buy", 0)):
            blockers.append(f"红旗 {','.join(red)}")
        budget = float(regime.get("risk_budget") or 0.0)
        if budget <= 0:
            blockers.append(f"体制 {regime.get('regime')} 预算为 0")
        if new_today >= int(regime.get("max_new_positions", dc.get("max_new_positions_per_day", 5))):
            blockers.append("今日新仓额度已用完")
        if open_positions >= int(dc.get("max_open_positions", 15)):
            blockers.append("持仓数达到上限")
        if cand.ai and cand.ai.verdict == "MANIPULATED" and cand.ai.confidence >= float(dc.get("ai_veto_min_confidence", 0.7)):
            blockers.append(f"AI 判定 MANIPULATED（{cand.ai.confidence:.2f}）否决")
        if not blockers:
            decision = "PAPER_BUY"
            size = float(acct.get("capital_usd", 500)) * float(acct.get("base_position_pct", 2.0)) / 100.0 * budget
            size = max(size, float(acct.get("min_position_usd", 2)))
            reasons.append(f"评分 ≥ 买入线，体制预算 {budget:.2f} → 模拟仓 ${size:.2f}")
            return decision, reasons, round(size, 2)
        reasons.append("达到买入线但被拦: " + "; ".join(blockers))
    return decision, reasons, 0.0


# ---------------------------------------------------------------- 扁平特征（进账本、供评估分桶）
def build_features(cand: Candidate, cross: Dict[str, Any]) -> Dict[str, Any]:
    s = cand.snapshot
    f: Dict[str, Any] = {
        "age_hours": s.age_hours, "liquidity_usd": s.liquidity_usd, "fdv_usd": s.fdv_usd,
        "market_cap_usd": s.market_cap_usd, "price_usd": s.price_usd, "dex": s.dex, "quote": s.quote_symbol,
        "vol_h1": s.vol("h1"), "vol_h6": s.vol("h6"), "vol_h24": s.vol("h24"),
        "vol_liq_24h": round(s.vol("h24") / s.liquidity_usd, 3) if s.liquidity_usd else None,
        "chg_m5": s.chg("m5"), "chg_h1": s.chg("h1"), "chg_h6": s.chg("h6"), "chg_h24": s.chg("h24"),
        "buys_h1": s.tx("h1", "buys"), "sells_h1": s.tx("h1", "sells"),
        "buyers_h1": s.txns.get("h1", {}).get("buyers"), "sellers_h1": s.txns.get("h1", {}).get("sellers"),
        "buys_h24": s.tx("h24", "buys"), "sells_h24": s.tx("h24", "sells"),
        "buyers_h24": s.txns.get("h24", {}).get("buyers"),
        "has_socials": bool((s.info or {}).get("socials") or (s.info or {}).get("websites")),
        "boosts": (s.info or {}).get("boosts_active") or 0,
    }
    if cand.security:
        f.update({"security_source": cand.security.source, "honeypot": cand.security.is_honeypot,
                  "sell_tax_pct": cand.security.sell_tax_pct, "launchpad": cand.security.launchpad,
                  "has_owner": cand.security.has_owner, "security_flags": cand.security.flags})
    fo = cand.forensics
    if fo:
        f.update({"forensics_quality": fo.quality, "holders_total": fo.holders_total,
                  "top10_eoa_pct": fo.top10_eoa_pct, "top1_eoa_pct": fo.top1_eoa_pct,
                  "creator_pct": fo.creator_pct, "contract_held_pct": fo.contract_held_pct, "burn_pct": fo.burn_pct,
                  "clustered_pct": fo.clustered_pct, "largest_cluster_pct": fo.largest_cluster_pct,
                  "cluster_count": len(fo.clusters), "fresh_wallet_pct": fo.fresh_wallet_pct,
                  "fresh_wallet_count": fo.fresh_wallet_count, "sybil_score": fo.sybil_score,
                  "early_buyers_holding_pct": fo.early_buyers_holding_pct, "curve_status": fo.curve_status,
                  "launchpad": fo.launchpad or f.get("launchpad", "")})
    sm = cand.smart_money
    if sm:
        f.update({"smart_count": sm.count, "smart_weighted": sm.weighted, "smart_net_buy_usd": sm.net_buy_usd,
                  "smart_registry_size": sm.registry_size})
    f.update({f"x_{k}": v for k, v in ((cross or {}).get("metrics") or {}).items()})
    f.update({"flags_red": cand.flags.get("red", []), "flags_yellow": cand.flags.get("yellow", []),
              "flags_green": cand.flags.get("green", []), "score_total": cand.score_total})
    f.update({f"score_{k}": v for k, v in cand.score_breakdown.items()})
    if cand.ai:
        f.update({"ai_verdict": cand.ai.verdict, "ai_confidence": cand.ai.confidence, "ai_provider": cand.ai.provider})
    return f
