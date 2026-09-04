# -*- coding: utf-8 -*-
"""fakechain.py — 离线合成链：生成 GeckoTerminal / Blockscout / OKX / CoinGecko 形态的响应。

场景代币：
  GOODCAT   健康：分散持有、独立老钱包、Pons 毕业、广泛参与 → 应进入 WATCH/PAPER_BUY，24h 后 +150%
  FAKEPUMP  女巫：前排钱包同一打款方同批创建、买盘集中、价涨无钱 → 应被剔除/红旗，之后归零
  MEHTOKEN  平庸：指标一般 → 评分不够，SKIP
  NEWBORN   太新（6 分钟）→ 预过滤剔除但记录为 SKIP 样本
  OLDIE     太老（100h）→ 剔除
  DEADCOIN  流动性 2k → 剔除且不进样本
  WETH 基础币的池 → 被 quote 过滤掉
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Dict, List

from radar.sources.http import FakeHttp
from radar.util import topic0

GT = "https://api.geckoterminal.com/api/v2"
BS_REST = "https://robinhoodchain.blockscout.com/api/v2"
BS_LEGACY = "https://robinhoodchain.blockscout.com/api"
RPC = "https://rpc.mainnet.chain.robinhood.com"
PONS_V2 = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
TRANSFER = topic0("Transfer(address,address,uint256)")
NOW = int(time.time())


def addr(seed: str) -> str:
    return "0x" + hashlib.sha1(seed.encode()).hexdigest()[:40]


def iso_ago(hours: float) -> str:
    t = time.gmtime(NOW - int(hours * 3600))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


class Token:
    def __init__(self, symbol: str, **kw):
        self.symbol = symbol
        self.name = kw.get("name", symbol.title())
        self.token = addr("token:" + symbol)
        self.pool = addr("pool:" + symbol)
        self.liq = kw.get("liq", 50_000)
        self.age_h = kw.get("age_h", 5.0)
        self.price = kw.get("price", 0.001)
        self.buyers = kw.get("buyers", {"m5": 5, "h1": 30, "h6": 120, "h24": 300})
        self.buys = kw.get("buys", {"m5": 8, "h1": 60, "h6": 240, "h24": 700})
        self.sells = kw.get("sells", {"m5": 4, "h1": 35, "h6": 150, "h24": 420})
        self.vol = kw.get("vol", {"m5": 3000, "h1": 40_000, "h6": 150_000, "h24": 400_000})
        self.chg = kw.get("chg", {"m5": 1.0, "h1": 12.0, "h6": 45.0, "h24": 80.0})
        self.kind = kw.get("kind", "healthy")         # healthy / sybil / plain
        self.creator = kw.get("creator", PONS_V2)
        self.n_holders = kw.get("n_holders", 30)
        self.outcome = kw.get("outcome", "flat")      # up / rug / flat / down
        self.base_is_weth = kw.get("base_is_weth", False)
        self.supply = 10 ** 27  # 1e9 * 1e18

    # ------------------------------------------------------------ GT pool
    def gt_pool(self) -> Dict[str, Any]:
        base_id = f"robinhood_{self.token}"
        quote_id = "robinhood_" + addr("weth")
        if self.base_is_weth:
            base_id, quote_id = quote_id, base_id
        return {
            "id": f"robinhood_{self.pool}", "type": "pool",
            "attributes": {
                "address": self.pool, "name": f"{'WETH' if self.base_is_weth else self.symbol} / WETH",
                "base_token_price_usd": str(self.price), "quote_token_price_usd": "3000",
                "pool_created_at": iso_ago(self.age_h), "fdv_usd": str(self.price * 1e9),
                "market_cap_usd": None, "reserve_in_usd": str(self.liq),
                "price_change_percentage": {k: str(v) for k, v in self.chg.items()},
                "transactions": {w: {"buys": self.buys.get(w, 0), "sells": self.sells.get(w, 0),
                                     "buyers": self.buyers.get(w, 0), "sellers": max(1, self.buyers.get(w, 0) // 2)}
                                 for w in ("m5", "h1", "h6", "h24")},
                "volume_usd": {k: str(v) for k, v in self.vol.items()},
            },
            "relationships": {"base_token": {"data": {"id": base_id, "type": "token"}},
                              "quote_token": {"data": {"id": quote_id, "type": "token"}},
                              "dex": {"data": {"id": "uniswap-v4-robinhood", "type": "dex"}}},
        }

    def gt_included(self) -> List[Dict[str, Any]]:
        return [{"id": f"robinhood_{self.token}", "type": "token",
                 "attributes": {"address": self.token, "name": self.name, "symbol": self.symbol, "decimals": 18}},
                {"id": "robinhood_" + addr("weth"), "type": "token",
                 "attributes": {"address": addr("weth"), "name": "Wrapped Ether", "symbol": "WETH", "decimals": 18}}]

    # ------------------------------------------------------------ holders / wallets
    def holders(self) -> List[Dict[str, Any]]:
        items = []
        # 锁仓合约（毕业池）
        items.append({"address": {"hash": addr("locker:" + self.symbol), "is_contract": True, "name": "PonsV2LaunchLocker"},
                      "value": str(int(self.supply * 0.40)), "token": {"total_supply": str(self.supply)}})
        items.append({"address": {"hash": self.pool, "is_contract": True, "name": "PoolManager"},
                      "value": str(int(self.supply * 0.10)), "token": {"total_supply": str(self.supply)}})
        remaining = 0.50
        n = self.n_holders
        if self.kind == "sybil":
            weights = [0.045] * 10 + [0.01] * (n - 10)
        else:
            weights = [0.05 * (0.82 ** i) for i in range(n)]
        tot = sum(weights)
        for i, w in enumerate(weights):
            pct = remaining * w / tot
            items.append({"address": {"hash": self.wallet(i), "is_contract": False, "name": None},
                          "value": str(int(self.supply * pct)), "token": {"total_supply": str(self.supply)}})
        return items

    def wallet(self, i: int) -> str:
        return addr(f"w:{self.symbol}:{i}")

    def wallet_profile(self, w: str) -> Dict[str, Any]:
        """返回 {first_ts, funder, txs}"""
        i = int(w[-2:], 16) if False else None
        idx = next((k for k in range(self.n_holders) if self.wallet(k) == w), 0)
        if self.kind == "sybil" and idx < 12:
            return {"first_ts": NOW - int(self.age_h * 3600) - 600 + idx * 30, "funder": addr("funder:BAD"), "txs": 2 + idx % 3}
        return {"first_ts": NOW - (30 + idx * 97) * 3600, "funder": addr(f"funder:{self.symbol}:{idx}"), "txs": 40 + idx * 13}

    def transfer_logs(self) -> List[Dict[str, Any]]:
        rows = []
        blk = 1_000_000
        for i in range(min(self.n_holders, 40)):
            rows.append({"topics": [TRANSFER, "0x" + self.pool[2:].rjust(64, "0"), "0x" + self.wallet(i)[2:].rjust(64, "0")],
                         "data": hex(10 ** 24), "blockNumber": hex(blk + i), "timeStamp": hex(NOW - int(self.age_h * 3600) + i * 20),
                         "transactionHash": addr(f"tx:{self.symbol}:{i}")})
        if self.kind == "sybil":
            rows.append({"topics": [TRANSFER, "0x" + self.wallet(0)[2:].rjust(64, "0"), "0x" + self.wallet(1)[2:].rjust(64, "0")],
                         "data": hex(10 ** 23), "blockNumber": hex(blk + 50), "timeStamp": hex(NOW - 1000), "transactionHash": addr("tx:s")})
        return rows

    def trades(self) -> Dict[str, Any]:
        data = []
        n = 60
        for i in range(n):
            if self.kind == "sybil":
                w = self.wallet(i % 4) if i % 5 else addr(f"rand:{i}")
                kind = "buy" if i % 4 else "sell"
                vol = 900 if i % 5 else 50
            else:
                w = addr(f"trader:{self.symbol}:{i}")
                kind = "buy" if i % 3 else "sell"
                vol = 120 + (i % 7) * 40
            data.append({"id": f"t{i}", "type": "trade", "attributes": {
                "block_number": 1_000_500 + i, "tx_hash": addr(f"ttx:{self.symbol}:{i}"), "tx_from_address": w,
                "from_token_amount": "1", "to_token_amount": "1", "price_from_in_usd": str(self.price), "price_to_in_usd": str(self.price),
                "block_timestamp": iso_ago(0.5 + i * 0.01), "kind": kind, "volume_in_usd": str(vol)}})
        return {"data": data}

    def ohlcv(self, since: int) -> List[List[Any]]:
        rows = []
        for h in range(0, 200):
            ts = since + h * 3600
            if ts > NOW + 3600:
                break
            if self.outcome == "up":
                m = 1 + 2.0 * min(h, 20) / 20 - (0.5 if h > 20 else 0)  # 到 3x 后回到 2.5x
            elif self.outcome == "rug":
                m = max(0.05, 1 - 0.3 * h)
            elif self.outcome == "down":
                m = max(0.3, 1 - 0.04 * h)
            else:
                m = 1 + 0.02 * math.sin(h)
            c = self.price * m
            rows.append([ts, c * 0.98, c * 1.05, c * 0.95, c, 5000])
        return list(reversed(rows))


def build_universe() -> List[Token]:
    return [
        Token("GOODCAT", name="Good Cat", liq=80_000, age_h=6, price=0.002, kind="healthy", outcome="up",
              buyers={"m5": 6, "h1": 40, "h6": 160, "h24": 400}, vol={"m5": 5000, "h1": 60_000, "h6": 250_000, "h24": 600_000}),
        Token("FAKEPUMP", name="Fake Pump", liq=40_000, age_h=3, price=0.0005, kind="sybil", n_holders=20, outcome="rug",
              buyers={"m5": 2, "h1": 8, "h6": 40, "h24": 120}, chg={"m5": 15.0, "h1": 90.0, "h6": 300.0, "h24": 300.0},
              vol={"m5": 8000, "h1": 90_000, "h6": 200_000, "h24": 300_000}),
        Token("MEHTOKEN", name="Meh", liq=20_000, age_h=30, price=0.0001, kind="plain", n_holders=12, outcome="flat",
              buyers={"m5": 1, "h1": 4, "h6": 20, "h24": 60}, chg={"m5": 0, "h1": -5.0, "h6": -20.0, "h24": 10.0},
              vol={"m5": 200, "h1": 3000, "h6": 12_000, "h24": 30_000}, buys={"m5": 1, "h1": 6, "h6": 30, "h24": 90},
              sells={"m5": 1, "h1": 9, "h6": 40, "h24": 100}, creator=addr("random-deployer")),
        Token("NEWBORN", liq=30_000, age_h=0.1, price=0.001, kind="plain", outcome="down"),
        Token("OLDIE", liq=60_000, age_h=100, price=0.003, kind="healthy", outcome="flat"),
        Token("DEADCOIN", liq=2_000, age_h=10, price=0.00001, kind="plain", outcome="rug"),
        Token("WETHPOOL", liq=500_000, age_h=10, price=3000, base_is_weth=True),
        Token("RANDO1", liq=12_000, age_h=8, price=0.0002, kind="plain", n_holders=8, outcome="flat",
              buyers={"m5": 2, "h1": 10, "h6": 40, "h24": 90}),
        Token("RANDO2", liq=9_000, age_h=20, price=0.0007, kind="plain", n_holders=8, outcome="down",
              buyers={"m5": 1, "h1": 6, "h6": 30, "h24": 70}),
    ]


def build_fake_http(tokens: List[Token]) -> FakeHttp:
    f = FakeHttp(name="fakechain")
    include = "include=base_token%2Cquote_token%2Cdex"
    listed = [t for t in tokens]
    page1 = {"data": [t.gt_pool() for t in listed], "included": sum((t.gt_included() for t in listed), [])}
    f.route(f"{GT}/networks/robinhood/new_pools?page=1&{include}", page1)
    for p in range(2, 8):
        f.route(f"{GT}/networks/robinhood/new_pools?page={p}&{include}", {"data": [], "included": []})
    trending = [t for t in listed if t.symbol in ("GOODCAT", "FAKEPUMP")]
    for d in ("5m", "1h", "6h", "24h"):
        f.route(f"{GT}/networks/robinhood/trending_pools?page=1&duration={d}&{include}",
                {"data": [t.gt_pool() for t in trending], "included": sum((t.gt_included() for t in trending), [])})
    for p in (1, 2, 3):
        for sort in ("h24_volume_usd_desc", "h24_tx_count_desc"):
            f.route(f"{GT}/networks/robinhood/pools?page={p}&sort={sort}&{include}",
                    {"data": [t.gt_pool() for t in listed] if p == 1 else [], "included": sum((t.gt_included() for t in listed), []) if p == 1 else []})
    for t in listed:
        f.route(f"{GT}/networks/robinhood/pools/{t.pool}?{include}", lambda t=t: {"data": t.gt_pool(), "included": t.gt_included()})
        f.route(f"{GT}/networks/robinhood/pools/{t.pool}/trades?trade_volume_in_usd_greater_than=0", lambda t=t: t.trades())
        f.route(f"{GT}/networks/robinhood/pools/{t.pool}/ohlcv/hour?", lambda t=t: {"data": {"attributes": {"ohlcv_list": t.ohlcv(NOW - 40 * 3600)}}})
        f.route(f"{BS_REST}/tokens/{t.token}/holders", lambda t=t: {"items": t.holders(), "next_page_params": None})
        f.route(f"{BS_REST}/tokens/{t.token}", lambda t=t: {"name": t.name, "symbol": t.symbol, "decimals": "18", "total_supply": str(t.supply), "holders": str(t.n_holders + 2)})
        f.route(f"{BS_REST}/addresses/{t.token}", lambda t=t: {"hash": t.token, "is_contract": True, "is_verified": True,
                                                              "name": "PonsV2LauncherToken" if t.creator == PONS_V2 else "Token",
                                                              "creator_address_hash": t.creator, "creation_transaction_hash": addr("ctx:" + t.symbol)})
        f.route(f"{BS_REST}/transactions/{addr('ctx:' + t.symbol)}", {"block": 1_000_000, "from": {"hash": t.creator}, "timestamp": iso_ago(t.age_h)})
        f.route(f"{BS_LEGACY}?module=logs&action=getLogs&fromBlock=1000000&toBlock=latest&address={t.token}&topic0={TRANSFER}",
                lambda t=t: {"status": "1", "result": t.transfer_logs()})
        for i in range(t.n_holders):
            w = t.wallet(i)
            prof = t.wallet_profile(w)
            f.route(f"{BS_LEGACY}?module=account&action=txlist&address={w}&sort=asc&page=1&offset=3",
                    {"status": "1", "result": [{"hash": addr("ftx:" + w), "timeStamp": str(prof["first_ts"]), "from": prof["funder"], "to": w,
                                                "value": "100000000000000000", "blockNumber": "900000"}]})
            f.route(f"{BS_REST}/addresses/{w}/counters", {"transactions_count": str(prof["txs"]), "token_transfers_count": str(prof["txs"] * 2)})
    # DexScreener：批量代币 → 交易对；单交易对
    def ds_pair(t: Token) -> Dict[str, Any]:
        return {"chainId": "robinhood", "dexId": "uniswap", "url": f"https://dexscreener.com/robinhood/{t.pool}", "pairAddress": t.pool,
                "baseToken": {"address": t.token, "name": t.name, "symbol": t.symbol}, "quoteToken": {"address": addr("weth"), "symbol": "WETH"},
                "priceUsd": str(t.price), "txns": {w: {"buys": t.buys.get(w, 0), "sells": t.sells.get(w, 0)} for w in ("m5", "h1", "h6", "h24")},
                "volume": {k: v for k, v in t.vol.items()}, "priceChange": {k: v for k, v in t.chg.items()},
                "liquidity": {"usd": t.liq}, "fdv": t.price * 1e9, "pairCreatedAt": (NOW - int(t.age_h * 3600)) * 1000,
                "info": {"websites": [{"url": "https://example.com"}], "socials": [{"type": "twitter", "url": "https://x.com/x"}]} if t.kind == "healthy" else {},
                "boosts": {"active": 0}}

    def ds_tokens(url_tail: str):
        addrs = url_tail.split(",")
        return [ds_pair(t) for t in listed if t.token in addrs]
    for t in listed:
        f.route(f"https://api.dexscreener.com/token-pairs/v1/robinhood/{t.token}", lambda t=t: [ds_pair(t)])
        f.route(f"https://api.dexscreener.com/latest/dex/pairs/robinhood/{t.pool}", lambda t=t: {"pairs": [ds_pair(t)]})
    f.routes["https://api.dexscreener.com/tokens/v1/robinhood/"] = lambda: [ds_pair(t) for t in listed]
    # 市场数据
    closes = [60000 + i * 90 for i in range(300)]          # 稳步上涨：站上全部均线
    okx_btc = {"code": "0", "data": [[str((NOW - i * 86400) * 1000), str(c), str(c * 1.02), str(c * 0.98), str(c), "1", "1", "1", "1"]
                                     for i, c in enumerate(reversed(closes))]}
    f.route("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=300", okx_btc)
    ethbtc = [0.030 + i * 0.0001 for i in range(60)]
    f.route("https://www.okx.com/api/v5/market/candles?instId=ETH-BTC&bar=1D&limit=60",
            {"code": "0", "data": [[str((NOW - i * 86400) * 1000), str(c), str(c), str(c), str(c), "1", "1", "1", "1"] for i, c in enumerate(reversed(ethbtc))]})
    f.route("https://api.coingecko.com/api/v3/global", {"data": {"market_cap_percentage": {"btc": 60.5, "eth": 12.1},
                                                                  "total_market_cap": {"usd": 3.1e12}, "market_cap_change_percentage_24h_usd": 1.2}})
    coins = [{"id": "bitcoin", "symbol": "btc", "price_change_percentage_7d_in_currency": 5.0, "price_change_percentage_30d_in_currency": 12.0}]
    for i in range(99):
        coins.append({"id": f"alt{i}", "symbol": f"a{i}", "price_change_percentage_7d_in_currency": 8.0 if i % 3 == 0 else 1.0,
                      "price_change_percentage_30d_in_currency": 20.0 if i % 4 == 0 else 5.0})
    f.route("https://api.coingecko.com/api/v3/coins/markets?", coins)
    f.route("https://api.alternative.me/fng/?limit=1", {"data": [{"value": "65", "value_classification": "Greed"}]})
    f.route("https://api.gopluslabs.io/api/v1/token_security/4663?", {"code": 2, "message": "chain not supported", "result": {}})
    f.route(f"{RPC}#eth_call", lambda body: {"jsonrpc": "2.0", "id": 1, "result": "0x"})
    return f


def all_overrides(f: FakeHttp) -> Dict[str, Any]:
    return {k: f for k in ("geckoterminal", "dexscreener", "blockscout", "rpc", "okx", "coinbase", "coingecko", "fng", "goplus", "llm", "gmgn")}
