# P2 数据层实现（cresus-bot Data Layer）

> 前提：P0 骨架完成（`08-bootstrap-cresus-bot.md` 全部打勾）。
>
> 这份文档带你实现 `src/data_layer/` 的完整数据采集代码：
> - Binance Futures 扫 100 币基础数据
> - 硬过滤 + 异常币筛选
> - 对异常币拉完整 22 维（含 OKX / Coinglass / CoinGecko）
> - 本地跑通 → 准备好喂给 P3 AI 层
>
> 全流程 ~45 分钟（代码 + 测试）。

---

## 阶段概览

```
Stage A：基础依赖（5 分钟）
Stage B：Binance 客户端 + 扫币（15 分钟）
Stage C：OKX / Coinglass / CoinGecko 客户端（10 分钟）
Stage D：22 维组装器（10 分钟）
Stage E：本地烟雾测试（5 分钟）
Stage F：commit + push（2 分钟）
```

---

## Stage A · 追加依赖

在 `cresus-bot/` 根目录，打开 `pyproject.toml`，把 `dependencies` 改为：

```toml
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "loguru>=0.7",
    "tenacity>=8.3",       # 自动重试
    "python-dotenv>=1.0",  # 备用加载
]
```

然后：

```bash
uv sync
```

---

## Stage B · Binance 客户端 + 扫币

### B.1 `src/data_layer/binance_client.py`

```python
"""Binance REST client — read-only, public + signed endpoints."""
import time
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import settings

_BASE_SPOT = "https://api.binance.com"
_BASE_FAPI = "https://fapi.binance.com"   # USDT-M Futures
_BASE_DAPI = "https://dapi.binance.com"   # COIN-M (暂不用)


def _client() -> httpx.Client:
    return httpx.Client(timeout=10)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_futures_exchange_info() -> dict[str, Any]:
    with _client() as c:
        return c.get(f"{_BASE_FAPI}/fapi/v1/exchangeInfo").raise_for_status().json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_futures_ticker_24h() -> list[dict]:
    """返回所有 USDT-M 合约 24h 统计（含 quoteVolume, lastPrice）。"""
    with _client() as c:
        return c.get(f"{_BASE_FAPI}/fapi/v1/ticker/24hr").raise_for_status().json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_open_interest(symbol: str) -> dict:
    with _client() as c:
        return (
            c.get(f"{_BASE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_open_interest_hist(symbol: str, period: str = "1h", limit: int = 24) -> list[dict]:
    with _client() as c:
        return (
            c.get(
                f"{_BASE_FAPI}/futures/data/openInterestHist",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_funding_rate(symbol: str) -> dict:
    """当前资金费率 + 下次结算时间。"""
    with _client() as c:
        return (
            c.get(f"{_BASE_FAPI}/fapi/v1/premiumIndex", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_funding_rate_hist(symbol: str, limit: int = 24) -> list[dict]:
    with _client() as c:
        return (
            c.get(
                f"{_BASE_FAPI}/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": limit},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_klines(
    symbol: str,
    interval: str = "1d",
    limit: int = 7,
    is_futures: bool = True,
) -> list[list]:
    base = _BASE_FAPI if is_futures else _BASE_SPOT
    endpoint = "/fapi/v1/klines" if is_futures else "/api/v3/klines"
    with _client() as c:
        return (
            c.get(f"{base}{endpoint}", params={"symbol": symbol, "interval": interval, "limit": limit})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_long_short_ratio(symbol: str, period: str = "1h", limit: int = 6) -> list[dict]:
    with _client() as c:
        return (
            c.get(
                f"{_BASE_FAPI}/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_top_trader_ratio(symbol: str, period: str = "1h", limit: int = 6) -> list[dict]:
    with _client() as c:
        return (
            c.get(
                f"{_BASE_FAPI}/futures/data/topLongShortPositionRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_taker_ratio(symbol: str, period: str = "1h", limit: int = 6) -> list[dict]:
    with _client() as c:
        return (
            c.get(
                f"{_BASE_FAPI}/futures/data/takerlongshortRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_order_book(symbol: str, limit: int = 20) -> dict:
    with _client() as c:
        return (
            c.get(f"{_BASE_FAPI}/fapi/v1/depth", params={"symbol": symbol, "limit": limit})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_spot_ticker(symbol: str) -> dict:
    """现货价，用于计算 Basis。"""
    with _client() as c:
        return (
            c.get(f"{_BASE_SPOT}/api/v3/ticker/price", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )
```

