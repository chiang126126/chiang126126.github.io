"""V4 Data Fetcher — 6 月历史 K 线 (15m / 1h / 4h / 1d) + Funding + OI + Taker.

输入: V3 paper history 接触过的 237 symbol.
输出: ~/cresus-bot/v4_{klines,funding,oi,taker}/{symbol}_*.parquet

不依赖 V3 binance_client.py (V3 client 含交易逻辑, V4 数据下载只读公开端点用 requests 直打).

约束:
- Binance perp /fapi/v1/klines: 1500 行/请求, weight=1, 2400 weight/min (futures public)
- Throttle: 0.1s 间隔 → 600 req/min, 在 weight 限制内
- 失败重试 3 次指数退避 (2s, 4s, 8s)
- Symbol 下架 → log 跳过

K 线分页 (单次最多 1500 行):
- 6 月 1d:  180 行 (1 req)
- 6 月 4h:  1080 行 (1 req)
- 6 月 1h:  4320 行 (3 req)
- 6 月 15m: 17280 行 (12 req)

OI / Taker ratio Binance 限制: 仅支持 30 日内查询.
  → 6 月需分 6 段拉, 每段 30 日.
  → 实际上 V4 conviction 用 OI/Taker 时只用最近 30 日数据 (live 阶段会持续 fetch).
  → 回测时 OI/Taker 早于 30 日的 trade, conviction 加分项跳过 (老 trade fallback None).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

# Binance perp public endpoints (no auth)
BINANCE_FAPI = "https://fapi.binance.com"
KLINES_ENDPOINT = "/fapi/v1/klines"
FUNDING_ENDPOINT = "/fapi/v1/fundingRate"
OI_HIST_ENDPOINT = "/futures/data/openInterestHist"
TAKER_RATIO_ENDPOINT = "/futures/data/takerlongshortRatio"

# 缓存目录
V4_KLINES_DIR = Path.home() / "cresus-bot" / "v4_klines"
V4_FUNDING_DIR = Path.home() / "cresus-bot" / "v4_funding"
V4_OI_DIR = Path.home() / "cresus-bot" / "v4_oi"
V4_TAKER_DIR = Path.home() / "cresus-bot" / "v4_taker"

# K 线时间框
TIMEFRAMES = ("15m", "1h", "4h", "1d")

# Funding / OI / Taker 时间窗
FUNDING_LOOKBACK_DAYS = 7
OI_INTERVAL = "4h"
TAKER_INTERVAL = "4h"

# Throttle + retry
THROTTLE_SEC = 0.1                # 100ms 间隔 → 600 req/min
MAX_RETRIES = 3
INITIAL_BACKOFF_SEC = 2.0

# Binance API 单次行数上限
KLINES_MAX_LIMIT = 1500
OI_MAX_LIMIT = 500
TAKER_MAX_LIMIT = 500
FUNDING_MAX_LIMIT = 1000

# OI / Taker 仅支持 30 日内查询
OI_MAX_LOOKBACK_DAYS = 30
TAKER_MAX_LOOKBACK_DAYS = 30


# ── Prototype symbol 集合 (Day 3 决策点用) ──────────────────────────
# Day 3 prototype 跑这 15 个 symbol 验证 V4 信号假设是否成立.
# 全量 237 symbol 等 PF > 1.3 验证后再跑.

PROTOTYPE_MAINSTREAM = (
    # 10 个 top liquid perp, 流动性 + 数据干净
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
)

V3_LIVE_WHITELIST = (
    # V3 live_trader.py LIVE_SYMBOL_WHITELIST (Phase 6 实盘允许币种)
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
)

V3_LIVE_BLACKLIST = (
    # V3 live_trader.py LIVE_SYMBOL_BLACKLIST (实盘 0 胜被禁的币)
    # V4 在这些上跑能验证: day-scale 是否能"救回" 短周期失败的 symbol?
    "DODOXUSDT",   # 4 笔 0 胜
    "NMRUSDT",     # 4 笔 0 胜
    "PLAYUSDT",    # 4 笔 0 胜
    "GUAUSDT",     # 3 笔 0 胜
    "STABLEUSDT",  # 5 笔 0 胜
)


def list_prototype_symbols() -> list[str]:
    """Day 3 prototype 用的 15 个 symbol (10 mainstream + 5 V3 黑名单).

    V3 whitelist (BTC/ETH/SOL) 已包含在 mainstream 里, 不重复.
    黑名单加入意图: 验证 V4 day-scale 是否能在 V3 已证伪的 symbol 上找到 edge.
    """
    return sorted(set(PROTOTYPE_MAINSTREAM) | set(V3_LIVE_BLACKLIST))


# ── 通用工具 ──────────────────────────────────────────────────────────

def _ms(dt: datetime) -> int:
    """datetime → epoch ms."""
    return int(dt.timestamp() * 1000)


def _http_get(path: str, params: dict, retries: int = MAX_RETRIES) -> list | dict:
    """带 throttle + retry 的 GET. 公开端点, 无需 auth."""
    url = f"{BINANCE_FAPI}{path}"
    backoff = INITIAL_BACKOFF_SEC
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            time.sleep(THROTTLE_SEC)
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
            else:
                raise
    raise RuntimeError(f"unreachable: {last_err}")


# ── Symbol 列表 ──────────────────────────────────────────────────────

def list_v3_symbols(paper_history_path: Path) -> list[str]:
    """从 V3 paper_trades_history.json 抽取所有接触过的 symbol.

    注意: V3 paper engine 用 1m 量价突变信号, 主流币 (BTC/ETH/SOL) 流动性大不被触发,
    所以 V3 paper history 不含主流币. V4 默认 universe 用 list_default_universe() 补.
    """
    with open(paper_history_path) as f:
        data = json.load(f)
    syms: set[str] = set()
    for bucket in ("recent_closed", "open_trades"):
        for trade in data.get(bucket, []) or []:
            sym = trade.get("symbol")
            if sym:
                syms.add(sym)
    return sorted(syms)


def list_default_universe(paper_history_path: Path) -> list[str]:
    """V4 默认下载/回测 universe.

    = V3 paper ∪ Mainstream (10) ∪ V3 黑名单 (5)
    确保主流币 (BTC/ETH/SOL/BNB/...) 一定在 — V4 day-scale 用 BTC 算 regime,
    用主流币作长持目标. V3 paper 不含主流币 (1m 量价突变信号过滤掉了).
    """
    paper_syms = set(list_v3_symbols(paper_history_path))
    mainstream = set(PROTOTYPE_MAINSTREAM)
    blacklist = set(V3_LIVE_BLACKLIST)
    return sorted(paper_syms | mainstream | blacklist)


# ── K 线拉取 ────────────────────────────────────────────────────────

def _klines_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    """单次拉 K 线 (最多 KLINES_MAX_LIMIT 行)."""
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": KLINES_MAX_LIMIT,
    }
    data = _http_get(KLINES_ENDPOINT, params)
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected response for {symbol} {interval}: {data}")
    return data


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    """拉指定时间区间的 K 线, 自动分页.

    Returns:
        list of raw kline rows (12-element lists, 跟 Binance 原始格式).
    """
    all_rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        chunk = _klines_chunk(symbol, interval, cursor, end_ms)
        if not chunk:
            break
        all_rows.extend(chunk)
        last_close = int(chunk[-1][6])
        next_cursor = last_close + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(chunk) < KLINES_MAX_LIMIT:
            break
    return all_rows


def klines_to_df(rows: list[list]) -> pd.DataFrame:
    """Raw Binance kline rows → DataFrame (typed, indexed by open_time UTC)."""
    if not rows:
        return pd.DataFrame()
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trade_count",
        "taker_buy_base", "taker_buy_quote", "_ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"):
        df[c] = df[c].astype(float)
    df["trade_count"] = df["trade_count"].astype(int)
    df = df.drop(columns=["_ignore"])
    df = df.set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def save_klines_parquet(symbol: str, interval: str, df: pd.DataFrame) -> Path:
    """存 DataFrame 到 V4_KLINES_DIR/{symbol}_{interval}.parquet."""
    V4_KLINES_DIR.mkdir(parents=True, exist_ok=True)
    path = V4_KLINES_DIR / f"{symbol}_{interval}.parquet"
    df.to_parquet(path, compression="snappy")
    return path


def load_klines(symbol: str, interval: str) -> pd.DataFrame:
    """读 K 线缓存."""
    path = V4_KLINES_DIR / f"{symbol}_{interval}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Funding rate ─────────────────────────────────────────────────────

def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """拉 funding rate 历史 (8h 一条). 无 30 日限制."""
    all_rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol.upper(),
            "startTime": cursor,
            "endTime": end_ms,
            "limit": FUNDING_MAX_LIMIT,
        }
        data = _http_get(FUNDING_ENDPOINT, params)
        if not isinstance(data, list) or not data:
            break
        all_rows.extend(data)
        last_t = int(data[-1]["fundingTime"])
        if last_t + 1 <= cursor:
            break
        cursor = last_t + 1
        if len(data) < FUNDING_MAX_LIMIT:
            break
    return all_rows


def funding_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["funding_time"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float)
    if "markPrice" in df.columns:
        df["mark_price"] = df["markPrice"].astype(float)
    else:
        df["mark_price"] = 0.0
    df = df[["funding_time", "funding_rate", "mark_price"]].set_index("funding_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def save_funding_parquet(symbol: str, df: pd.DataFrame) -> Path:
    V4_FUNDING_DIR.mkdir(parents=True, exist_ok=True)
    path = V4_FUNDING_DIR / f"{symbol}.parquet"
    df.to_parquet(path, compression="snappy")
    return path


def load_funding(symbol: str) -> pd.DataFrame:
    path = V4_FUNDING_DIR / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Open Interest history ────────────────────────────────────────────
# Binance 限制 (2026 verified):
#   - 仅最近 30 日数据可查
#   - startTime/endTime range 必须 *严格* < 30 days (= 30d 也会 400)
# 简化处理: 不传 time params, 单次拿最新 limit=500 (max), Binance 自动 cap 在 30d.
# 返回 ~180 行 (30d × 24h / 4h) for 4h interval.

def fetch_oi_hist(symbol: str, start_ms: int = 0, end_ms: int = 0, interval: str = OI_INTERVAL) -> list[dict]:
    """拉 OI 历史 (最近 30 日, ~180 行 at 4h).

    start_ms / end_ms 参数保留为 backward-compat, 实际忽略.
    Binance 仅存 30 日数据, 无法补更早.
    """
    params = {
        "symbol": symbol.upper(),
        "period": interval,
        "limit": OI_MAX_LIMIT,
    }
    try:
        data = _http_get(OI_HIST_ENDPOINT, params)
    except Exception as e:
        log.warning(f"OI fetch failed for {symbol}: {e}")
        return []
    return data if isinstance(data, list) else []


def oi_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["sum_oi"] = df["sumOpenInterest"].astype(float)
    df["sum_oi_value"] = df["sumOpenInterestValue"].astype(float)
    df = df[["ts", "sum_oi", "sum_oi_value"]].set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def save_oi_parquet(symbol: str, df: pd.DataFrame) -> Path:
    V4_OI_DIR.mkdir(parents=True, exist_ok=True)
    path = V4_OI_DIR / f"{symbol}_{OI_INTERVAL}.parquet"
    df.to_parquet(path, compression="snappy")
    return path


def load_oi(symbol: str) -> pd.DataFrame:
    path = V4_OI_DIR / f"{symbol}_{OI_INTERVAL}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── Taker buy ratio ──────────────────────────────────────────────────
# 同 OI: Binance 限制 30 日 + range 严格 < 30d.
# 不传 time params, 单次拿最新 500 行.

def fetch_taker_ratio(symbol: str, start_ms: int = 0, end_ms: int = 0, interval: str = TAKER_INTERVAL) -> list[dict]:
    """拉 taker buy/sell ratio (最近 30 日)."""
    params = {
        "symbol": symbol.upper(),
        "period": interval,
        "limit": TAKER_MAX_LIMIT,
    }
    try:
        data = _http_get(TAKER_RATIO_ENDPOINT, params)
    except Exception as e:
        log.warning(f"taker fetch failed for {symbol}: {e}")
        return []
    return data if isinstance(data, list) else []


def taker_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["buy_sell_ratio"] = df["buySellRatio"].astype(float)
    df["buy_vol"] = df["buyVol"].astype(float)
    df["sell_vol"] = df["sellVol"].astype(float)
    # 派生 taker buy ratio = buy / (buy+sell), 用 0-1 区间 (跟 V4_SPEC §2.4 阈值 0.55/0.45 对齐)
    total = (df["buy_vol"] + df["sell_vol"]).replace(0, float("nan"))
    df["taker_buy_ratio"] = df["buy_vol"] / total
    df = df[["ts", "buy_sell_ratio", "buy_vol", "sell_vol", "taker_buy_ratio"]].set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def save_taker_parquet(symbol: str, df: pd.DataFrame) -> Path:
    V4_TAKER_DIR.mkdir(parents=True, exist_ok=True)
    path = V4_TAKER_DIR / f"{symbol}_{TAKER_INTERVAL}.parquet"
    df.to_parquet(path, compression="snappy")
    return path


def load_taker(symbol: str) -> pd.DataFrame:
    path = V4_TAKER_DIR / f"{symbol}_{TAKER_INTERVAL}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


# ── 主入口 ──────────────────────────────────────────────────────────

def download_one_symbol(symbol: str, start_ms: int, end_ms: int,
                        timeframes: tuple = TIMEFRAMES,
                        include_funding: bool = True,
                        include_oi: bool = True,
                        include_taker: bool = True) -> dict:
    """下载单 symbol 全部数据.

    Returns:
        {"ok": [...], "skipped": [...], "failed": [(kind, err), ...], "rows": {...}}
    """
    result: dict = {"ok": [], "skipped": [], "failed": [], "rows": {}}

    for tf in timeframes:
        try:
            rows = fetch_klines(symbol, tf, start_ms, end_ms)
            df = klines_to_df(rows)
            if df.empty:
                result["skipped"].append(f"klines_{tf}")
                continue
            save_klines_parquet(symbol, tf, df)
            result["ok"].append(f"klines_{tf}")
            result["rows"][f"klines_{tf}"] = len(df)
        except Exception as e:
            result["failed"].append((f"klines_{tf}", str(e)))

    if include_funding:
        try:
            rows = fetch_funding(symbol, start_ms, end_ms)
            df = funding_to_df(rows)
            if not df.empty:
                save_funding_parquet(symbol, df)
                result["ok"].append("funding")
                result["rows"]["funding"] = len(df)
            else:
                result["skipped"].append("funding")
        except Exception as e:
            result["failed"].append(("funding", str(e)))

    if include_oi:
        try:
            rows = fetch_oi_hist(symbol, start_ms, end_ms)
            df = oi_to_df(rows)
            if not df.empty:
                save_oi_parquet(symbol, df)
                result["ok"].append("oi")
                result["rows"]["oi"] = len(df)
            else:
                result["skipped"].append("oi")
        except Exception as e:
            result["failed"].append(("oi", str(e)))

    if include_taker:
        try:
            rows = fetch_taker_ratio(symbol, start_ms, end_ms)
            df = taker_to_df(rows)
            if not df.empty:
                save_taker_parquet(symbol, df)
                result["ok"].append("taker")
                result["rows"]["taker"] = len(df)
            else:
                result["skipped"].append("taker")
        except Exception as e:
            result["failed"].append(("taker", str(e)))

    return result


def download_all(symbols: Iterable[str], months_back: int = 6,
                 timeframes: tuple = TIMEFRAMES,
                 include_funding: bool = True,
                 include_oi: bool = True,
                 include_taker: bool = True) -> dict:
    """主入口: 下载所有 symbol × 所有数据.

    返回 stats dict 含 per-category 计数 (klines/funding/oi/taker), 方便 debug.
    """
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=months_back * 30)
    end_ms, start_ms = _ms(end), _ms(start)

    stats: dict = {
        "start": start.isoformat(), "end": end.isoformat(),
        "symbols": 0, "ok": 0, "partial": 0, "failed_all": [],
        "kline_ok": 0, "funding_ok": 0, "oi_ok": 0, "taker_ok": 0,
        "by_symbol": {},
    }
    symbols = list(symbols)
    stats["symbols"] = len(symbols)

    for i, sym in enumerate(symbols, 1):
        r = download_one_symbol(sym, start_ms, end_ms,
                                timeframes=timeframes,
                                include_funding=include_funding,
                                include_oi=include_oi,
                                include_taker=include_taker)
        stats["by_symbol"][sym] = r
        if not r["failed"]:
            stats["ok"] += 1
        elif r["ok"]:
            stats["partial"] += 1
        else:
            stats["failed_all"].append(sym)
        # 分类计数
        if any(k.startswith("klines_") for k in r["ok"]):
            stats["kline_ok"] += 1
        if "funding" in r["ok"]:
            stats["funding_ok"] += 1
        if "oi" in r["ok"]:
            stats["oi_ok"] += 1
        if "taker" in r["ok"]:
            stats["taker_ok"] += 1
        if i % 10 == 0:
            log.info(f"progress: {i}/{len(symbols)} ({i/len(symbols)*100:.0f}%)")

    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V4 historical data downloader")
    parser.add_argument("--symbols", nargs="*", help="symbol list")
    parser.add_argument("--prototype", action="store_true",
                        help="只拉 prototype 15 symbol (Day 3 验证用)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--paper-history", type=Path,
                        default=Path.home() / "cresus-bot" / "paper_trades_history.json")
    parser.add_argument("--skip-klines", action="store_true",
                        help="跳过 K 线 (K 线已下过, 仅补 funding/OI/taker, 快很多)")
    parser.add_argument("--skip-funding", action="store_true", help="跳过 funding")
    parser.add_argument("--skip-oi", action="store_true", help="跳过 OI")
    parser.add_argument("--skip-taker", action="store_true", help="跳过 taker")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Symbol universe 默认: V3 paper ∪ Mainstream ∪ V3 黑名单 (含 BTC/ETH/SOL 等主流币)
    if args.prototype:
        syms = list_prototype_symbols()
        print(f"[prototype mode] 15 symbol = 10 mainstream + 5 V3 blacklist")
    elif args.symbols:
        syms = args.symbols
        print(f"[explicit symbols] {len(syms)} from CLI")
    else:
        syms = list_default_universe(args.paper_history)
        print(f"[default universe] {len(syms)} = V3 paper ∪ Mainstream ∪ V3 blacklist")

    tfs = () if args.skip_klines else TIMEFRAMES
    print(f"下载 {len(syms)} symbol × {len(tfs)} timeframe × {args.months} 月 "
          f"(funding={'skip' if args.skip_funding else 'on'}, "
          f"oi={'skip' if args.skip_oi else 'on'}, "
          f"taker={'skip' if args.skip_taker else 'on'})")
    stats = download_all(syms, months_back=args.months, timeframes=tfs,
                         include_funding=not args.skip_funding,
                         include_oi=not args.skip_oi,
                         include_taker=not args.skip_taker)
    print(json.dumps({k: v for k, v in stats.items() if k != "by_symbol"}, indent=2, default=str))
