# -*- coding: utf-8 -*-
"""crossval.py — 第四层：价格 / 真实资金 / 参与者 交叉验证。

原则：价格涨了但链上没有真实新增资金 → 高风险；价格还没启动但真实买入与参与者持续增加 → 重点观察。
输入是快照（GeckoTerminal/DexScreener）、最近成交（含钱包）、取证结果、上一轮快照（若有）。
输出 flags{red,yellow,green} + metrics。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import ForensicsResult, PoolSnapshot
from .util import now_utc, parse_iso, safe_float


def trade_metrics(trades: List[dict], liquidity_usd: Optional[float]) -> Dict[str, Any]:
    now = now_utc()
    buy1 = sell1 = buy_all = sell_all = 0.0
    buyers_1h, buyers_all = set(), set()
    per_buyer: Dict[str, float] = {}
    sizes = []
    for t in trades or []:
        v = float(t.get("volume_usd") or 0.0)
        ts = parse_iso(t.get("ts"))
        recent = bool(ts) and (now - ts).total_seconds() <= 3600
        w = t.get("wallet") or ""
        sizes.append(v)
        if t.get("kind") == "buy":
            buy_all += v
            buyers_all.add(w)
            per_buyer[w] = per_buyer.get(w, 0.0) + v
            if recent:
                buy1 += v
                buyers_1h.add(w)
        else:
            sell_all += v
            if recent:
                sell1 += v
    top5 = sorted(per_buyer.values(), reverse=True)[:5]
    top5_share = (sum(top5) / buy_all) if buy_all > 0 else None
    sizes.sort()
    med = sizes[len(sizes) // 2] if sizes else None
    return {
        "trades_n": len(trades or []),
        "net_flow_1h_usd": round(buy1 - sell1, 2),
        "net_flow_all_usd": round(buy_all - sell_all, 2),
        "buy_usd_all": round(buy_all, 2), "sell_usd_all": round(sell_all, 2),
        "unique_buyers_1h": len(buyers_1h), "unique_buyers_trades": len(buyers_all),
        "top5_buyer_share": round(top5_share, 3) if top5_share is not None else None,
        "median_trade_usd": round(med, 2) if med is not None else None,
        "net_flow_1h_to_liq": round((buy1 - sell1) / liquidity_usd, 4) if liquidity_usd else None,
    }


def cross_validate(snap: PoolSnapshot, trades: List[dict], forensics: Optional[ForensicsResult],
                   prev: Optional[dict] = None) -> Dict[str, Any]:
    red: List[str] = []
    yellow: List[str] = []
    green: List[str] = []
    liq = snap.liquidity_usd or 0.0
    tm = trade_metrics(trades, liq)
    chg1, chg6, chg24 = snap.chg("h1"), snap.chg("h6"), snap.chg("h24")
    buys1, sells1 = snap.tx("h1", "buys"), snap.tx("h1", "sells")
    buyers1 = snap.tx("h1", "buyers") or None
    buyers6 = snap.tx("h6", "buyers") or None
    buyers24 = snap.tx("h24", "buyers") or None
    buys6, sells6 = snap.tx("h6", "buys"), snap.tx("h6", "sells")
    vol24 = snap.vol("h24")

    # ---- 红：价涨无钱 / 买盘高度集中 / 流动性抽走
    if chg1 is not None and chg1 >= 30:
        few_buyers = (buyers1 is not None and buyers1 <= 3) or (tm["trades_n"] >= 10 and tm["unique_buyers_1h"] <= 3)
        if tm["trades_n"] >= 10 and (tm["net_flow_1h_usd"] <= 0 or few_buyers):
            red.append("PRICE_UP_NO_INFLOW")
    if tm["top5_buyer_share"] is not None and tm["trades_n"] >= 15 and tm["top5_buyer_share"] >= 0.7:
        red.append("BUYS_CONCENTRATED")
    if prev and safe_float(prev.get("liquidity_usd")) and liq and liq < 0.6 * float(prev["liquidity_usd"]):
        red.append("LIQUIDITY_DRAINING")
    if forensics and forensics.quality != "none" and forensics.largest_cluster_pct >= 20:
        red.append("CLUSTER_CONTROLS_SUPPLY")

    # ---- 黄
    if sells1 >= 10 and sells1 > 1.5 * max(buys1, 1):
        yellow.append("SELLERS_DOMINATE")
    if (chg1 is not None and chg1 >= 150) or (chg6 is not None and chg6 >= 400):
        yellow.append("CHASING_RISK")
    if liq and vol24 / liq > 30 and (buyers24 is not None and buyers24 < 50):
        yellow.append("WASH_SUSPECT")
    if (snap.info or {}).get("boosts_active"):
        yellow.append("PAID_PROMOTION")
    if forensics and forensics.quality != "none":
        if forensics.fresh_wallet_pct >= 15:
            yellow.append("FRESH_WALLETS_HEAVY")
        if forensics.creator_pct > 5:
            yellow.append("CREATOR_HOLDS_LARGE")
        if forensics.early_buyers_holding_pct is not None and forensics.early_buyers_holding_pct >= 25:
            yellow.append("SNIPERS_STILL_HOLDING")
    elif forensics is None or forensics.quality == "none":
        yellow.append("FORENSICS_UNAVAILABLE")

    # ---- 绿：吸筹 / 广泛参与 / 买卖健康 / 有社交
    if tm["net_flow_1h_to_liq"] is not None and tm["net_flow_1h_to_liq"] >= 0.05 and chg1 is not None and -10 <= chg1 <= 15:
        green.append("INFLOW_NO_PRICE")
    if (buyers1 or 0) >= 25 and (buyers6 or 0) >= 80 and (tm["top5_buyer_share"] is None or tm["top5_buyer_share"] <= 0.4):
        green.append("BROAD_PARTICIPATION")
    if buys6 + sells6 >= 30 and 0.9 <= buys6 / max(sells6, 1) <= 2.5:
        green.append("HEALTHY_BUY_SELL")
    info = snap.info or {}
    if info.get("socials") or info.get("websites") or info.get("twitter"):
        green.append("HAS_SOCIALS")
    if forensics and forensics.quality == "full" and forensics.sybil_score <= 0.2 and not forensics.clusters:
        green.append("HOLDERS_LOOK_INDEPENDENT")
    if forensics and forensics.launchpad.startswith("pons") and forensics.curve_status == "graduated":
        green.append("GRADUATED_LOCKED_LP")

    return {"flags": {"red": red, "yellow": yellow, "green": green}, "metrics": tm}
