# -*- coding: utf-8 -*-
"""regime.py — 第一层：市场环境判断 → 风险预算。

思想：BTC 是整个市场的"水库"。BTC 先涨、先稳，资金才会从 BTC → ETH/大市值 → 小市值/MEME 扩散。
所以在 BTC 高风险下跌阶段，即使发现"小币机会"，也把实验账户的新仓额度压到 0。

输入（全部 best-effort，缺失只降低 confidence）：
  BTC 日线（EMA20/50/200、7d/30d 变化）、ETH/BTC 7d/30d、BTC 占比及其 7 日变化、
  前 100 币跑赢 BTC 的比例（山寨季代理，7d/30d）、恐惧贪婪、Robinhood Chain 活跃度。
输出：regime ∈ {RISK_OFF, BTC_ONLY, ROTATION, ALT_SEASON} + blow_off 叠加 + risk_budget(0~1)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .util import clamp, ema, iso, now_utc, parse_iso, pct_change, safe_float

REGIME_ZH = {
    "RISK_OFF": "风险释放·只看不买",
    "BTC_ONLY": "BTC 主导·小额试错",
    "ROTATION": "资金轮动开始·正常预算",
    "ALT_SEASON": "山寨季·满预算",
}


def _dominance_change_7d(history: List[dict], current: Optional[float]) -> Optional[float]:
    """从自己的历史记录里找 ≥6.5 天前最近的一条占比。"""
    if current is None:
        return None
    now = now_utc()
    best = None
    for h in history:
        ts = parse_iso(h.get("ts"))
        d = safe_float((h.get("metrics") or {}).get("btc_dominance"))
        if not ts or d is None:
            continue
        age_days = (now - ts).total_seconds() / 86400.0
        if age_days >= 6.5 and (best is None or age_days < best[0]):
            best = (age_days, d)
    return round(current - best[1], 3) if best else None


def _chain_activity_wow(history: List[dict], current: Optional[float]) -> Optional[float]:
    if current is None:
        return None
    now = now_utc()
    best = None
    for h in history:
        ts = parse_iso(h.get("ts"))
        v = safe_float((h.get("metrics") or {}).get("chain_top_pools_vol_24h"))
        if not ts or v is None or v <= 0:
            continue
        age_days = (now - ts).total_seconds() / 86400.0
        if age_days >= 6.5 and (best is None or age_days < best[0]):
            best = (age_days, v)
    return round(pct_change(current, best[1]), 2) if best else None


def compute_regime(market, rules: Dict[str, Any], history: Optional[List[dict]] = None,
                   chain_activity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rg = rules.get("regime") or {}
    history = history or []
    m: Dict[str, Any] = {}
    missing: List[str] = []
    support: List[str] = []
    challenge: List[str] = []

    # ---------------------------------------------------------------- BTC 趋势
    closes = market.daily_closes("BTC-USDT", 300) if market else []
    trend = "UNKNOWN"
    up_count = 0
    if len(closes) >= 60:
        price = closes[-1]
        e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200) if len(closes) >= 200 else None
        m.update({
            "btc_price": round(price, 2),
            "btc_ema20": round(e20, 2) if e20 else None,
            "btc_ema50": round(e50, 2) if e50 else None,
            "btc_ema200": round(e200, 2) if e200 else None,
            "btc_change_24h_pct": round(pct_change(closes[-1], closes[-2]) or 0.0, 2),
            "btc_change_7d_pct": round(pct_change(closes[-1], closes[-8]) or 0.0, 2) if len(closes) >= 8 else None,
            "btc_change_30d_pct": round(pct_change(closes[-1], closes[-31]) or 0.0, 2) if len(closes) >= 31 else None,
        })
        rets = [abs(closes[i] / closes[i - 1] - 1) for i in range(max(1, len(closes) - 14), len(closes))]
        m["btc_avg_abs_daily_move_pct"] = round(sum(rets) / len(rets) * 100, 2) if rets else None
        up_count = int(bool(e20 and price > e20)) + int(bool(e50 and price > e50)) \
            + int(bool(e200 and price > e200)) + int(bool(e50 and e200 and e50 > e200))
        chg7 = m.get("btc_change_7d_pct") or 0.0
        chg30 = m.get("btc_change_30d_pct") or 0.0
        if chg7 <= rg.get("btc_7d_drop_risk_off_pct", -10) or (e50 and e200 and price < e50 and price < e200):
            trend = "DOWN"
        elif up_count >= 3 and chg30 > 0:
            trend = "STRONG"
        elif up_count >= 2:
            trend = "UP"
        else:
            trend = "WEAK"
        support.append(f"BTC ≈ {price:,.0f}，站上均线数 {up_count}/4，7d {chg7:+.1f}%，30d {chg30:+.1f}%")
    else:
        missing.append("btc_candles")

    # ---------------------------------------------------------------- ETH/BTC
    ethbtc = market.daily_closes("ETH-BTC", 60) if market else []
    if len(ethbtc) >= 31:
        m["eth_btc_change_7d_pct"] = round(pct_change(ethbtc[-1], ethbtc[-8]) or 0.0, 2)
        m["eth_btc_change_30d_pct"] = round(pct_change(ethbtc[-1], ethbtc[-31]) or 0.0, 2)
    else:
        missing.append("eth_btc")

    # ---------------------------------------------------------------- 占比 / 广度 / 情绪
    g = market.global_metrics() if market else {}
    m["btc_dominance"] = g.get("btc_dominance")
    m["eth_dominance"] = g.get("eth_dominance")
    m["total_mcap_change_24h_pct"] = g.get("mcap_change_24h_pct")
    if m["btc_dominance"] is None:
        missing.append("dominance")
    m["btc_dominance_change_7d"] = _dominance_change_7d(history, m["btc_dominance"])

    coins = market.top_coins(100) if market else []
    b30 = market.alt_breadth(coins, "30d") if coins else None
    b7 = market.alt_breadth(coins, "7d") if coins else None
    m["alt_breadth_30d"] = b30["breadth"] if b30 else None
    m["alt_breadth_7d"] = b7["breadth"] if b7 else None
    if not b30:
        missing.append("alt_breadth")

    fg = market.fear_greed() if market else {}
    m["fear_greed"] = fg.get("value")
    m["fear_greed_class"] = fg.get("classification")

    if chain_activity:
        m["chain_top_pools_vol_24h"] = chain_activity.get("top_pools_vol_24h")
        m["chain_new_pools_24h_est"] = chain_activity.get("new_pools_24h_est")
        m["chain_vol_wow_pct"] = _chain_activity_wow(history, m["chain_top_pools_vol_24h"])

    # ---------------------------------------------------------------- 轮动评分
    rotation = 0.0
    if (m.get("eth_btc_change_7d_pct") or 0) > 0:
        rotation += 1
    if m.get("btc_dominance_change_7d") is not None and m["btc_dominance_change_7d"] < 0:
        rotation += 1
    breadth30 = m.get("alt_breadth_30d")
    if breadth30 is not None:
        if breadth30 >= rg.get("alt_breadth_rotation", 0.5):
            rotation += 1
        if breadth30 >= rg.get("alt_breadth_alt_season", 0.75):
            rotation += 1
    if (m.get("alt_breadth_7d") or 0) >= 0.6:
        rotation += 0.5
    m["rotation_score"] = rotation
    m["btc_trend"] = trend

    # ---------------------------------------------------------------- 定体制
    if trend == "DOWN":
        regime = "RISK_OFF"
    elif trend in ("UP", "STRONG"):
        if rotation >= 3 and (breadth30 or 0) >= rg.get("alt_breadth_alt_season", 0.75):
            regime = "ALT_SEASON"
        elif rotation >= 2:
            regime = "ROTATION"
        else:
            regime = "BTC_ONLY"
    elif trend == "WEAK":
        regime = "BTC_ONLY"
        challenge.append("BTC 趋势偏弱（均线支撑不足），预算按 BTC_ONLY 处理但需警惕转 RISK_OFF")
    else:
        regime = "BTC_ONLY"
        challenge.append("BTC 日线数据缺失，默认按 BTC_ONLY 保守处理")

    blow_off = False
    fgv = m.get("fear_greed")
    if fgv is not None and fgv >= rg.get("fear_greed_blow_off", 85) and (
            (m.get("btc_change_7d_pct") or 0) >= rg.get("btc_7d_gain_blow_off_pct", 20) or (breadth30 or 0) >= 0.9):
        blow_off = True
        challenge.append(f"极度贪婪（F&G {fgv:.0f}）叠加急涨，属于冲顶风险区，预算减半、止盈收紧")

    budget = float((rg.get("risk_budget") or {}).get(regime, 0.0))
    if blow_off:
        budget *= float(rg.get("blow_off_multiplier", 0.5))
    max_new = int((rg.get("max_new_positions_by_regime") or {}).get(regime, 0))

    # ---------------------------------------------------------------- 说明文字
    if m.get("btc_dominance") is not None:
        d7 = m.get("btc_dominance_change_7d")
        support.append(f"BTC 占比 {m['btc_dominance']:.1f}%" + (f"（7 日 {d7:+.2f}pp）" if d7 is not None else "（7 日变化待累计历史）"))
    if b30:
        support.append(f"前 100 币中 30d 跑赢 BTC 的占 {b30['breadth'] * 100:.0f}%（{b30['beat_btc']}/{b30['n']}）"
                       + (f"，7d 占 {b7['breadth'] * 100:.0f}%" if b7 else ""))
    if m.get("eth_btc_change_7d_pct") is not None:
        support.append(f"ETH/BTC 7d {m['eth_btc_change_7d_pct']:+.1f}%，30d {m.get('eth_btc_change_30d_pct', 0):+.1f}%")
    if fgv is not None:
        support.append(f"恐惧贪婪 {fgv:.0f}（{m.get('fear_greed_class') or ''}）")
    if m.get("chain_top_pools_vol_24h"):
        wow = m.get("chain_vol_wow_pct")
        support.append(f"Robinhood Chain 头部池 24h 成交 ≈ ${m['chain_top_pools_vol_24h'] / 1e6:.1f}M"
                       + (f"，周环比 {wow:+.0f}%" if wow is not None else ""))
    if regime == "BTC_ONLY":
        challenge.append("资金仍集中在 BTC/少数大币，全面山寨季未确认：只用小额样本验证方法，不加码")
    if regime == "RISK_OFF":
        challenge.append("BTC 处于下行/破位结构，新仓预算为 0：只记录候选，不开模拟仓")

    completeness = 1.0 - 0.2 * len(missing)
    confidence = int(clamp(100 * completeness * (0.75 if trend in ("WEAK", "UNKNOWN") else 1.0), 10, 95))
    judgment = f"BTC 趋势 {trend}，轮动分 {rotation:.1f}/4.5 → {regime}（{REGIME_ZH.get(regime, regime)}），" \
               f"风险预算 {budget:.2f}，今日最多新开 {max_new} 个模拟仓" + ("，冲顶风险叠加" if blow_off else "")

    return {
        "ts": iso(),
        "regime": regime,
        "regime_zh": REGIME_ZH.get(regime, regime),
        "blow_off": blow_off,
        "risk_budget": round(budget, 3),
        "max_new_positions": max_new,
        "confidence": confidence,
        "judgment": judgment,
        "support": support,
        "challenge": challenge,
        "metrics": m,
        "missing_inputs": missing,
    }


def compact_history_entry(regime: Dict[str, Any]) -> Dict[str, Any]:
    """写入 regime_history.jsonl 的精简行（供 7 日变化计算）。"""
    m = regime.get("metrics") or {}
    return {"ts": regime.get("ts"), "regime": regime.get("regime"), "risk_budget": regime.get("risk_budget"),
            "metrics": {k: m.get(k) for k in ("btc_price", "btc_dominance", "alt_breadth_30d", "alt_breadth_7d",
                                                "eth_btc_change_7d_pct", "fear_greed", "chain_top_pools_vol_24h",
                                                "chain_new_pools_24h_est", "btc_change_7d_pct")}}