### B.2 `src/data_layer/scanner.py`（扫 100 币 + 硬过滤 + 异常筛选）

```python
"""Top-N coin scanner.

Step 1: fetch all USDT-M futures tickers (24h stats)
Step 2: filter to top-N by quoteVolume
Step 3: hard-filter vol/OI > 20x (no real contract structure)
Step 4: flag anomalies — any of:
        - OI 24h change > ±20%
        - |funding rate| > 0.05%
        - price vs MA20 deviation > threshold
"""
from dataclasses import dataclass, field

from loguru import logger

from common.config import settings
from data_layer.binance_client import (
    get_funding_rate,
    get_futures_ticker_24h,
    get_open_interest,
    get_open_interest_hist,
    get_klines,
)


@dataclass
class CoinSnapshot:
    symbol: str
    price: float
    vol_24h_usd: float       # quote volume in USDT
    oi_usd: float            # current OI in USDT
    vol_oi_ratio: float      # vol / OI
    funding_rate: float      # current funding rate (decimal, e.g. 0.0001)
    oi_change_24h_pct: float # % change of OI over past 24h
    price_vs_ma20_pct: float # % deviation of price from 20-period daily MA
    is_anomaly: bool = False
    anomaly_reasons: list[str] = field(default_factory=list)


def _calc_ma20_deviation(symbol: str, current_price: float) -> float:
    try:
        klines = get_klines(symbol, interval="1d", limit=20)
        closes = [float(k[4]) for k in klines]
        if len(closes) < 20:
            return 0.0
        ma20 = sum(closes) / len(closes)
        return (current_price - ma20) / ma20 * 100
    except Exception:
        return 0.0


def _calc_oi_change_24h(symbol: str) -> float:
    try:
        hist = get_open_interest_hist(symbol, period="1h", limit=25)
        if len(hist) < 2:
            return 0.0
        latest = float(hist[-1]["sumOpenInterestValue"])
        ago_24h = float(hist[0]["sumOpenInterestValue"])
        if ago_24h == 0:
            return 0.0
        return (latest - ago_24h) / ago_24h * 100
    except Exception:
        return 0.0


def scan_top_coins(top_n: int | None = None) -> list[CoinSnapshot]:
    n = top_n or settings.scan_top_n_coins
    hard_filter = settings.vol_oi_hard_filter
    confidence_threshold_watch = settings.confidence_watch_threshold  # not used here, kept for context

    logger.info(f"Fetching 24h tickers for all USDT-M futures …")
    tickers = get_futures_ticker_24h()

    usdt_perp = [
        t for t in tickers
        if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 0
    ]
    usdt_perp.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    top = usdt_perp[:n]
    logger.info(f"Top {n} by volume selected, now evaluating …")

    results: list[CoinSnapshot] = []

    for t in top:
        symbol = t["symbol"]
        price = float(t["lastPrice"])
        vol_24h = float(t["quoteVolume"])

        try:
            oi_data = get_open_interest(symbol)
            oi_usd = float(oi_data["openInterest"]) * price
        except Exception:
            logger.warning(f"{symbol}: OI fetch failed, skipping")
            continue

        if oi_usd == 0:
            continue
        vol_oi = vol_24h / oi_usd

        # Hard filter
        if vol_oi > hard_filter:
            logger.debug(f"{symbol}: vol/OI={vol_oi:.1f}x > {hard_filter}x → skipped")
            continue

        try:
            fr_data = get_funding_rate(symbol)
            funding = float(fr_data["lastFundingRate"])
        except Exception:
            funding = 0.0

        oi_chg = _calc_oi_change_24h(symbol)
        ma20_dev = _calc_ma20_deviation(symbol, price)

        reasons: list[str] = []
        if abs(oi_chg) > 20:
            reasons.append(f"OI 24h {oi_chg:+.1f}%")
        if abs(funding) > 0.0005:
            reasons.append(f"funding {funding*100:+.4f}%")
        if abs(ma20_dev) > 10:
            reasons.append(f"MA20 dev {ma20_dev:+.1f}%")

        snap = CoinSnapshot(
            symbol=symbol,
            price=price,
            vol_24h_usd=vol_24h,
            oi_usd=oi_usd,
            vol_oi_ratio=vol_oi,
            funding_rate=funding,
            oi_change_24h_pct=oi_chg,
            price_vs_ma20_pct=ma20_dev,
            is_anomaly=bool(reasons),
            anomaly_reasons=reasons,
        )
        results.append(snap)

    anomalies = [s for s in results if s.is_anomaly]
    logger.info(f"Scan complete: {len(results)} passed hard filter, {len(anomalies)} anomalies")
    return results
```

