# -*- coding: utf-8 -*-
"""evaluate.py — 回答唯一重要的问题：AI 筛出来的项目是否明显优于随机选择？

对照组：
  SELECTED = WATCH + PAPER_BUY（系统看好）
  BUY      = PAPER_BUY
  BASELINE = 每轮随机抽的样本
  SKIP     = 被硬过滤/低分剔除但仍记录的样本（检验"我们有没有错杀赢家"）
指标（主周期 24h，次周期 7d）：中位收益、命中率（≥ +50%）、归零率（≤ -80% 或 rug）、平均最大涨幅。
命中率差异用 bootstrap 给置信区间；两组都 ≥ min_samples 之前一律标 insufficient。
另外按特征分桶（sybil、聪明钱、体制、发射台、评分段…）看哪些指标真的有效。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from .util import median, safe_float

GROUPS = {
    "SELECTED": lambda s: s.get("decision") in ("WATCH", "PAPER_BUY"),
    "BUY": lambda s: s.get("decision") == "PAPER_BUY",
    "BASELINE": lambda s: s.get("decision") == "BASELINE",
    "SKIP": lambda s: s.get("decision") == "SKIP",
}


def _ret(s: dict, h: str) -> Optional[float]:
    o = (s.get("outcomes") or {}).get(h) or {}
    return safe_float(o.get("ret_pct"))


def _maxret(s: dict, h: str) -> Optional[float]:
    o = (s.get("outcomes") or {}).get(h) or {}
    return safe_float(o.get("max_ret_pct"))


def group_stats(samples: List[dict], h: str, hit: float, rug: float) -> Dict[str, Any]:
    rets = [(_ret(s, h), _maxret(s, h), s.get("status") == "rug") for s in samples if _ret(s, h) is not None]
    n = len(rets)
    if n == 0:
        return {"n": 0}
    r = [x[0] for x in rets]
    mx = [x[1] for x in rets if x[1] is not None]
    return {
        "n": n,
        "median_ret_pct": round(median(r), 2),
        "mean_ret_pct": round(sum(r) / n, 2),
        "hit_rate": round(sum(1 for x in r if x >= hit) / n, 3),
        "rug_rate": round(sum(1 for x in rets if x[2] or x[0] <= rug) / n, 3),
        "positive_rate": round(sum(1 for x in r if x > 0) / n, 3),
        "mean_max_ret_pct": round(sum(mx) / len(mx), 2) if mx else None,
        "p90_ret_pct": round(sorted(r)[int(0.9 * (n - 1))], 2),
    }


def bootstrap_diff(a: List[float], b: List[float], hit: float, n_boot: int = 1000, seed: int = 7) -> Optional[Dict[str, float]]:
    if not a or not b:
        return None
    rnd = random.Random(seed)
    ha = [1.0 if x >= hit else 0.0 for x in a]
    hb = [1.0 if x >= hit else 0.0 for x in b]
    diffs = []
    for _ in range(n_boot):
        sa = [ha[rnd.randrange(len(ha))] for _ in ha]
        sb = [hb[rnd.randrange(len(hb))] for _ in hb]
        diffs.append(sum(sa) / len(sa) - sum(sb) / len(sb))
    diffs.sort()
    return {"diff": round(sum(ha) / len(ha) - sum(hb) / len(hb), 3),
            "ci_low": round(diffs[int(0.025 * n_boot)], 3), "ci_high": round(diffs[int(0.975 * n_boot) - 1], 3)}


def _bucket(v: Any, edges: List[float], labels: List[str]) -> str:
    x = safe_float(v)
    if x is None:
        return "unknown"
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]


FEATURE_BUCKETS = {
    "sybil_score": lambda f: _bucket(f.get("sybil_score"), [0.2, 0.5], ["<0.2", "0.2-0.5", ">=0.5"]),
    "smart_count": lambda f: _bucket(f.get("smart_count"), [1, 2], ["0", "1", ">=2"]),
    "score": lambda f: _bucket(f.get("score_total"), [60, 72], ["<60", "60-72", ">=72"]),
    "launchpad": lambda f: (f.get("launchpad") or "other"),
    "forensics_quality": lambda f: (f.get("forensics_quality") or "none"),
    "liquidity": lambda f: _bucket(f.get("liquidity_usd"), [25_000, 100_000], ["<25k", "25k-100k", ">=100k"]),
    "age_hours": lambda f: _bucket(f.get("age_hours"), [2, 12], ["<2h", "2-12h", ">=12h"]),
    "chg_h1": lambda f: _bucket(f.get("chg_h1"), [0, 50], ["<0", "0-50", ">=50"]),
    "has_socials": lambda f: str(bool(f.get("has_socials"))),
    "top10_eoa_pct": lambda f: _bucket(f.get("top10_eoa_pct"), [20, 35], ["<20", "20-35", ">=35"]),
    "fresh_wallet_pct": lambda f: _bucket(f.get("fresh_wallet_pct"), [5, 15], ["<5", "5-15", ">=15"]),
    "x_top5_buyer_share": lambda f: _bucket(f.get("x_top5_buyer_share"), [0.4, 0.7], ["<0.4", "0.4-0.7", ">=0.7"]),
}


def evaluate(samples: List[dict], rules: Dict[str, Any]) -> Dict[str, Any]:
    oc = rules.get("outcomes") or {}
    hit, rug = float(oc.get("hit_return_pct", 50)), float(oc.get("rug_return_pct", -80))
    min_n = int(oc.get("min_samples_for_verdict", 50))
    n_boot = int(oc.get("bootstrap_resamples", 1000))
    out: Dict[str, Any] = {"horizons": {}, "verdict": "insufficient", "min_samples": min_n, "feature_buckets": {},
                           "by_rules_version": {}, "by_regime": {}}
    for h in ("h24", "h168", "h6", "h1"):
        hs = {g: group_stats([s for s in samples if fn(s)], h, hit, rug) for g, fn in GROUPS.items()}
        sel = [_ret(s, h) for s in samples if GROUPS["SELECTED"](s) and _ret(s, h) is not None]
        base = [_ret(s, h) for s in samples if GROUPS["BASELINE"](s) and _ret(s, h) is not None]
        hs["selected_vs_baseline"] = bootstrap_diff(sel, base, hit, n_boot)
        out["horizons"][h] = hs
    h24 = out["horizons"]["h24"]
    ns, nb = h24["SELECTED"].get("n", 0), h24["BASELINE"].get("n", 0)
    if ns >= min_n and nb >= min_n and h24.get("selected_vs_baseline"):
        d = h24["selected_vs_baseline"]
        if d["ci_low"] > 0:
            out["verdict"] = "edge"
        elif d["ci_high"] < 0:
            out["verdict"] = "no_edge"
        else:
            out["verdict"] = "unclear"
    out["progress"] = {"selected": ns, "baseline": nb, "needed": min_n}

    for name, fn in FEATURE_BUCKETS.items():
        buckets: Dict[str, List[dict]] = {}
        for s in samples:
            if s.get("decision") == "BASELINE":
                continue
            buckets.setdefault(fn(s.get("features") or {}), []).append(s)
        out["feature_buckets"][name] = {b: group_stats(v, "h24", hit, rug) for b, v in sorted(buckets.items())}

    for key, field in (("by_rules_version", "rules_version"), ("by_regime", "regime")):
        groups: Dict[str, List[dict]] = {}
        for s in samples:
            if GROUPS["SELECTED"](s):
                groups.setdefault(str(s.get(field) or "?"), []).append(s)
        out[key] = {k: group_stats(v, "h24", hit, rug) for k, v in sorted(groups.items())}
    return out
