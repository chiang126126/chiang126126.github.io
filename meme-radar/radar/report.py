# -*- coding: utf-8 -*-
"""report.py — 产出看板数据（data/summary.json 等）与每日 Markdown 简报。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Candidate
from .util import DATA_DIR, day_key, hours_between, iso, load_json, now_utc, parse_iso, save_json


def _cand_brief(c: Candidate) -> Dict[str, Any]:
    s = c.snapshot
    fo, sm = c.forensics, c.smart_money
    return {
        "token": c.token, "symbol": c.symbol, "name": c.name, "pool": c.pool, "dex": s.dex, "url": s.url,
        "decision": c.decision, "score": round(c.score_total, 1), "score_breakdown": {k: round(v, 1) for k, v in c.score_breakdown.items()},
        "flags": c.flags, "killed_by": c.killed_by, "reasons": c.decision_reasons,
        "price_usd": s.price_usd, "liquidity_usd": s.liquidity_usd, "fdv_usd": s.fdv_usd, "age_hours": s.age_hours,
        "vol_h1": s.vol("h1"), "vol_h24": s.vol("h24"), "chg_h1": s.chg("h1"), "chg_h6": s.chg("h6"), "chg_h24": s.chg("h24"),
        "buyers_h1": s.txns.get("h1", {}).get("buyers"), "buyers_h24": s.txns.get("h24", {}).get("buyers"),
        "buys_h24": s.tx("h24", "buys"), "sells_h24": s.tx("h24", "sells"),
        "forensics": {"quality": fo.quality, "holders": fo.holders_total, "top10_eoa_pct": fo.top10_eoa_pct,
                      "clustered_pct": fo.clustered_pct, "largest_cluster_pct": fo.largest_cluster_pct,
                      "clusters": fo.clusters[:5], "fresh_wallet_pct": fo.fresh_wallet_pct, "fresh_wallet_count": fo.fresh_wallet_count,
                      "creator_pct": fo.creator_pct, "sybil_score": fo.sybil_score, "launchpad": fo.launchpad,
                      "curve_status": fo.curve_status, "early_buyers_holding_pct": fo.early_buyers_holding_pct,
                      "notes": fo.notes, "holder_map": fo.holder_map, "inspected": fo.inspected, "profiled": fo.profiled,
                      "inspected_pct": fo.inspected_pct, "contract_held_pct": fo.contract_held_pct, "burn_pct": fo.burn_pct} if fo else None,
        "security": c.security.to_dict() if c.security else None,
        "smart_money": {"count": sm.count, "weighted": sm.weighted, "net_buy_usd": sm.net_buy_usd,
                        "wallets": sm.wallets[:5], "registry_size": sm.registry_size} if sm else None,
        "ai": c.ai.to_dict() if c.ai else None,
        "position_size_usd": c.position_size_usd,
        "x": {k: v for k, v in (c.features or {}).items() if k.startswith("x_")},
    }


def build_watchlist(candidates: List[Candidate], ledger, keep_hours: float = 96) -> Dict[str, Any]:
    """近 96h 内曾被 WATCH / PAPER_BUY 的代币 + 最新状态。"""
    prev = load_json(DATA_DIR / "watchlist.json", {}) or {}
    items: Dict[str, dict] = {it["token"]: it for it in (prev.get("items") or []) if isinstance(it, dict)}
    now = now_utc()
    for c in candidates:
        if c.decision in ("WATCH", "PAPER_BUY"):
            it = items.get(c.token) or {"token": c.token, "symbol": c.symbol, "first_seen": iso(), "history": []}
            it.update({"symbol": c.symbol, "name": c.name, "pool": c.pool, "url": c.snapshot.url, "last_seen": iso(),
                       "decision": c.decision, "score": round(c.score_total, 1), "price_usd": c.snapshot.price_usd,
                       "liquidity_usd": c.snapshot.liquidity_usd, "fdv_usd": c.snapshot.fdv_usd,
                       "flags": c.flags, "sybil_score": c.forensics.sybil_score if c.forensics else None,
                       "smart_count": c.smart_money.count if c.smart_money else 0,
                       "ai": c.ai.verdict if c.ai else None})
            it["history"] = (it.get("history") or [])[-20:] + [{"at": iso(), "score": round(c.score_total, 1),
                                                                 "decision": c.decision, "price": c.snapshot.price_usd}]
            items[c.token] = it
    # 附上账本里的结果
    by_token: Dict[str, dict] = {}
    for s in ledger.samples:
        if s.get("decision") in ("WATCH", "PAPER_BUY"):
            by_token[s["token"]] = s
    out = []
    for t, it in items.items():
        if hours_between(it.get("last_seen"), now) is not None and hours_between(it.get("last_seen"), now) > keep_hours:
            continue
        s = by_token.get(t)
        if s:
            it["outcomes"] = s.get("outcomes") or {}
            it["status"] = s.get("status")
            it["first_price"] = s.get("price_at")
        out.append(it)
    out.sort(key=lambda x: (x.get("decision") != "PAPER_BUY", -(x.get("score") or 0)))
    return {"updated": iso(), "items": out}


def write_outputs(regime: Dict[str, Any], candidates: Optional[List[Candidate]], universe: Dict[str, Any], ledger,
                  evaluation: Dict[str, Any], run_meta: Dict[str, Any], baseline_symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """candidates=None 表示本轮没跑 scan：沿用上一轮 candidates.json / watchlist.json，只刷新其它板块。"""
    order = {"PAPER_BUY": 0, "WATCH": 1, "SKIP": 2}
    if candidates is None:
        prev_c = load_json(DATA_DIR / "candidates.json", {}) or {}
        briefs = prev_c.get("items") or []
        universe = universe or prev_c.get("universe") or {}
        watchlist = build_watchlist([], ledger)
        candidates_updated = prev_c.get("updated")
    else:
        briefs = [_cand_brief(c) for c in candidates]
        briefs.sort(key=lambda b: (order.get(b["decision"], 3), -(b["score"] or 0)))
        watchlist = build_watchlist(candidates, ledger)
        candidates_updated = iso()
        save_json(DATA_DIR / "candidates.json", {"updated": candidates_updated, "regime": regime.get("regime"),
                                                 "universe": universe, "items": briefs})
    save_json(DATA_DIR / "watchlist.json", watchlist)
    candidates = candidates or []
    positions = ledger.positions
    recent_samples = sorted(ledger.samples, key=lambda s: s.get("discovered_at") or "", reverse=True)[:60]
    summary = {
        "updated": iso(),
        "run": run_meta,
        "regime": regime,
        "universe": universe,
        "candidates_updated": candidates_updated,
        "counts": {"candidates": len(briefs),
                   "paper_buy": sum(1 for b in briefs if b["decision"] == "PAPER_BUY"),
                   "watch": sum(1 for b in briefs if b["decision"] == "WATCH"),
                   "skip": sum(1 for b in briefs if b["decision"] == "SKIP")},
        "top": briefs[:25],
        "watchlist": watchlist["items"][:40],
        "portfolio": ledger.portfolio_summary(),
        "positions_open": positions.get("open", []),
        "positions_closed_recent": sorted(positions.get("closed", []), key=lambda p: p.get("closed_at") or "", reverse=True)[:30],
        "samples": ledger.sample_stats(),
        "recent_samples": [{k: s.get(k) for k in ("sample_id", "symbol", "token", "decision", "score", "discovered_at",
                                                  "price_at", "liquidity_at", "status", "outcomes", "regime", "url")}
                           for s in recent_samples],
        "evaluation": evaluation,
        "baseline_symbols": baseline_symbols or [],
    }
    save_json(DATA_DIR / "summary.json", summary)
    return summary


def daily_report_md(summary: Dict[str, Any]) -> str:
    r = summary.get("regime") or {}
    ev = summary.get("evaluation") or {}
    pf = summary.get("portfolio") or {}
    h24 = ((ev.get("horizons") or {}).get("h24") or {})
    lines = [f"# meme-radar 日报 · {day_key()}", "",
             f"**市场环境**：{r.get('regime')}（{r.get('regime_zh')}）· 风险预算 {r.get('risk_budget')} · 置信 {r.get('confidence')}",
             f"> {r.get('judgment', '')}", ""]
    for s in r.get("support") or []:
        lines.append(f"- {s}")
    for c in r.get("challenge") or []:
        lines.append(f"- ⚠️ {c}")
    u = summary.get("universe") or {}
    lines += ["", f"**本轮扫描**：发现 {u.get('discovered', 0)} 个池 → 预过滤后 {u.get('prefiltered', 0)} → 取证 {u.get('forensics_done', 0)} → "
                  f"PAPER_BUY {summary['counts'].get('paper_buy', 0)} / WATCH {summary['counts'].get('watch', 0)} / SKIP {summary['counts'].get('skip', 0)}", ""]
    tops = [b for b in summary.get("top") or [] if b["decision"] in ("PAPER_BUY", "WATCH")][:10]
    if tops:
        lines += ["## 今日候选", "", "| 决策 | 代币 | 评分 | 流动性 | 年龄 | 1h | sybil | 聪明钱 | 旗帜 |", "|---|---|---|---|---|---|---|---|---|"]
        for b in tops:
            fo = b.get("forensics") or {}
            sm = b.get("smart_money") or {}
            flags = ",".join((b["flags"].get("red") or [])[:2] + (b["flags"].get("yellow") or [])[:2])
            liq = f"${(b.get('liquidity_usd') or 0) / 1000:.0f}k"
            age = f"{b.get('age_hours') or 0:.1f}h"
            chg = f"{b.get('chg_h1'):+.0f}%" if b.get("chg_h1") is not None else "-"
            lines.append(f"| {b['decision']} | {b['symbol']} | {b['score']} | {liq} | {age} | {chg} | {fo.get('sybil_score', '-')} | {sm.get('count', 0)} | {flags} |")
    lines += ["", "## 模拟组合（实验账户）", "",
              f"- 权益 ${pf.get('equity_usd')}（本金 ${pf.get('capital_usd')}），已实现 ${pf.get('realized_pnl_usd')}，未实现 ${pf.get('unrealized_pnl_usd')}",
              f"- 持仓 {pf.get('open_positions')}，已平 {pf.get('closed_positions')}，胜率 {pf.get('win_rate')}，盈亏因子 {pf.get('profit_factor')}，最大回撤 {pf.get('max_drawdown_pct')}%",
              "", "## 验证进度（筛选 vs 随机）", "",
              f"- 结论：**{ev.get('verdict')}**（SELECTED {ev.get('progress', {}).get('selected', 0)} / BASELINE {ev.get('progress', {}).get('baseline', 0)}，各需 ≥ {ev.get('min_samples')}）"]
    for g in ("SELECTED", "BUY", "BASELINE", "SKIP"):
        gs = h24.get(g) or {}
        if gs.get("n"):
            lines.append(f"- {g}: n={gs['n']}，24h 中位 {gs.get('median_ret_pct')}%，命中率 {gs.get('hit_rate')}，归零率 {gs.get('rug_rate')}")
    d = h24.get("selected_vs_baseline")
    if d:
        lines.append(f"- 命中率差（SELECTED−BASELINE）{d['diff']:+.3f}，95% CI [{d['ci_low']:+.3f}, {d['ci_high']:+.3f}]")
    lines += ["", "> 本报告由规则引擎自动生成；AI 仅做『真实市场』审查与否决，不做价格预测。所有仓位均为模拟。"]
    return "\n".join(lines) + "\n"


def write_daily_report(summary: Dict[str, Any]) -> str:
    md = daily_report_md(summary)
    path = DATA_DIR / "reports" / f"{day_key()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return str(path)