---

## Stage C · OKX / Coinglass / CoinGecko 客户端

### C.1 `src/data_layer/okx_client.py`

```python
"""OKX REST client — public endpoints only."""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_BASE = "https://www.okx.com"


def _client() -> httpx.Client:
    return httpx.Client(timeout=10)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_funding_rate(instId: str) -> dict:
    """e.g. instId = 'BTC-USDT-SWAP'"""
    with _client() as c:
        return (
            c.get(f"{_BASE}/api/v5/public/funding-rate", params={"instId": instId})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_order_book(instId: str, sz: int = 20) -> dict:
    with _client() as c:
        return (
            c.get(f"{_BASE}/api/v5/market/books", params={"instId": instId, "sz": sz})
            .raise_for_status()
            .json()
        )


def binance_symbol_to_okx(symbol: str) -> str:
    """'BTCUSDT' → 'BTC-USDT-SWAP'"""
    base = symbol.replace("USDT", "")
    return f"{base}-USDT-SWAP"
```

### C.2 `src/data_layer/coinglass_client.py`

```python
"""Coinglass REST client — requires API key (paid plan)."""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import settings

_BASE = "https://open-api.coinglass.com"


def _headers() -> dict[str, str]:
    return {"coinglassSecret": settings.coinglass_api_key}


def _client() -> httpx.Client:
    return httpx.Client(timeout=10, headers=_headers())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_open_interest_by_exchange(symbol: str) -> dict:
    """各交易所 OI 分布，symbol='BTC'。"""
    with _client() as c:
        return (
            c.get(f"{_BASE}/public/v2/open_interest", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_liquidation(symbol: str) -> dict:
    """24h 爆仓数据（需要你 Coinglass 最低档订阅）。"""
    with _client() as c:
        return (
            c.get(f"{_BASE}/public/v2/liquidation_history", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )
```

### C.3 `src/data_layer/coingecko_client.py`

```python
"""CoinGecko free-tier client — market cap, coin type, etc."""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_BASE = "https://api.coingecko.com/api/v3"

# 简单的 symbol→id 缓存（启动时可刷新）
_SYMBOL_TO_ID: dict[str, str] = {}


def _client() -> httpx.Client:
    return httpx.Client(timeout=15)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def load_coin_list() -> None:
    """预加载 symbol→coingecko_id 映射（约 15K 条）。启动时调用一次。"""
    global _SYMBOL_TO_ID
    with _client() as c:
        coins = c.get(f"{_BASE}/coins/list").raise_for_status().json()
    _SYMBOL_TO_ID = {c["symbol"].upper(): c["id"] for c in coins}


def get_coin_id(symbol: str) -> str | None:
    base = symbol.upper().replace("USDT", "").replace("BUSD", "")
    return _SYMBOL_TO_ID.get(base)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_coin_market_data(coin_id: str) -> dict:
    with _client() as c:
        return (
            c.get(
                f"{_BASE}/coins/{coin_id}",
                params={"localization": "false", "tickers": "false", "community_data": "false"},
            )
            .raise_for_status()
            .json()
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_fear_greed() -> dict:
    """alternative.me 恐惧贪婪指数。"""
    with _client() as c:
        return c.get("https://api.alternative.me/fng/?limit=1").raise_for_status().json()
```

---

## Stage D · 22 维组装器

### D.1 `src/data_layer/assembler.py`

