# -*- coding: utf-8 -*-
"""pipeline.py — 把五层串起来：regime → outcomes → scan → evaluate → report。

设计原则（与仓库里其它数据管道一致）：逐源 try/except、任何失败保留旧值、绝不非零退出、
确定性输出（sort_keys）。所有外部依赖都可通过 http_overrides 注入 FakeHttp 做离线测试。
"""
from __future__ import annotations

import math
import random
import time
import traceback
from typing import Any, Dict, List, Optional, Set

from .ai import AiJudge, evidence_document
from .config import ENV, load_chain, load_rules
from .crossval import cross_validate
from .evaluate import evaluate
from .forensics import Forensics, WalletCache
from .ledger import Ledger
from .models import Candidate, PoolSnapshot
from .regime import compact_history_entry, compute_regime
from .report import write_daily_report, write_outputs
from .screen import build_features, decide, hard_filter, score
from .smartmoney import SmartMoneyRegistry
from .sources.blockscout import Blockscout
from .sources.dexscreener import DexScreener
from .sources.evm_rpc import EvmRpc
from .sources.geckoterminal import GeckoTerminal
from .sources.gmgn import Gmgn, import_manual
from .sources.http import HttpClient, HttpError
from .sources.market import MarketData
from .sources.pons import Pons
from .sources.security import Security
from .util import (DATA_DIR, day_key, iso, load_json, now_utc, parse_iso, read_jsonl, save_json, write_jsonl)


