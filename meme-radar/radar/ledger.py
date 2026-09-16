# -*- coding: utf-8 -*-
"""ledger.py — 第五层：样本账本 + 模拟仓 + 结果回填。

每个候选（含被 SKIP 的部分样本与随机基线）都记录『发现时的全部特征 + 决策』，
之后按 1h / 6h / 24h / 7d 回填『之后发生了什么』。这是整个系统真正的资产：
跑满 50–100 个样本后，evaluate.py 才能回答"筛选是否优于随机"。

模拟仓规则（experiment_account）：翻倍收回一半本金 → 再涨再收 → 剩余用回撤止盈；
-50% 止损；72h 没有 +30% 就时间止损；流动性抽走按 RUG 立即出局。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

from .models import Candidate, PoolSnapshot
from .util import (DATA_DIR, day_key, hours_between, iso, load_json, now_utc, parse_iso, read_jsonl, save_json,
                   write_jsonl)

PriceFetcher = Callable[[str, str, int, int], Dict[str, Any]]
# fetch(pool, token, since_ts, until_ts) -> {"candles": [[ts,o,h,l,c,v],...], "price_now": float|None,
#                                            "liq_now": float|None, "pool_alive": bool}


class Ledger:
    def __init__(self, rules: Dict[str, Any], data_dir=None):
        self.rules = rules
        self.dir = data_dir or DATA_DIR
        self.samples_path = self.dir / "ledger.jsonl"
        self.positions_path = self.dir / "positions.json"
        self.samples: List[dict] = list(read_jsonl(self.samples_path))
        raw = load_json(self.positions_path, {}) or {}
        self.positions: Dict[str, Any] = {"open": raw.get("open", []), "closed": raw.get("closed", []),
                                          "updated": raw.get("updated")}
        self.horizons = [int(h) for h in (rules.get("outcomes") or {}).get("horizons_hours", [1, 6, 24, 168])]

    # ---------------------------------------------------------------- persistence
    def save(self):
        write_jsonl(self.samples_path, self.samples)
        self.positions["updated"] = iso()
        save_json(self.positions_path, self.positions)

    # ---------------------------------------------------------------- samples
    def _sample_id(self, token: str, when: Optional[str] = None) -> str:
        return f"{day_key(parse_iso(when)) if when else day_key()}:{token}"

    def existing_ids(self) -> set:
        return {s.get("sample_id") for s in self.samples}

    def _row_from_candidate(self, c: Candidate, regime: Dict[str, Any], decision: Optional[str] = None) -> dict:
        s = c.snapshot
        return {
            "sample_id": self._sample_id(c.token), "token": c.token, "symbol": c.symbol, "name": c.name,
            "pool": c.pool, "chain": s.chain, "discovered_at": iso(), "pool_created_at": s.pool_created_at,
            "rules_version": self.rules.get("version"), "regime": regime.get("regime"),
            "risk_budget": regime.get("risk_budget"), "decision": decision or c.decision,
            "decision_reasons": c.decision_reasons, "killed_by": c.killed_by, "score": c.score_total,
            "position_size_usd": c.position_size_usd, "price_at": s.price_usd, "liquidity_at": s.liquidity_usd,
            "fdv_at": s.fdv_usd, "mcap_at": s.market_cap_usd, "features": c.features, "flags": c.flags,
            "ai": c.ai.to_dict() if c.ai else None, "early_buyers": (c.forensics.early_buyers if c.forensics else [])[:30],
            "status": "pending", "outcomes": {}, "last_checked": None, "url": s.url,
        }

    def add_samples(self, cands: List[Candidate], regime: Dict[str, Any]) -> int:
        ids = self.existing_ids()
        n = 0
        for c in cands:
            if not c.snapshot.price_usd:
                continue
            sid = self._sample_id(c.token)
            if sid in ids:
                continue
            self.samples.append(self._row_from_candidate(c, regime))
            ids.add(sid)
            n += 1
        return n

    def add_baseline(self, snaps: List[PoolSnapshot], regime: Dict[str, Any]) -> int:
        """随机基线：从『整个宇宙』里随机抽（不管我们怎么判断），用独立 id 命名空间（:B），
        同一代币可以同时以自己的决策行和基线行存在——这样基线才是无偏的『随机选』。"""
        ids = self.existing_ids()
        n = 0
        for s in snaps:
            if not s.price_usd:
                continue
            sid = self._sample_id(s.base_token) + ":B"
            if sid in ids:
                continue
            self.samples.append({
                "sample_id": sid, "token": s.base_token, "symbol": s.base_symbol, "name": s.base_name, "pool": s.pool_address,
                "chain": s.chain, "discovered_at": iso(), "pool_created_at": s.pool_created_at,
                "rules_version": self.rules.get("version"), "regime": regime.get("regime"),
                "risk_budget": regime.get("risk_budget"), "decision": "BASELINE", "decision_reasons": ["随机基线样本"],
                "killed_by": [], "score": None, "position_size_usd": 0.0, "price_at": s.price_usd,
                "liquidity_at": s.liquidity_usd, "fdv_at": s.fdv_usd, "mcap_at": s.market_cap_usd,
                "features": {"age_hours": s.age_hours, "liquidity_usd": s.liquidity_usd, "vol_h24": s.vol("h24"),
                             "chg_h1": s.chg("h1"), "chg_h24": s.chg("h24"), "buyers_h24": s.txns.get("h24", {}).get("buyers")},
                "flags": {}, "ai": None, "early_buyers": [], "status": "pending", "outcomes": {}, "last_checked": None,
                "url": s.url,
            })
            ids.add(sid)
            n += 1
        return n

    def new_positions_today(self) -> int:
        today = day_key()
        return sum(1 for p in self.positions["open"] + self.positions["closed"] if (p.get("opened_at") or "").startswith(today))

    def open_count(self) -> int:
        return len(self.positions["open"])

    def has_open(self, token: str) -> bool:
        return any(p.get("token") == token for p in self.positions["open"])

    # ---------------------------------------------------------------- positions
    def open_position(self, c: Candidate) -> Optional[dict]:
        s = c.snapshot
        if not s.price_usd or c.position_size_usd <= 0 or self.has_open(c.token):
            return None
        pos = {
            "id": f"{day_key()}:{c.token}", "token": c.token, "symbol": c.symbol, "pool": c.pool,
            "opened_at": iso(), "entry_price": s.price_usd, "size_usd": c.position_size_usd,
            "entry_liquidity": s.liquidity_usd, "remaining_fraction": 1.0, "realized_usd": 0.0,
            "peak_price": s.price_usd, "last_price": s.price_usd, "last_checked": iso(),
            "tp_hit": [], "exits": [], "status": "open", "score": c.score_total, "regime": (c.features or {}).get("regime"),
        }
        self.positions["open"].append(pos)
        return pos

    def _exit(self, pos: dict, price: float, fraction: float, reason: str, at: Optional[str] = None):
        fraction = min(fraction, pos["remaining_fraction"])
        if fraction <= 0:
            return
        if price <= 0:
            if reason == "RUG":          # 池子消失：剩余份额按 0 计
                pos["exits"].append({"at": at or iso(), "price": 0.0, "fraction": round(fraction, 4), "usd": 0.0, "reason": reason})
                pos["remaining_fraction"] = 0.0
                self._close(pos, reason)
            return
        usd = pos["size_usd"] * fraction * (price / pos["entry_price"])
        pos["exits"].append({"at": at or iso(), "price": price, "fraction": round(fraction, 4), "usd": round(usd, 4), "reason": reason})
        pos["realized_usd"] = round(pos["realized_usd"] + usd, 4)
        pos["remaining_fraction"] = round(pos["remaining_fraction"] - fraction, 4)
        if pos["remaining_fraction"] <= 1e-6:
            self._close(pos, reason)

    def _close(self, pos: dict, reason: str):
        acct = self.rules.get("experiment_account") or {}
        cost = float(acct.get("assumed_roundtrip_cost_pct", 6)) / 100.0
        pos["status"] = "closed"
        pos["close_reason"] = reason
        pos["closed_at"] = iso()
        pos["remaining_fraction"] = 0.0
        pnl = pos["realized_usd"] - pos["size_usd"] - pos["size_usd"] * cost
        pos["pnl_usd"] = round(pnl, 4)
        pos["pnl_pct"] = round(pnl / pos["size_usd"] * 100.0, 2) if pos["size_usd"] else 0.0
        pos["hold_hours"] = round(hours_between(pos["opened_at"], pos["closed_at"]) or 0.0, 2)

    def manage_positions(self, fetch: PriceFetcher) -> Dict[str, int]:
        acct = self.rules.get("experiment_account") or {}
        ladder = acct.get("take_profit_ladder") or []
        trail = float(acct.get("trailing_stop_from_peak_pct", 40)) / 100.0
        sl = float(acct.get("stop_loss_pct", 50)) / 100.0
        tstop_h = float(acct.get("time_stop_hours", 72))
        tstop_gain = float(acct.get("time_stop_min_gain_pct", 30)) / 100.0
        rug_drop = float(acct.get("rug_liquidity_drop_pct", 70)) / 100.0
        stats = {"checked": 0, "closed": 0, "errors": 0}
        still_open = []
        now = now_utc()
        for pos in self.positions["open"]:
            stats["checked"] += 1
            since = parse_iso(pos.get("last_checked") or pos["opened_at"])
            try:
                px = fetch(pos["pool"], pos["token"], int(since.timestamp()), int(now.timestamp()))
            except Exception:
                stats["errors"] += 1
                still_open.append(pos)
                continue
            entry = pos["entry_price"]
            candles = [c for c in (px.get("candles") or []) if c[0] >= int(since.timestamp())]
            price_now = px.get("price_now") or (candles[-1][4] if candles else pos["last_price"])
            liq_now = px.get("liq_now")
            # 流动性抽走 / 池子消失 → RUG
            if (px.get("pool_alive") is False) or (liq_now is not None and pos.get("entry_liquidity") and liq_now < pos["entry_liquidity"] * (1 - rug_drop)):
                self._exit(pos, float(price_now or 0.0) if px.get("pool_alive") is not False else 0.0, pos["remaining_fraction"], "RUG")
            else:
                for c in candles:
                    ts, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
                    at = iso(parse_iso(ts))
                    if pos["status"] != "open":
                        break
                    if l <= entry * (1 - sl) and not pos["tp_hit"]:
                        self._exit(pos, entry * (1 - sl), pos["remaining_fraction"], "STOP_LOSS", at)
                        break
                    for i, rung in enumerate(ladder):
                        if i in pos["tp_hit"]:
                            continue
                        if h >= entry * float(rung["at_multiple"]):
                            pos["tp_hit"].append(i)
                            self._exit(pos, entry * float(rung["at_multiple"]), float(rung["sell_fraction"]), f"TP{i + 1}", at)
                    if pos["status"] != "open":
                        break
                    pos["peak_price"] = max(pos["peak_price"], h)
                    if pos["tp_hit"] and cl <= pos["peak_price"] * (1 - trail):
                        self._exit(pos, cl, pos["remaining_fraction"], "TRAILING_STOP", at)
                        break
                    if l <= entry * (1 - sl):
                        self._exit(pos, entry * (1 - sl), pos["remaining_fraction"], "STOP_LOSS", at)
                        break
                if pos["status"] == "open" and price_now:
                    pos["last_price"] = float(price_now)
                    pos["peak_price"] = max(pos["peak_price"], float(price_now))
                    held_h = hours_between(pos["opened_at"], now) or 0.0
                    if held_h >= tstop_h and (float(price_now) / entry - 1) < tstop_gain:
                        self._exit(pos, float(price_now), pos["remaining_fraction"], "TIME_STOP")
            pos["last_checked"] = iso(now)
            if pos["status"] == "open":
                still_open.append(pos)
            else:
                stats["closed"] += 1
                self.positions["closed"].append(pos)
        self.positions["open"] = still_open
        return stats

    # ---------------------------------------------------------------- outcomes
    def update_outcomes(self, fetch: PriceFetcher, max_updates: int = 80) -> Dict[str, int]:
        oc = self.rules.get("outcomes") or {}
        rug_ret = float(oc.get("rug_return_pct", -80))
        acct = self.rules.get("experiment_account") or {}
        rug_drop = float(acct.get("rug_liquidity_drop_pct", 70)) / 100.0
        stats = {"checked": 0, "updated": 0, "completed": 0, "rugs": 0, "errors": 0}
        now = now_utc()
        due = []
        for s in self.samples:
            if s.get("status") == "complete":
                continue
            t0 = parse_iso(s.get("discovered_at"))
            if not t0 or not s.get("price_at"):
                continue
            pending = [h for h in self.horizons if f"h{h}" not in (s.get("outcomes") or {})]
            ready = [h for h in pending if (now - t0).total_seconds() >= h * 3600]
            if ready:
                due.append((s, t0, ready))
        due.sort(key=lambda x: x[1])
        for s, t0, ready in due[:max_updates]:
            stats["checked"] += 1
            until = int(t0.timestamp()) + max(ready) * 3600
            try:
                px = fetch(s["pool"], s["token"], int(t0.timestamp()), min(until, int(now.timestamp())))
            except Exception:
                stats["errors"] += 1
                continue
            p0 = float(s["price_at"])
            candles = [c for c in (px.get("candles") or []) if c[0] >= int(t0.timestamp()) - 3600]
            alive = px.get("pool_alive", True)
            liq_now = px.get("liq_now")
            out = s.setdefault("outcomes", {})
            for h in ready:
                t_end = int(t0.timestamp()) + h * 3600
                window = [c for c in candles if c[0] <= t_end]
                if window:
                    close = window[-1][4]
                    hi = max(c[2] for c in window)
                    lo = min(c[3] for c in window)
                    out[f"h{h}"] = {"ret_pct": round((close / p0 - 1) * 100, 2), "max_ret_pct": round((hi / p0 - 1) * 100, 2),
                                    "min_ret_pct": round((lo / p0 - 1) * 100, 2), "price": close, "src": "ohlcv"}
                elif px.get("price_now") and alive:
                    pn = float(px["price_now"])
                    out[f"h{h}"] = {"ret_pct": round((pn / p0 - 1) * 100, 2), "max_ret_pct": None, "min_ret_pct": None,
                                    "price": pn, "src": "spot"}
                elif alive is False:
                    out[f"h{h}"] = {"ret_pct": -100.0, "max_ret_pct": None, "min_ret_pct": None, "price": 0.0, "src": "dead"}
                else:
                    continue
                if liq_now is not None and s.get("liquidity_at"):
                    out[f"h{h}"]["liq_ret_pct"] = round((liq_now / float(s["liquidity_at"]) - 1) * 100, 2)
            s["last_checked"] = iso(now)
            stats["updated"] += 1
            last = out.get(f"h{max(ready)}") or {}
            liq_gone = liq_now is not None and s.get("liquidity_at") and liq_now < float(s["liquidity_at"]) * (1 - rug_drop)
            if alive is False or (last.get("ret_pct") is not None and last["ret_pct"] <= rug_ret and liq_gone):
                s["status"] = "rug"
                stats["rugs"] += 1
                for h in self.horizons:   # 归零后不再等更长周期
                    out.setdefault(f"h{h}", {"ret_pct": last.get("ret_pct", -100.0), "max_ret_pct": None, "min_ret_pct": None,
                                             "price": last.get("price", 0.0), "src": "rug_fill"})
            if all(f"h{h}" in out for h in self.horizons):
                s["status"] = "complete" if s.get("status") != "rug" else "rug"
                stats["completed"] += 1
        return stats

    # ---------------------------------------------------------------- summaries
    def portfolio_summary(self) -> Dict[str, Any]:
        acct = self.rules.get("experiment_account") or {}
        capital = float(acct.get("capital_usd", 500))
        closed = self.positions["closed"]
        open_ = self.positions["open"]
        realized = sum(float(p.get("pnl_usd", 0.0)) for p in closed)
        unreal = 0.0
        for p in open_:
            remaining = p["size_usd"] * p["remaining_fraction"] * (p["last_price"] / p["entry_price"])
            unreal += p["realized_usd"] + remaining - p["size_usd"]
        wins = [p for p in closed if float(p.get("pnl_usd", 0)) > 0]
        losses = [p for p in closed if float(p.get("pnl_usd", 0)) <= 0]
        gp = sum(float(p["pnl_usd"]) for p in wins)
        gl = -sum(float(p["pnl_usd"]) for p in losses)
        eq, peak, mdd = capital, capital, 0.0
        for p in sorted(closed, key=lambda x: x.get("closed_at") or ""):
            eq += float(p.get("pnl_usd", 0.0))
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100 if peak else 0.0)
        reasons: Dict[str, int] = {}
        for p in closed:
            reasons[p.get("close_reason", "?")] = reasons.get(p.get("close_reason", "?"), 0) + 1
        return {
            "capital_usd": capital, "open_positions": len(open_), "closed_positions": len(closed),
            "realized_pnl_usd": round(realized, 2), "unrealized_pnl_usd": round(unreal, 2),
            "equity_usd": round(capital + realized + unreal, 2),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "profit_factor": round(gp / gl, 2) if gl > 0 else (None if not wins else math.inf),
            "avg_pnl_pct": round(sum(float(p.get("pnl_pct", 0)) for p in closed) / len(closed), 2) if closed else None,
            "best_pct": max((float(p.get("pnl_pct", 0)) for p in closed), default=None),
            "worst_pct": min((float(p.get("pnl_pct", 0)) for p in closed), default=None),
            "max_drawdown_pct": round(mdd, 2), "close_reasons": reasons,
            "deployed_usd": round(sum(p["size_usd"] * p["remaining_fraction"] for p in open_), 2),
        }

    def sample_stats(self) -> Dict[str, Any]:
        by_dec: Dict[str, int] = {}
        complete = 0
        for s in self.samples:
            by_dec[s.get("decision", "?")] = by_dec.get(s.get("decision", "?"), 0) + 1
            if s.get("status") in ("complete", "rug"):
                complete += 1
        return {"total": len(self.samples), "by_decision": by_dec, "complete": complete}