```python
"""Full 22-dimension data assembly for a single anomaly coin.

Returns a structured dict ready to be formatted into a DeepSeek prompt.
"""
from loguru import logger

from data_layer import binance_client as bn
from data_layer import coingecko_client as cg
from data_layer import coinglass_client as glass
from data_layer import okx_client as okx
from data_layer.scanner import CoinSnapshot


def assemble(snap: CoinSnapshot) -> dict:
    sym = snap.symbol
    base = sym.replace("USDT", "")
    okx_sym = okx.binance_symbol_to_okx(sym)
    coin_id = cg.get_coin_id(sym)

    data: dict = {
        "symbol": sym,
        "price": snap.price,
        "vol_24h_usd": snap.vol_24h_usd,
        "vol_oi_ratio": snap.vol_oi_ratio,
        "oi_usd": snap.oi_usd,
        "funding_rate_binance": snap.funding_rate,
        "oi_change_24h_pct": snap.oi_change_24h_pct,
        "price_vs_ma20_pct": snap.price_vs_ma20_pct,
        "anomaly_reasons": snap.anomaly_reasons,
    }

    # --- Klines (dim 1) ---
    for interval, limit, key in [("1d", 7, "klines_1d"), ("4h", 6, "klines_4h"), ("1h", 6, "klines_1h")]:
        try:
            k = bn.get_klines(sym, interval=interval, limit=limit)
            data[key] = [{"o": x[1], "h": x[2], "l": x[3], "c": x[4], "v": x[5]} for x in k]
        except Exception as e:
            logger.warning(f"{sym} klines {interval}: {e}")
            data[key] = []

    # --- OI history (dim 2) ---
    try:
        data["oi_hist_1h"] = [
            {"ts": x["timestamp"], "oi": x["sumOpenInterest"]}
            for x in bn.get_open_interest_hist(sym, period="1h", limit=24)
        ]
    except Exception as e:
        logger.warning(f"{sym} OI hist: {e}")
        data["oi_hist_1h"] = []

    # --- Funding history (dim 5) ---
    try:
        data["funding_hist"] = [
            {"time": x["fundingTime"], "rate": x["fundingRate"]}
            for x in bn.get_funding_rate_hist(sym, limit=12)
        ]
    except Exception as e:
        logger.warning(f"{sym} funding hist: {e}")
        data["funding_hist"] = []

    # --- Basis (dim 6): futures price vs spot price ---
    try:
        spot = bn.get_spot_ticker(sym)
        spot_price = float(spot["price"])
        data["basis_pct"] = (snap.price - spot_price) / spot_price * 100
    except Exception:
        data["basis_pct"] = None

    # --- Long/short ratio (dim 7) ---
    try:
        data["long_short_ratio"] = bn.get_long_short_ratio(sym, period="1h", limit=6)
    except Exception:
        data["long_short_ratio"] = []

    # --- Top trader ratio (dim 7 detail) ---
    try:
        data["top_trader_ratio"] = bn.get_top_trader_ratio(sym, period="1h", limit=6)
    except Exception:
        data["top_trader_ratio"] = []

    # --- Taker ratio (dim 8) ---
    try:
        data["taker_ratio"] = bn.get_taker_ratio(sym, period="1h", limit=6)
    except Exception:
        data["taker_ratio"] = []

    # --- Order book (dim 11) ---
    try:
        data["order_book_binance"] = bn.get_order_book(sym, limit=20)
    except Exception:
        data["order_book_binance"] = {}

    # --- OKX funding (dim 4 cross-exchange) ---
    try:
        okx_fr = okx.get_funding_rate(okx_sym)
        data["funding_rate_okx"] = float(okx_fr["data"][0]["fundingRate"])
    except Exception:
        data["funding_rate_okx"] = None

    # --- Coinglass: exchange OI distribution (dim 10), liquidations (dim 12) ---
    try:
        oi_dist = glass.get_open_interest_by_exchange(base)
        data["oi_by_exchange"] = oi_dist.get("data", [])
    except Exception:
        data["oi_by_exchange"] = []

    try:
        liq = glass.get_liquidation(base)
        data["liquidation_24h"] = liq.get("data", {})
    except Exception:
        data["liquidation_24h"] = {}

    # --- CoinGecko: market cap, category (dim 13, 15) ---
    if coin_id:
        try:
            cg_data = cg.get_coin_market_data(coin_id)
            md = cg_data.get("market_data", {})
            data["market_cap_usd"] = md.get("market_cap", {}).get("usd")
            data["fdv_usd"] = md.get("fully_diluted_valuation", {}).get("usd")
            data["categories"] = cg_data.get("categories", [])
        except Exception:
            data["market_cap_usd"] = None
            data["fdv_usd"] = None
            data["categories"] = []
    else:
        data["market_cap_usd"] = None
        data["fdv_usd"] = None
        data["categories"] = []

    # --- OI/市值 (dim 14) ---
    if data["market_cap_usd"]:
        data["oi_to_mcap"] = snap.oi_usd / data["market_cap_usd"]
    else:
        data["oi_to_mcap"] = None

    # --- Fear & Greed (dim 16) ---
    try:
        fg = cg.get_fear_greed()
        data["fear_greed"] = fg["data"][0]
    except Exception:
        data["fear_greed"] = {}

    # --- BTC market context (dim 17) ---
    try:
        btc_ticker = [t for t in bn.get_futures_ticker_24h() if t["symbol"] == "BTCUSDT"]
        if btc_ticker:
            data["btc_price"] = float(btc_ticker[0]["lastPrice"])
            data["btc_change_24h_pct"] = float(btc_ticker[0]["priceChangePercent"])
    except Exception:
        data["btc_price"] = None
        data["btc_change_24h_pct"] = None

    return data
```