class Pipeline:
    def __init__(self, rules: Optional[Dict[str, Any]] = None, chain: str = "robinhood", env=ENV,
                 http_overrides: Optional[Dict[str, Any]] = None, verbose: bool = False,
                 max_forensics: Optional[int] = None):
        self.rules = rules or load_rules()
        self.chain_cfg = load_chain(chain)
        self.env = env
        self.verbose = verbose
        self.errors: List[str] = []
        ov = http_overrides or {}

        def H(name: str, rps: float, **kw):
            return ov.get(name) or HttpClient(name, rps=rps, **kw)

        bs_key = env.blockscout_api_key
        self.h = {
            "geckoterminal": H("geckoterminal", 0.45), "dexscreener": H("dexscreener", 4.0),
            "blockscout": H("blockscout", 4.5 if bs_key else 1.5), "rpc": H("rpc", 5.0),
            "okx": H("okx", 2.0), "coinbase": H("coinbase", 2.0), "coingecko": H("coingecko", 0.4), "fng": H("fng", 1.0),
            "goplus": H("goplus", 1.0), "llm": H("llm", 1.0, timeout=90), "gmgn": H("gmgn", 1.0),
        }
        sc = self.chain_cfg.get("screeners") or {}
        self.gt = GeckoTerminal(self.h["geckoterminal"], sc.get("geckoterminal_network", "robinhood"))
        self.ds = DexScreener(self.h["dexscreener"], sc.get("dexscreener_chain", "robinhood"))
        self.bs = Blockscout(self.h["blockscout"], self.chain_cfg, bs_key)
        self.rpc = EvmRpc(self.h["rpc"], env.rpc_url or (self.chain_cfg.get("rpc") or {}).get("http", ""))
        self.pons = Pons(self.chain_cfg)
        self.security = Security(self.h["goplus"], self.bs, self.rpc, int(self.chain_cfg.get("chain_id", 0)), self.pons)
        self.market = MarketData(self.h["okx"], self.h["coinbase"], self.h["coingecko"], self.h["fng"], env.coingecko_api_key)
        self.forensics = Forensics(self.bs, self.pons, self.chain_cfg, self.rules,
                                   WalletCache(max_entries=int((self.rules.get("forensics") or {}).get("wallet_cache_max_entries", 6000))),
                                   self.rpc)
        self.registry = SmartMoneyRegistry(self.rules)
        self.ledger = Ledger(self.rules)
        self.ai = AiJudge(env, self.h["llm"], int((self.rules.get("decision") or {}).get("max_ai_judgements_per_run", 8)))
        self.gmgn = Gmgn(self.h["gmgn"], env.gmgn_api_key, env.gmgn_base_url, sc.get("geckoterminal_network", "robinhood"))
        self.max_forensics = max_forensics if max_forensics is not None else int((self.rules.get("universe") or {}).get("max_candidates_for_forensics", 30))
        self.quotes = {q.upper() for q in ((self.rules.get("universe") or {}).get("quote_tokens_allowed") or [])}
        self.prev_path = DATA_DIR / "prev_snapshots.json"
        self.prev: Dict[str, dict] = (load_json(self.prev_path, {}) or {}).get("tokens", {})

    # ---------------------------------------------------------------- utils
    def log(self, msg: str):
        print(f"[{iso()}] {msg}", flush=True)

    def fail(self, stage: str, e: Exception):
        self.errors.append(f"{stage}: {type(e).__name__}: {str(e)[:200]}")
        self.log(f"!! {stage} 失败: {type(e).__name__}: {str(e)[:200]}")
        if self.verbose:
            traceback.print_exc()

    def http_stats(self) -> Dict[str, Any]:
        return {k: dict(getattr(v, "stats", {}) or {}) for k, v in self.h.items()}

    # ---------------------------------------------------------------- 第一层：体制
    def chain_activity(self) -> Dict[str, Any]:
        vol = 0.0
        n = 0
        for page in (1, 2, 3):
            try:
                pools = self.gt.top_pools(page)
            except Exception:
                break
            if not pools:
                break
            vol += sum(p.vol("h24") for p in pools)
            n += len(pools)
        new24 = None
        try:
            new24 = sum(1 for p in self.gt.new_pools(1) if p.age_hours is not None and p.age_hours <= 24)
        except Exception:
            pass
        return {"top_pools_vol_24h": round(vol, 0) if n else None, "top_pools_counted": n, "new_pools_24h_est": new24}

    def run_regime(self) -> Dict[str, Any]:
        hist_path = DATA_DIR / "regime_history.jsonl"
        history = list(read_jsonl(hist_path))
        try:
            activity = self.chain_activity()
        except Exception as e:
            self.fail("chain_activity", e)
            activity = {}
        try:
            regime = compute_regime(self.market, self.rules, history, activity)
        except Exception as e:
            self.fail("regime", e)
            regime = load_json(DATA_DIR / "regime.json", {}) or {"regime": "BTC_ONLY", "risk_budget": 0.0,
                                                                   "max_new_positions": 0, "judgment": "regime 计算失败，保守处理"}
            regime["stale"] = True
            return regime
        save_json(DATA_DIR / "regime.json", regime)
        history.append(compact_history_entry(regime))
        write_jsonl(hist_path, history[-3000:])
        self.log(f"regime: {regime['judgment']}")
        return regime

    # ---------------------------------------------------------------- 第二层：发现 + 初筛
    def discover(self) -> List[PoolSnapshot]:
        uni = self.rules.get("universe") or {}
        seen: Dict[str, PoolSnapshot] = {}
        n_raw = 0
        for page in range(1, int(uni.get("new_pools_pages", 5)) + 1):
            try:
                pools = self.gt.new_pools(page)
            except Exception as e:
                self.fail(f"new_pools p{page}", e)
                break
            if not pools:
                break
            n_raw += len(pools)
            for p in pools:
                self._consider(seen, p)
            if pools[-1].age_hours is not None and pools[-1].age_hours > float(uni.get("max_age_hours", 72)):
                break
        for d in uni.get("trending_durations") or ["1h"]:
            try:
                for p in self.gt.trending_pools(d):
                    n_raw += 1
                    self._consider(seen, p)
            except Exception as e:
                self.fail(f"trending {d}", e)
        self.universe_raw = n_raw
        return list(seen.values())

    def _consider(self, seen: Dict[str, PoolSnapshot], p: PoolSnapshot):
        if not p.base_token or not p.pool_address:
            return
        if p.base_symbol.upper() in self.quotes or p.base_symbol.upper() in ("WETH", "USDC", "USDT", "ETH", "DAI"):
            return
        if self.quotes and p.quote_symbol and p.quote_symbol.upper() not in self.quotes:
            return
        cur = seen.get(p.base_token)
        if cur is None or (p.liquidity_usd or 0) > (cur.liquidity_usd or 0):
            seen[p.base_token] = p

    # ---------------------------------------------------------------- 第三/四层：单币深挖
    def enrich(self, snap: PoolSnapshot, regime: Dict[str, Any], do_forensics: bool) -> Candidate:
        token, pool = snap.base_token, snap.pool_address
        cand = Candidate(token=token, symbol=snap.base_symbol, name=snap.base_name, pool=pool, snapshot=snap)
        addr_info = {}
        try:
            addr_info = self.bs.address(token)
        except Exception:
            pass
        try:
            cand.security = self.security.check(token, addr_info or None)
        except Exception as e:
            self.fail(f"security {snap.base_symbol}", e)
        holders = None
        if do_forensics:
            try:
                cand.forensics = self.forensics.analyze(token, {pool}, creator_hint=addr_info.get("creator", ""))
                holders = self.forensics.last_holders
            except Exception as e:
                self.fail(f"forensics {snap.base_symbol}", e)
        trades: List[dict] = []
        try:
            trades = self.gt.trades(pool)
        except Exception:
            pass
        try:
            cand.smart_money = self.registry.signal(trades, holders)
        except Exception as e:
            self.fail(f"smartmoney {snap.base_symbol}", e)
        cross = cross_validate(snap, trades, cand.forensics, self.prev.get(token))
        cand.flags = cross["flags"]
        if cand.security:
            if cand.security.is_honeypot:
                cand.flags["red"].append("HONEYPOT")
            for f in ("has_owner", "unverified_contract", "mintable", "closed_source", "hidden_owner"):
                if f in cand.security.flags:
                    cand.flags["yellow"].append(f.upper())
            if "pons_template" in cand.security.flags:
                cand.flags["green"].append("PONS_TEMPLATE")
        cand.killed_by = hard_filter(snap, cand.security, cand.forensics, self.rules)
        cand.score_total, cand.score_breakdown = score(snap, cand.security, cand.forensics, cand.smart_money, cross, self.rules)
        cand.features = build_features(cand, cross)
        cand.features["regime"] = regime.get("regime")
        cand.features["risk_budget"] = regime.get("risk_budget")
        cand.features["forensics_done"] = bool(do_forensics)
        cand._cross_metrics = cross.get("metrics")  # type: ignore[attr-defined]
        return cand

    def _decide(self, cand: Candidate, regime: Dict[str, Any]):
        dc = self.rules.get("decision") or {}
        cand.decision, cand.decision_reasons, cand.position_size_usd = decide(
            cand, regime, self.rules, self.ledger.new_positions_today(), self.ledger.open_count())
        if cand.decision in ("WATCH", "PAPER_BUY") and cand.score_total >= float(dc.get("watch_threshold", 60)):
            try:
                cand.ai = self.ai.judge(cand, regime, getattr(cand, "_cross_metrics", None))
            except Exception as e:
                self.fail(f"ai {cand.symbol}", e)
            if cand.ai:
                cand.features["ai_verdict"], cand.features["ai_confidence"], cand.features["ai_provider"] = cand.ai.verdict, cand.ai.confidence, cand.ai.provider
                if cand.decision == "PAPER_BUY":
                    cand.decision, cand.decision_reasons, cand.position_size_usd = decide(
                        cand, regime, self.rules, self.ledger.new_positions_today(), self.ledger.open_count())
        if cand.decision == "PAPER_BUY":
            pos = self.ledger.open_position(cand)
            if pos:
                cand.decision_reasons.append(f"已开模拟仓 #{pos['id']}")
            else:
                cand.decision = "WATCH"
                cand.decision_reasons.append("已有同币持仓/价格缺失，转 WATCH")

    def run_scan(self, regime: Dict[str, Any]) -> Dict[str, Any]:
        uni = self.rules.get("universe") or {}
        t0 = time.time()
        snaps = self.discover()
        self.log(f"discover: 原始 {getattr(self, 'universe_raw', 0)} 条 → 去重 {len(snaps)} 个代币")
        pre = [(s, hard_filter(s, None, None, self.rules)) for s in snaps]
        passed = [s for s, k in pre if not k]
        failed = [(s, k) for s, k in pre if k]
        # 便宜的排序启发：流动性 × log(24h 独立买家)
        passed.sort(key=lambda s: -(s.liquidity_usd or 0) * math.log1p(s.tx("h24", "buyers") or s.tx("h24", "buys")))
        deep, shallow = passed[:self.max_forensics], passed[self.max_forensics:]
        self.log(f"prefilter: 通过 {len(passed)}（深挖 {len(deep)}，浅扫 {len(shallow)}），剔除 {len(failed)}")

        candidates: List[Candidate] = []
        for s in deep:
            try:
                c = self.enrich(s, regime, True)
                self._decide(c, regime)
                candidates.append(c)
                if self.verbose:
                    self.log(f"  {c.decision:9s} {c.symbol:12s} score {c.score_total:5.1f} liq ${(s.liquidity_usd or 0):,.0f} sybil {c.forensics.sybil_score if c.forensics else '-'} kill {c.killed_by}")
            except Exception as e:
                self.fail(f"enrich {s.base_symbol}", e)
        for s in shallow:
            try:
                c = self.enrich(s, regime, False)
                self._decide(c, regime)
                candidates.append(c)
            except Exception as e:
                self.fail(f"enrich-shallow {s.base_symbol}", e)
        # 被剔除但仍值得跟踪结果的样本（检验有没有错杀）
        min_liq = float(uni.get("baseline_min_liquidity_usd", 3000))
        skip_cands = []
        for s, kills in failed:
            if (s.liquidity_usd or 0) >= min_liq and s.price_usd and len(skip_cands) < int(uni.get("skip_samples_per_run", 8)):
                c = Candidate(token=s.base_token, symbol=s.base_symbol, name=s.base_name, pool=s.pool_address, snapshot=s)
                c.killed_by = kills
                c.decision, c.decision_reasons = "SKIP", [f"硬过滤: {', '.join(kills)}"]
                c.features = build_features(c, {})
                c.features["regime"] = regime.get("regime")
                skip_cands.append(c)
        # 随机基线：对整个宇宙无偏随机抽样（含被选中的代币），代表『随机选』会得到什么
        pool_for_baseline = [s for s in snaps if (s.liquidity_usd or 0) >= min_liq and s.price_usd]
        rnd = random.Random(day_key())
        baseline = rnd.sample(pool_for_baseline, min(len(pool_for_baseline), int(uni.get("baseline_sample_per_run", 4))))

        n_new = self.ledger.add_samples(candidates + skip_cands, regime)
        n_base = self.ledger.add_baseline(baseline, regime)
        self.ledger.save()
        self.forensics.cache.save()
        self.registry.save()
        for s in snaps:
            self.prev[s.base_token] = {"liquidity_usd": s.liquidity_usd, "price_usd": s.price_usd, "observed_at": s.observed_at}
        cutoff = now_utc().timestamp() - 7 * 86400
        self.prev = {k: v for k, v in self.prev.items() if (parse_iso(v.get("observed_at")) or now_utc()).timestamp() >= cutoff}
        save_json(self.prev_path, {"updated": iso(), "tokens": self.prev})

        universe = {"discovered": len(snaps), "raw_rows": getattr(self, "universe_raw", 0), "prefiltered": len(passed),
                    "forensics_done": sum(1 for c in candidates if c.forensics and c.forensics.quality != "none"),
                    "skipped": len(failed), "new_samples": n_new, "new_baseline": n_base,
                    "seconds": round(time.time() - t0, 1)}
        self.log(f"scan 完成: {universe}")
        return {"candidates": candidates, "skip_candidates": skip_cands, "baseline": baseline, "universe": universe}

    # ---------------------------------------------------------------- 第五层：结果回填
    def price_fetcher(self, pool: str, token: str, since: int, until: int) -> Dict[str, Any]:
        hours = int(math.ceil((until - since) / 3600.0)) + 2
        out: Dict[str, Any] = {"candles": [], "price_now": None, "liq_now": None, "pool_alive": None}
        try:
            out["candles"] = self.gt.ohlcv(pool, "hour", 1, min(1000, max(hours, 2)), before_timestamp=until + 3600)
        except HttpError as e:
            if e.status == 404:
                out["pool_alive"] = False
        except Exception:
            pass
        try:
            snap = self.gt.pool(pool)
            if snap:
                out["price_now"], out["liq_now"], out["pool_alive"] = snap.price_usd, snap.liquidity_usd, True
        except HttpError as e:
            if e.status == 404:
                out["pool_alive"] = False
        except Exception:
            pass
        if out["pool_alive"] is None and not out["candles"]:
            try:
                pair = self.ds.pair(pool)
                if pair:
                    out["price_now"], out["liq_now"], out["pool_alive"] = pair.price_usd, pair.liquidity_usd, True
            except Exception:
                pass
        if out["pool_alive"] is None and not out["candles"] and out["price_now"] is None:
            raise RuntimeError("no price data")
        return out

    def run_outcomes(self) -> Dict[str, Any]:
        sm = self.rules.get("smart_money") or {}
        stats = {"positions": {}, "outcomes": {}, "smart_money_updates": 0}
        try:
            stats["positions"] = self.ledger.manage_positions(self.price_fetcher)
        except Exception as e:
            self.fail("positions", e)
        try:
            stats["outcomes"] = self.ledger.update_outcomes(self.price_fetcher)
        except Exception as e:
            self.fail("outcomes", e)
        # 赢家/输家的最早买家 → 聪明钱库
        for s in self.ledger.samples:
            if s.get("sm_recorded") or s.get("status") not in ("complete", "rug") or not s.get("early_buyers"):
                continue
            o = s.get("outcomes") or {}
            r24 = (o.get("h24") or {}).get("ret_pct")
            r168 = (o.get("h168") or {}).get("ret_pct")
            result = None
            if (r24 is not None and r24 >= float(sm.get("winner_24h_return_pct", 100))) or (r168 is not None and r168 >= float(sm.get("winner_7d_return_pct", 200))):
                result = "win"
            elif s.get("status") == "rug" or (r24 is not None and r24 <= float(sm.get("loser_24h_return_pct", -70))):
                result = "loss"
            if result:
                stats["smart_money_updates"] += self.registry.record_outcome(s["early_buyers"], s["token"], s.get("symbol", ""), result)
            s["sm_recorded"] = True
        self.registry.save()
        self.ledger.save()
        self.log(f"outcomes: {stats}")
        return stats

    def run_evaluate(self) -> Dict[str, Any]:
        ev = evaluate(self.ledger.samples, self.rules)
        ev["updated"] = iso()
        save_json(DATA_DIR / "evaluation.json", ev)
        self.log(f"evaluate: verdict={ev.get('verdict')} progress={ev.get('progress')}")
        return ev

    def import_wallets(self, path) -> int:
        rows = import_manual(path)
        n = self.registry.merge_external(rows, "manual")
        if self.gmgn.enabled:
            n += self.registry.merge_external(self.gmgn.smart_wallets(), "gmgn")
        self.registry.save()
        return n

    # ---------------------------------------------------------------- 全流程
    def cycle(self, stages: Optional[Set[str]] = None) -> Dict[str, Any]:
        stages = stages or {"regime", "outcomes", "scan", "evaluate", "report"}
        t0 = time.time()
        regime = self.run_regime() if "regime" in stages else (load_json(DATA_DIR / "regime.json", {}) or {"regime": "BTC_ONLY", "risk_budget": 0.0, "max_new_positions": 0})
        if "outcomes" in stages:
            self.run_outcomes()
        scan = {"candidates": [], "skip_candidates": [], "baseline": [], "universe": {}}
        if "scan" in stages:
            try:
                scan = self.run_scan(regime)
            except Exception as e:
                self.fail("scan", e)
        evaluation = self.run_evaluate() if "evaluate" in stages else (load_json(DATA_DIR / "evaluation.json", {}) or {})
        summary = {}
        if "report" in stages:
            run_meta = {"at": iso(), "seconds": round(time.time() - t0, 1), "errors": self.errors, "http": self.http_stats(),
                        "ai_enabled": self.ai.enabled, "ai_calls": self.ai.calls, "blockscout_pro": bool(self.env.blockscout_api_key),
                        "rules_version": self.rules.get("version"), "smart_wallets": len(self.registry),
                        "stages": sorted(stages)}
            try:
                cands = (scan["candidates"] + scan["skip_candidates"]) if "scan" in stages else None
                summary = write_outputs(regime, cands, scan["universe"], self.ledger, evaluation, run_meta,
                                        [b.base_symbol for b in scan["baseline"]])
                path = write_daily_report(summary)
                self.log(f"report: {path}")
            except Exception as e:
                self.fail("report", e)
        self.log(f"cycle 完成，用时 {time.time() - t0:.1f}s，错误 {len(self.errors)}")
        return {"regime": regime, "scan": scan, "evaluation": evaluation, "summary": summary, "errors": self.errors}

    def evidence_for(self, token: str) -> str:
        data = load_json(DATA_DIR / "candidates.json", {}) or {}
        regime = load_json(DATA_DIR / "regime.json", {}) or {}
        for it in data.get("items") or []:
            if it.get("token") == token.lower():
                import json as _json
                return "# Evidence（来自 candidates.json 快照）\n" + _json.dumps({"regime": regime.get("judgment"), **it}, ensure_ascii=False, indent=2)
        return "未在最近一轮 candidates.json 里找到该代币。"
