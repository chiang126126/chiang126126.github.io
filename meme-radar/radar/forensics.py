# -*- coding: utf-8 -*-
"""forensics.py — 第三层核心：钱包关联取证（"K 线是最晚出现的信息"）。

问题：一个币看起来有十几个"大户"在买，但这些钱包可能同一天创建、资金来自同一个上游地址，
本质上是项目方一个人控制十几个钱包，制造出很多人抢筹的假象。

做法：拉前 N 个持有人 → 剔除池子/曲线/锁仓/销毁地址 → 对每个 EOA 拉『最早交易』（钱包年龄 + 首个打款方）
和『交易计数』→ 用并查集把满足以下任一条件的钱包连成簇：
  • 同一个（非中性）首个打款方
  • 打款方本身就是另一个前排持有人
  • 两者之间有本代币的直接转账
  • 同一批次创建（首笔交易时间彼此相差 < 15 分钟）且都是几乎没历史的新钱包（批量 ≥ 3 才算）
输出 sybil_score（0 健康 → 1 几乎肯定一人多号）以及可解释的簇列表。

所有外部调用 best-effort；拿不到就降级为 partial / none，并在 notes 里说明。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .models import ForensicsResult, HolderInfo, WalletProfile
from .util import DATA_DIR, clamp, hours_between, iso, load_json, norm_addr, now_utc, parse_iso, save_json


class UnionFind:
    def __init__(self):
        self.p: Dict[str, str] = {}
        self.reason: Dict[str, Set[str]] = {}

    def add(self, x: str):
        self.p.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str, reason: str):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra
        root = self.find(a)
        self.reason.setdefault(root, set()).update(self.reason.pop(rb, set()) if rb != root else set())
        self.reason.setdefault(root, set()).add(reason)

    def groups(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for x in list(self.p):
            out.setdefault(self.find(x), []).append(x)
        return out


class WalletCache:
    """钱包画像缓存（年龄/打款方不会变）。提交进仓库，跨 Actions 运行复用，按条数 LRU 淘汰。"""

    def __init__(self, path=None, max_entries: int = 6000):
        self.path = path or (DATA_DIR / "wallet_cache.json")
        self.max_entries = max_entries
        raw = load_json(self.path, {}) or {}
        self.d: Dict[str, dict] = raw.get("wallets", {}) if isinstance(raw, dict) else {}
        self.dirty = False

    def get(self, addr: str) -> Optional[WalletProfile]:
        r = self.d.get(norm_addr(addr))
        if not r:
            return None
        try:
            return WalletProfile(**{k: r.get(k) for k in WalletProfile.__dataclass_fields__ if k in r})
        except TypeError:
            return None

    def put(self, p: WalletProfile):
        self.d[norm_addr(p.address)] = p.to_dict()
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        if len(self.d) > self.max_entries:
            items = sorted(self.d.items(), key=lambda kv: kv[1].get("fetched_at") or "")
            self.d = dict(items[-self.max_entries:])
        save_json(self.path, {"updated": iso(), "count": len(self.d), "wallets": self.d})
        self.dirty = False


class Forensics:
    def __init__(self, blockscout, pons, chain_cfg: Dict[str, Any], rules: Dict[str, Any],
                 cache: Optional[WalletCache] = None, rpc=None):
        self.bs = blockscout
        self.pons = pons
        self.rpc = rpc
        self.cfg = rules.get("forensics") or {}
        self.neutral = {norm_addr(a) for a in ((chain_cfg.get("neutral_funders") or {}).get("addresses") or [])}
        self.burn = {norm_addr(a) for a in (chain_cfg.get("burn_addresses") or [])}
        lp = (chain_cfg.get("launchpads") or {}).get("pons") or {}
        for k in ("v1_factory", "v2_factory", "v2_router", "v2_meme_hook"):
            if lp.get(k):
                self.neutral.add(norm_addr(lp[k]))
        self.cache = cache or WalletCache(max_entries=int(self.cfg.get("wallet_cache_max_entries", 6000)))
        self.last_holders: List[HolderInfo] = []

    # ---------------------------------------------------------------- wallet
    def profile_wallet(self, addr: str, is_contract: bool = False) -> WalletProfile:
        addr = norm_addr(addr)
        cached = self.cache.get(addr)
        if cached and cached.quality == "full":
            return cached
        p = WalletProfile(address=addr, is_contract=is_contract, fetched_at=iso())
        try:
            txs = self.bs.first_txs(addr, 3)
            if txs:
                first = txs[0]
                p.first_tx_at = iso(parse_iso(first["ts"])) if first.get("ts") else None
                p.age_hours = round(hours_between(p.first_tx_at, now_utc()), 2) if p.first_tx_at else None
                incoming = next((t for t in txs if t.get("to") == addr and t.get("value", 0) > 0), None)
                if incoming:
                    p.first_funder = incoming["from"]
                    p.first_funder_kind = "neutral" if incoming["from"] in self.neutral else "eoa_or_contract"
                elif first.get("from") == addr:
                    p.first_funder_kind = "unknown"   # 第一笔就是自己发起（gas 来自内部交易/桥）
            p.quality = "partial"
            cnt = self.bs.address_counters(addr)
            p.tx_count = cnt.get("transactions")
            p.token_transfers_count = cnt.get("token_transfers")
            p.quality = "full"
        except Exception as e:  # noqa: BLE001
            p.label = f"err:{type(e).__name__}"
        self.cache.put(p)
        return p

    # ---------------------------------------------------------------- token
    def analyze(self, token: str, pool_addresses: Optional[Set[str]] = None,
                creator_hint: str = "") -> ForensicsResult:
        token = norm_addr(token)
        res = ForensicsResult(token=token)
        pools = {norm_addr(a) for a in (pool_addresses or set())}
        calls0 = int(getattr(self.bs.http, "stats", {}).get("calls", 0))
        try:
            tinfo = self.bs.token(token)
        except Exception as e:  # noqa: BLE001
            res.notes.append(f"token 信息获取失败: {type(e).__name__}")
            res.quality = "none"
            return res
        supply = tinfo.get("total_supply") or 0
        res.holders_total = tinfo.get("holders_count") or None

        creator, creation_tx, cname = creator_hint, "", ""
        try:
            ainfo = self.bs.address(token)
            creator, creation_tx, cname = ainfo.get("creator") or creator, ainfo.get("creation_tx") or "", ainfo.get("name") or ""
        except Exception:
            res.notes.append("合约创建者信息不可用")
        res.creator_address = creator or ""
        res.launchpad = self.pons.detect(creator, cname) if self.pons else ""

        try:
            holders = self.bs.token_holders(token, limit=50, total_supply=supply or None)
        except Exception as e:  # noqa: BLE001
            res.notes.append(f"持有人列表获取失败: {type(e).__name__}")
            res.quality = "none"
            return res
        if not holders:
            res.notes.append("持有人列表为空")
            res.quality = "none"
            return res
        self.last_holders = holders

        # ---- 分类
        eoas: List[HolderInfo] = []
        for h in holders:
            h.kind = self.pons.classify_holder(h, pools, self.burn, creator) if self.pons else ("contract" if h.is_contract else "eoa")
            if h.kind == "burn":
                res.burn_pct += h.pct_supply
            elif h.kind in ("pool", "curve", "locker", "contract"):
                res.contract_held_pct += h.pct_supply
            elif h.kind == "creator":
                res.creator_pct += h.pct_supply
                eoas.append(h)
            else:
                eoas.append(h)
        res.curve_status = self.pons.curve_status(holders) if self.pons else ""
        eoas.sort(key=lambda h: h.pct_supply, reverse=True)
        res.top10_eoa_pct = round(sum(h.pct_supply for h in eoas[:10]), 3)
        res.top1_eoa_pct = round(eoas[0].pct_supply, 3) if eoas else 0.0
        res.contract_held_pct = round(res.contract_held_pct, 3)
        res.burn_pct = round(res.burn_pct, 3)
        res.creator_pct = round(res.creator_pct, 3)

        # ---- 钱包画像
        n = int(self.cfg.get("top_holders_to_inspect", 25))
        inspect = eoas[:n]
        profiles: Dict[str, WalletProfile] = {}
        for h in inspect:
            profiles[h.address] = self.profile_wallet(h.address)
        res.inspected = len(inspect)
        res.inspected_pct = round(sum(h.pct_supply for h in inspect), 3)
        full = sum(1 for p in profiles.values() if p.quality == "full")
        res.quality = "full" if inspect and full >= 0.7 * len(inspect) else "partial"

        # ---- 早期买家（创建区块起 ≤1000 条 Transfer）
        early: List[str] = []
        transfers: List[dict] = []
        if creation_tx:
            try:
                blk = self.bs.tx(creation_tx).get("block") or 0
                if blk:
                    transfers = self.bs.token_transfer_logs_asc(token, blk)
            except Exception:
                res.notes.append("早期转账日志不可用")
        holder_set = {h.address for h in eoas}
        skip = pools | self.burn | {h.address for h in holders if h.kind in ("pool", "curve", "locker", "contract")}
        want = int(self.cfg.get("early_buyers_count", 30))
        for t in transfers:
            to = norm_addr(t.get("to") or "")
            if to and to not in skip and to != token and to not in early:
                early.append(to)
            if len(early) >= want:
                break
        res.early_buyers = early
        if early:
            held = sum(h.pct_supply for h in eoas if h.address in set(early))
            res.early_buyers_holding_pct = round(held, 3)

        # ---- 聚类
        uf = UnionFind()
        pct_of = {h.address: h.pct_supply for h in inspect}
        for a in pct_of:
            uf.add(a)
        # 1) 同一打款方 / 打款方是另一个持有人
        by_funder: Dict[str, List[str]] = {}
        for a, p in profiles.items():
            f = norm_addr(p.first_funder or "")
            if f and f not in self.neutral:
                by_funder.setdefault(f, []).append(a)
                if f in pct_of and f != a:
                    uf.union(a, f, "funded_by_holder")
        for f, ws in by_funder.items():
            if len(ws) >= 2:
                for w in ws[1:]:
                    uf.union(ws[0], w, "same_funder")
        # 2) 持有人之间直接转账
        for t in transfers:
            fr, to = norm_addr(t.get("from") or ""), norm_addr(t.get("to") or "")
            if fr in pct_of and to in pct_of and fr != to:
                uf.union(fr, to, "direct_transfer")
        # 3) 同批次创建的新钱包
        fresh_h = float(self.cfg.get("fresh_wallet_hours", 24))
        fresh_tx = int(self.cfg.get("fresh_wallet_max_txs", 10))
        window_min = float(self.cfg.get("batch_creation_window_minutes", 15))
        min_batch = int(self.cfg.get("min_batch_size", 3))
        fresh_list = []
        for a, p in profiles.items():
            if p.first_tx_at and p.age_hours is not None and p.age_hours <= fresh_h and (p.tx_count or 0) <= fresh_tx:
                fresh_list.append((parse_iso(p.first_tx_at), a))
        fresh_list.sort(key=lambda x: x[0])
        i = 0
        while i < len(fresh_list):
            j = i
            while j + 1 < len(fresh_list) and (fresh_list[j + 1][0] - fresh_list[i][0]).total_seconds() <= window_min * 60:
                j += 1
            batch = [a for _, a in fresh_list[i:j + 1]]
            if len(batch) >= min_batch:
                for w in batch[1:]:
                    uf.union(batch[0], w, "batch_creation")
            i = j + 1

        clusters = []
        for root, members in uf.groups().items():
            if len(members) >= 2:
                pct = round(sum(pct_of.get(mm, 0.0) for mm in members), 3)
                clusters.append({"size": len(members), "pct": pct, "reasons": sorted(uf.reason.get(root, set())),
                                 "wallets": sorted(members, key=lambda mm: -pct_of.get(mm, 0.0))})
        clusters.sort(key=lambda c: -c["pct"])
        res.clusters = clusters
        res.clustered_pct = round(sum(c["pct"] for c in clusters), 3)
        res.largest_cluster_pct = clusters[0]["pct"] if clusters else 0.0

        fresh_pct = sum(pct_of[a] for a, p in profiles.items()
                        if p.age_hours is not None and p.age_hours <= fresh_h and (p.tx_count or 0) <= fresh_tx)
        res.fresh_wallet_pct = round(fresh_pct, 3)
        res.fresh_wallet_count = sum(1 for p in profiles.values()
                                     if p.age_hours is not None and p.age_hours <= fresh_h and (p.tx_count or 0) <= fresh_tx)

        # ---- sybil 评分（相对于被检查的持仓份额）
        base = res.inspected_pct or 1.0
        clustered_share = clamp(res.clustered_pct / base, 0, 1)
        fresh_share = clamp(res.fresh_wallet_pct / base, 0, 1)
        score = 0.6 * clustered_share + 0.4 * fresh_share
        if res.largest_cluster_pct >= 20:
            score += 0.2
        if res.creator_pct > 10:
            score += 0.1
        if res.quality != "full":
            score *= 0.8   # 数据不全时不要过度定罪
        res.sybil_score = round(clamp(score, 0, 1), 3)

        cluster_of: Dict[str, int] = {}
        for ci, cl in enumerate(clusters):
            for wa in cl["wallets"]:
                cluster_of[wa] = ci
        res.holder_map = [{
            "a": h.address, "p": round(h.pct_supply, 3), "c": cluster_of.get(h.address),
            "f": bool(profiles[h.address].age_hours is not None and profiles[h.address].age_hours <= fresh_h and (profiles[h.address].tx_count or 0) <= fresh_tx),
            "age": profiles[h.address].age_hours, "tx": profiles[h.address].tx_count,
            "k": h.kind,
        } for h in inspect]
        if clusters:
            res.notes.append(f"发现 {len(clusters)} 个关联簇，合计持仓 {res.clustered_pct:.1f}%，最大簇 {res.largest_cluster_pct:.1f}%")
        if res.fresh_wallet_count:
            res.notes.append(f"{res.fresh_wallet_count} 个前排钱包为 <{fresh_h:.0f}h 新钱包（合计 {res.fresh_wallet_pct:.1f}%）")
        if res.early_buyers_holding_pct is not None:
            res.notes.append(f"最早 {len(early)} 个买家仍持有前排筹码 {res.early_buyers_holding_pct:.1f}%")
        res.calls_used = int(getattr(self.bs.http, "stats", {}).get("calls", 0)) - calls0
        self.cache.save()
        return res