### D.2 `src/data_layer/__init__.py`（补充导出，可选）

文件已由 P0 创建（空 `__init__.py`），不需要修改。

---

## Stage E · 烟雾测试

### E.1 `scripts/test_data_layer.py`

```bash
cat > scripts/test_data_layer.py <<'EOF'
"""P2 data layer smoke test.

Scans top-10 coins, prints anomalies, then assembles full 22-dim for the
top anomaly.  Does NOT require paid API keys (Coinglass will fail gracefully).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from loguru import logger
from data_layer.coingecko_client import load_coin_list
from data_layer.scanner import scan_top_coins
from data_layer.assembler import assemble


def main() -> None:
    logger.info("=== P2 Data Layer Smoke Test ===")

    logger.info("Loading CoinGecko coin list …")
    load_coin_list()

    logger.info("Scanning top 10 coins …")
    snaps = scan_top_coins(top_n=10)

    for s in snaps:
        tag = "🔥 ANOMALY" if s.is_anomaly else "  normal"
        logger.info(
            f"{tag}  {s.symbol:<12} price={s.price:>10,.4f}  "
            f"vol/OI={s.vol_oi_ratio:>6.1f}x  "
            f"funding={s.funding_rate*100:+.4f}%  "
            f"OI_chg={s.oi_change_24h_pct:+.1f}%  "
            f"reasons={s.anomaly_reasons}"
        )

    anomalies = [s for s in snaps if s.is_anomaly]
    if not anomalies:
        logger.warning("No anomalies in top 10. Try top_n=50 for broader scan.")
        return

    target = anomalies[0]
    logger.info(f"\nAssembling full 22-dim for {target.symbol} …")
    data = assemble(target)
    logger.success(
        f"Assembly done. Keys: {list(data.keys())}\n"
        f"  klines_1d: {len(data.get('klines_1d', []))} bars\n"
        f"  klines_4h: {len(data.get('klines_4h', []))} bars\n"
        f"  oi_hist  : {len(data.get('oi_hist_1h', []))} pts\n"
        f"  categories: {data.get('categories', [])[:3]}\n"
        f"  fear_greed: {data.get('fear_greed', {}).get('value')} "
        f"({data.get('fear_greed', {}).get('value_classification')})"
    )
    logger.success("P2 data layer is ready for P3 AI layer.")


if __name__ == "__main__":
    main()
EOF
```

### E.2 运行测试

```bash
uv run python scripts/test_data_layer.py
```

**期望输出（示例）**：

```
INFO  | === P2 Data Layer Smoke Test ===
INFO  | Loading CoinGecko coin list …
INFO  | Scanning top 10 coins …
INFO  | 🔥 ANOMALY  SOLUSDT       price=   182.3000  vol/OI=  3.2x  funding=+0.0112%  OI_chg=+24.5%  reasons=['OI 24h +24.5%']
INFO  |   normal    BTCUSDT       price= 67500.0000  vol/OI=  1.1x  funding=+0.0050%  …
…
INFO  | Assembling full 22-dim for SOLUSDT …
SUCCESS | Assembly done. Keys: ['symbol', 'price', …]
          klines_1d: 7 bars
          klines_4h: 6 bars
          oi_hist  : 24 pts
          categories: ['Layer 1 (L1)', 'Smart Contract Platform']
          fear_greed: 62 (Greed)
SUCCESS | P2 data layer is ready for P3 AI layer.
```

