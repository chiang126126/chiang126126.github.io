# -*- coding: utf-8 -*-
"""smartmoney.py — 聪明钱库与共振检测。

不是机械跟一个"大户"，而是维护一批『在我们自己的样本里反复早期买到赢家』的钱包，
看它们是否共同开始买同一个新币。

来源：
  • auto   —— 结果回填时，赢家代币的最早买家 +1 win；亏损/归零代币的最早买家 +1 loss
  • manual —— data/smart_wallets.manual.json / .csv（可从 GMGN 等页面整理）
  • gmgn   —— 可选 API（见 sources/gmgn.py）
评分：score = (wins + 1) / (wins + losses + 2)（拉普拉斯平滑）；manual 给 0.65 先验。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .models import HolderInfo, SmartMoneySignal
from .util import DATA_DIR, iso, load_json, norm_addr, save_json


class SmartMoneyRegistry:
    def __init__(self, rules: Dict[str, Any], path=None):
        self.cfg = rules.get("smart_money") or {}
        self.path = path or (DATA_DIR / "smart_wallets.json")
        raw = load_json(self.path, {}) or {}
        self.w: Dict[str, dict] = raw.get("wallets", {}) if isinstance(raw, dict) else {}
        self.dirty = False

    # ---------------------------------------------------------------- persistence
    def save(self):
        if not self.dirty:
            return
        save_json(self.path, {"updated": iso(), "count": len(self.w), "wallets": self.w})
        self.dirty = False

    def __len__(self):
        return len(self.w)

    # ---------------------------------------------------------------- updates
    def merge_external(self, rows: Iterable[dict], source: str = "manual") -> int:
        n = 0
        for r in rows:
            a = norm_addr(r.get("address") or "")
            if not a:
                continue
            e = self.w.setdefault(a, {"label": "", "source": source, "wins": 0, "losses": 0, "tokens": [],
                                      "first_seen": iso(), "manual_score": None})
            e["label"] = r.get("label") or e.get("label") or source
            e["source"] = source if e.get("source") == "auto" else e.get("source", source)
            if r.get("score") is not None:
                e["manual_score"] = float(r["score"])
            n += 1
        self.dirty = self.dirty or n > 0
        return n

    def record_outcome(self, wallets: Iterable[str], token: str, symbol: str, result: str) -> int:
        """result ∈ {win, loss}。同一代币只记一次。"""
        n = 0
        key = f"{token}:{result}"
        for a in wallets:
            a = norm_addr(a)
            if not a:
                continue
            e = self.w.setdefault(a, {"label": "", "source": "auto", "wins": 0, "losses": 0, "tokens": [],
                                      "first_seen": iso(), "manual_score": None})
            if key in e["tokens"]:
                continue
            e["tokens"].append(key)
            e["tokens"] = e["tokens"][-50:]
            if result == "win":
                e["wins"] = int(e.get("wins", 0)) + 1
            else:
                e["losses"] = int(e.get("losses", 0)) + 1
            e["last_hit"] = iso()
            e["label"] = e.get("label") or f"auto:{symbol}"
            n += 1
        self.dirty = self.dirty or n > 0
        return n

    # ---------------------------------------------------------------- scoring
    def score(self, addr: str) -> float:
        e = self.w.get(norm_addr(addr))
        if not e:
            return 0.0
        if e.get("manual_score") is not None:
            return float(e["manual_score"])
        wins, losses = int(e.get("wins", 0)), int(e.get("losses", 0))
        if wins + losses == 0:
            return 0.65 if e.get("source") in ("manual", "gmgn") else 0.0
        return (wins + 1) / (wins + losses + 2)

    def is_smart(self, addr: str) -> bool:
        e = self.w.get(norm_addr(addr))
        if not e:
            return False
        if e.get("source") in ("manual", "gmgn") and int(e.get("losses", 0)) <= int(e.get("wins", 0)) + 1:
            return self.score(addr) >= float(self.cfg.get("min_score", 0.6))
        return int(e.get("wins", 0)) >= int(self.cfg.get("min_wins", 2)) and self.score(addr) >= float(self.cfg.get("min_score", 0.6))

    def smart_set(self) -> Dict[str, float]:
        return {a: self.score(a) for a in self.w if self.is_smart(a)}

    # ---------------------------------------------------------------- signal
    def signal(self, trades: List[dict], holders: Optional[List[HolderInfo]] = None) -> SmartMoneySignal:
        """trades: GeckoTerminal 最近成交（wallet/kind/volume_usd）；holders: 前排持有人。"""
        smart = self.smart_set()
        sig = SmartMoneySignal(registry_size=len(smart))
        if not smart:
            return sig
        flows: Dict[str, Dict[str, float]] = {}
        for t in trades or []:
            w = norm_addr(t.get("wallet") or "")
            if w in smart:
                f = flows.setdefault(w, {"buy": 0.0, "sell": 0.0})
                f["buy" if t.get("kind") == "buy" else "sell"] += float(t.get("volume_usd") or 0.0)
        held = {}
        for h in holders or []:
            if h.address in smart and h.kind in ("eoa", "creator"):
                held[h.address] = h.pct_supply
        wallets = set(flows) | set(held)
        for w in wallets:
            f = flows.get(w, {"buy": 0.0, "sell": 0.0})
            net = f["buy"] - f["sell"]
            side = "buy" if net > 0 else ("sell" if net < 0 else "hold")
            entry = {"address": w, "score": round(smart[w], 3), "label": (self.w.get(w) or {}).get("label", ""),
                     "net_usd": round(net, 2), "holding_pct": held.get(w), "side": side}
            sig.wallets.append(entry)
            if side != "sell":
                sig.count += 1
                sig.weighted += smart[w]
            sig.net_buy_usd += net
        sig.wallets.sort(key=lambda e: -abs(e["net_usd"]))
        sig.weighted = round(sig.weighted, 3)
        sig.net_buy_usd = round(sig.net_buy_usd, 2)
        return sig
