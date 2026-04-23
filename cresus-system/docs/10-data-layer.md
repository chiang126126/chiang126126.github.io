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

## ✅ P2 完成检查清单

- [ ] `uv sync` 无报错（tenacity 等依赖已装）
- [ ] `scripts/test_data_layer.py` 运行完成，无未处理异常
- [ ] 控制台能看到 anomaly 币（或至少看到正常扫描输出）
- [ ] 22 维 assemble 返回 `klines_1d: 7 bars`、`klines_4h: 6 bars`
- [ ] `git push` 成功，GitHub 私有仓库能看到 `src/data_layer/` 文件夹
- [ ] GitHub 仓库**没有** `.env`

全部打勾 = P2 数据层完成，可以进入 **P3 AI 判断层**（DeepSeek prompt 工程）。

---

## 下一步：P3 AI 判断层

P3 文档（`10-ai-layer.md`）将覆盖：
- `src/ai_layer/prompt_builder.py`：把 22 维数据格式化成 DeepSeek prompt
- `src/ai_layer/judge.py`：调用 DeepSeek API，解析结构化输出
- 信心度评分逻辑
- 烟雾测试：对真实异常币跑一次完整判断