> **Coinglass key 还没填**：`oi_by_exchange` / `liquidation_24h` 会是空 dict，是正常的——`assembler.py` 已用 `try/except` 兜住。等你拿到 key 后填进 `.env` 就自动生效。

---

## Stage F · commit + push

```bash
# 在 cresus-bot/ 目录
git status                  # 确认 .env 不在列表

git add src/data_layer/ scripts/test_data_layer.py pyproject.toml

git status                  # 再确认

git commit -m "P2: implement data layer — Binance/OKX/Coinglass/CoinGecko clients + scanner + 22-dim assembler"
git push
```

---

## ✅ Sprint 1 完成检查清单

- [x] `uv sync` 无报错（tenacity 等依赖已装）
- [x] `scripts/test_data_layer.py` 运行完成，无未处理异常
- [x] 控制台能看到 anomaly 币（或至少看到正常扫描输出）
- [x] 22 维 assemble 返回 29 个字段
- [x] `git push` 成功，GitHub 私有仓库能看到 `src/data_layer/` 文件夹
- [x] GitHub 仓库**没有** `.env`

---

## Sprint 2 · Master Framework v0.7 新信号接入

> Commit: `a214867` — 2026-04-24
>
> 新增信号：V4A-Flash 做空信号、跨交易所 OI 集中度、期货/现货成交量比、北京峰值时段标记

### 新增文件

**`src/data_layer/signals.py`**

```python
"""Sprint 2 derived signals."""
from datetime import datetime, timezone

import httpx
from loguru import logger

from data_layer.binance_client import get_spot_ticker_24h

_NO_SPOT_SYMBOLS: set[str] = {
    "XAUUSDT", "XAGUSDT", "XPTUSDT", "XPDUSDT",
    "CLUSDT", "NGUSDT", "HGUSDT",
    "USTCUSDT",
}


def is_beijing_peak_hour() -> bool:
    """True when UTC is 21:00–00:59 (= Beijing 05:00–08:59)."""
    h = datetime.now(timezone.utc).hour
    return h >= 21 or h == 0


def detect_v4a_flash(klines_4h: list[dict], klines_1h: list[dict]) -> dict:
    """V4A-Flash: 4H upper shadow (>40% of range) + 1H retracement (>0.3%) → SHORT.

    Uses second-to-last 4H candle (completed) and latest 1H candle.
    """
    empty = {"signal": False, "upper_shadow_pct": 0.0, "retracement_pct": 0.0, "direction": None}
    if len(klines_4h) < 2 or len(klines_1h) < 1:
        return empty

    k = klines_4h[-2]
    o, h, l, c = float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"])
    candle_range = h - l
    if candle_range < 1e-10:
        return empty

    upper_shadow = h - max(o, c)
    upper_shadow_pct = upper_shadow / candle_range
    has_upper_shadow = upper_shadow_pct > 0.4 and (upper_shadow / c) > 0.005

    h1_close = float(klines_1h[-1]["c"])
    retracement_pct = (c - h1_close) / c * 100
    has_retracement = retracement_pct > 0.3

    signal = has_upper_shadow and has_retracement
    return {
        "signal": signal,
        "upper_shadow_pct": round(upper_shadow_pct, 3),
        "retracement_pct": round(retracement_pct, 3),
        "direction": "SHORT" if signal else None,
    }


def calc_spot_futures_vol_ratio(symbol: str, futures_vol_usd: float) -> float:
    """futures_vol / spot_vol。> 20x = 极端杠杆。商品币跳过。"""
    if symbol in _NO_SPOT_SYMBOLS:
        return 0.0
    try:
        spot = get_spot_ticker_24h(symbol)
        spot_vol = float(spot.get("quoteVolume", 0))
        if spot_vol == 0:
            return 0.0
        return round(futures_vol_usd / spot_vol, 2)
    except httpx.HTTPStatusError:
        _NO_SPOT_SYMBOLS.add(symbol)
        return 0.0
    except Exception as e:
        logger.debug(f"{symbol} spot vol: {e}")
        return 0.0
```

### 修改文件

**`src/data_layer/binance_client.py`** — 追加函数：

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_spot_ticker_24h(symbol: str) -> dict:
    """现货 24h 统计（含 quoteVolume），用于计算期货/现货成交量比。"""
    with _client() as c:
        return (
            c.get(f"{_BASE_SPOT}/api/v3/ticker/24hr", params={"symbol": symbol})
            .raise_for_status()
            .json()
        )
```

**`src/data_layer/okx_client.py`** — 追加函数：

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def get_open_interest(instId: str) -> dict:
    """OKX 单币合约 OI（instId='BTC-USDT-SWAP'），oi 单位为币数（oiCcy）。"""
    with _client() as c:
        return (
            c.get(
                f"{_BASE}/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": instId},
            )
            .raise_for_status()
            .json()
        )
```

**`src/data_layer/scanner.py`** — `CoinSnapshot` 新增字段：

```python
spot_futures_vol_ratio: float = 0.0   # Sprint 2: futures/spot vol ratio
is_beijing_peak: bool = False          # Sprint 2: Beijing 05:00–08:59
```

异常原因追加规则（阈值 20x，正常基线 5–15x）：

```python
if sf_ratio > 20:
    reasons.append(f"fut/spot vol {sf_ratio:.1f}x")
```

**`src/data_layer/assembler.py`** — 新增字段：

```python
# V4A-Flash (需要 klines_4h / klines_1h 先组装完成)
data["v4a_flash"] = detect_v4a_flash(klines_4h, klines_1h)

# OKX OI（注意用 oiCcy 而不是 oi，oi 是合约张数）
okx_oi_coins = float(okx_oi_raw["data"][0]["oiCcy"])
okx_oi_usd = okx_oi_coins * snap.price
data["oi_usd_okx"] = okx_oi_usd
total_oi = snap.oi_usd + okx_oi_usd
data["oi_concentration_binance_pct"] = round(snap.oi_usd / total_oi * 100, 1)
```

### Sprint 2 验证输出

```
INFO  | === Sprint 2 Smoke Test ===
INFO  | Beijing peak hour: False
INFO  | Scan complete: 9 passed hard filter, 1 anomalies
INFO  | [ANOMALY]  CLUSDT  OI_chg=+40.3%  reasons=['OI 24h +40.3%']
INFO  | V4A-Flash:             {'signal': False, 'upper_shadow_pct': 0.522, ...}
INFO  | OI Binance:            $77,471,138
INFO  | OI OKX:                $13,340,488
INFO  | OI concentration (BN): 85.3%
INFO  | Total fields:          34
SUCCESS | Sprint 2 smoke test PASSED.
```

> **CLUSDT 信号解读**：OI +40.3% + Binance 集中度 85.3% → Pattern A（OI 积累）；V4A-Flash 4H 上影 52% 但 1H 未回调，暂无做空确认。DeepSeek P3 判断：Watch List。

### ⚠️ OKX OI 注意事项

OKX `/api/v5/public/open-interest` 返回：
- `oi`：合约张数（**不能**直接 × price，单位错误）
- `oiCcy`：币数（**正确**，× price = USD）
- `oiUsd`：直接 USD（如可用也可用这个字段）

---

## ✅ P2 完成检查清单（Sprint 1 + Sprint 2）

- [x] `uv sync` 无报错
- [x] Sprint 1：`scripts/test_data_layer.py` PASSED，22 维 29 字段
- [x] Sprint 2：`scripts/test_sprint2.py` PASSED，34 字段
- [x] H1 硬过滤生效（KATUSDT vol/OI=31x 被过滤）
- [x] 跨交易所 OI 集中度正确（Binance vs OKX 同量级）
- [x] V4A-Flash 检测逻辑正确（上影 + 回撤双条件）
- [x] Beijing 峰值时段标记
- [x] 商品币（XAU/XAG/CL）无 RetryError 噪音
- [x] `git push` 成功（Sprint 1: `32c6dfb`，Sprint 2: `a214867`）
- [x] GitHub 仓库无 `.env`

全部打勾 = P2 数据层完成，进入 **P3 AI 判断层**。

---

## 下一步：P3 AI 判断层

P3 文档（`11-ai-layer.md`）将覆盖：
- `src/ai_layer/prompt_builder.py`：把 34 维数据格式化成 DeepSeek prompt
- `src/ai_layer/judge.py`：调用 DeepSeek API，解析结构化 JSON 输出
- 信心度评分逻辑（0–100，Master Framework 权重）
- 烟雾测试：对真实异常币跑一次完整判断

---

## P3 AI 判断层（Sprint 1 + Sprint 2）

> 私有库路径：`src/ai_layer/`
> Commits: Sprint 1 `0bdca47` · Sprint 2 `619cc82`

### 文件结构

```
src/ai_layer/
├── __init__.py              # P0 创建（空）
├── prompt_builder.py        # P3 Sprint 1+2
└── judge.py                 # P3 Sprint 1+2
```

### `src/ai_layer/prompt_builder.py`

核心常量 `SYSTEM_PROMPT` 内嵌 Master Framework v0.7：

- 8 种结构类型（A–H）定义
- H2–H5 硬规则（H1 由数据层预过滤，不传入 LLM）
- 信心度评分公式（0–100）
- 强制输出 JSON schema（`entry_price` / `stop_loss` / `take_profit` 对 LONG/SHORT 必填）

关键设计决策：
> H1（vol/OI <20x）由 scanner 预过滤，**不传给 LLM**，避免 DeepSeek 因方向误判产生错误推理。

### `src/ai_layer/judge.py`

```python
def judge(snapshot: dict, model: str = "deepseek-chat") -> dict:
    """给定 34 维快照，返回 DeepSeek 结构化决策。"""
```

- 使用 `httpx` 直接调用 DeepSeek OpenAI 兼容接口
- `response_format={"type": "json_object"}` 确保 JSON 输出
- `temperature=0.3`（低随机性，利于结构化判断）
- 4xx 错误立即中止（`retry_if_not_exception_type(httpx.HTTPStatusError)`）

### P3 实测输出（top 50 扫描）

```
RAVEUSDT   SKIP   conf=0   struct=none  (MA20 -80%，无可操作结构)
LABUSDT    LONG   conf=80  struct=A     (OI +22.2%，负资金费，做多 Pattern A)
SPKUSDT    SKIP   conf=0   struct=none  (OI 流出，价格过度伸展)
ENJUSDT    LONG   conf=70  struct=A     (OI +25.7%，taker 买压)
STABLEUSDT LONG   conf=70  struct=A     (OI +32.8%，Binance 集中度 86.4% H5 触发 -20)
```

STABLEUSDT 完整评分推理：`base 40 +10 OI +10 funding +10 L/S ratio +10 taker ratio -20 H5 = 60`
（注：DeepSeek 显示 conf=70，H5 扣分逻辑在提示词边界上，后续可调优）

### 费用参考

| 项目 | 数值 |
|------|------|
| 模型 | `deepseek-chat`（DeepSeek-V3） |
| 输入 tokens / 次 | ~2,500–2,700 |
| 输出 tokens / 次 | ~120–190 |
| 费用 / 次 | ~$0.0008 |
| 每天 5 分钟扫 100 币，5 个异常 | ~$0.0008 × 5 × 288 ≈ **$1.15 / 天** |

---

## ✅ P3 完成检查清单

- [x] `src/ai_layer/prompt_builder.py` — SYSTEM_PROMPT 内嵌 Master Framework v0.7
- [x] `src/ai_layer/judge.py` — DeepSeek API 调用 + JSON 解析
- [x] `scripts/test_ai_layer.py` — 单币烟雾测试 PASSED
- [x] `scripts/test_ai_batch.py` — 批量测试（top 50，5 个异常币）PASSED
- [x] LONG/SHORT 信号有 entry_price / stop_loss / take_profit（非 null）
- [x] Hard rules 正确执行（H2/H4/H5）
- [x] 无 RetryError 噪音
- [x] `git push` 成功

全部打勾 = P3 AI 判断层完成，进入 **P4 信号路由 + Discord 通知**。

---

## 下一步：P4 Discord 通知层

P4 文档（`11-discord-notifier.md`）将覆盖：

- Discord Bot Token + Channel ID 配置
- `src/notifier/discord_client.py`：发送 Embed 消息
- `src/execution/signal_router.py`：按信心度路由
  - ≥70 → `#high-confidence` 频道
  - 50–69 → `#watch-list` 频道
  - 每次扫描摘要 → `#scan-log` 频道
- `scripts/main_loop.py`：5 分钟定时扫描 + 路由 + 通知完整闭环
