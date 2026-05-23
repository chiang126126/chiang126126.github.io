"""V4 Data Fetcher — 6 月历史 K 线 (1h / 4h / 1d) from Binance perp.

输入: V3 paper history 接触过的 237 symbol (来自 paper_trades_history.json).
输出: ~/cresus-bot/v4_klines/{symbol}_{interval}.parquet

设计:
- 复用 binance_client.py get_klines (V3 已经实现, 含 rate limit + retry)
- 每 symbol 拉 6 月数据: 1h × 4320 + 4h × 1080 + 1d × 180 = ~5580 行
- Parquet 列: open_time, open, high, low, close, volume, close_time, quote_volume,
              trade_count, taker_buy_base, taker_buy_quote
- 增量更新: 已有缓存只拉新数据, 文件存在时跳过完整下载

约束:
- Binance perp /fapi/v1/klines: 1000 行/请求, weight=1, 1200 weight/min
- 237 symbol × 3 timeframe × 平均 5 请求 = 3555 请求 ≈ 3min 全量下载
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# 缓存目录 (跟 ~/cresus-bot/ 数据目录平级)
V4_KLINES_DIR = Path.home() / "cresus-bot" / "v4_klines"
V4_FUNDING_DIR = Path.home() / "cresus-bot" / "v4_funding"
V4_OI_DIR = Path.home() / "cresus-bot" / "v4_oi"
V4_TAKER_DIR = Path.home() / "cresus-bot" / "v4_taker"

# K 线时间框
#   15m: SL/TP 精度层 (回测引擎用, 解决 1h K 内同时穿 SL/TP 的歧义)
#   1h:  Regime 检测 + 信号触发 lookup
#   4h:  ATR / MACD / 信号确认
#   1d:  Donchian / volume MA / 周线趋势
TIMEFRAMES = ("15m", "1h", "4h", "1d")

# Funding / OI / Taker 时间窗 (V4 day-scale, 不用 V3 的瞬时/5m 粒度)
FUNDING_LOOKBACK_DAYS = 7        # 取 7d funding 序列, 算当前 + 平均
OI_INTERVAL = "4h"               # OI 历史粒度 (Binance 提供 5m/15m/30m/1h/2h/4h/6h/12h/1d)
TAKER_INTERVAL = "4h"            # taker buy ratio 粒度


def list_v3_symbols(paper_history_path: Path) -> list[str]:
    """从 V3 paper_trades_history.json 抽取所有接触过的 symbol."""
    with open(paper_history_path) as f:
        data = json.load(f)
    syms: set[str] = set()
    for bucket in ("recent_closed", "open_trades"):
        for trade in data.get(bucket, []) or []:
            sym = trade.get("symbol")
            if sym:
                syms.add(sym)
    return sorted(syms)


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    """从 Binance perp /fapi/v1/klines 拉历史 K 线.

    封装 rate limit + 分页 (>1000 行自动多次请求).
    """
    # TODO: 调 binance_client.get_klines (V3 已有), 处理分页
    raise NotImplementedError


def save_to_parquet(symbol: str, interval: str, klines: list[list]) -> Path:
    """存到 V4_KLINES_DIR/{symbol}_{interval}.parquet."""
    # TODO: 用 pandas + pyarrow 写 parquet
    raise NotImplementedError


def load_klines(symbol: str, interval: str):
    """读缓存 parquet → DataFrame.

    Returns:
        pandas.DataFrame with columns: open_time, open, high, low, close, volume, ...
        Indexed by open_time (UTC).
    """
    # TODO: pd.read_parquet
    raise NotImplementedError


def fetch_funding(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """Binance /fapi/v1/fundingRate — 8h 一条记录.

    Returns: list of {fundingTime, fundingRate, markPrice}
    """
    # TODO
    raise NotImplementedError


def fetch_oi_hist(symbol: str, start_ms: int, end_ms: int, interval: str = OI_INTERVAL) -> list[dict]:
    """Binance /futures/data/openInterestHist — 4h 一条 (含 sumOpenInterest, sumOpenInterestValue).

    注意: 该端点仅支持 30 日内的数据查询, 跨大区间需分段请求.
    """
    # TODO
    raise NotImplementedError


def fetch_taker_ratio(symbol: str, start_ms: int, end_ms: int, interval: str = TAKER_INTERVAL) -> list[dict]:
    """Binance /futures/data/takerlongshortRatio — 4h 一条 (含 buySellRatio, buyVol, sellVol)."""
    # TODO
    raise NotImplementedError


def load_funding(symbol: str):
    """读 funding 缓存 parquet → DataFrame."""
    raise NotImplementedError


def load_oi(symbol: str):
    """读 OI 缓存 parquet → DataFrame."""
    raise NotImplementedError


def load_taker(symbol: str):
    """读 taker ratio 缓存 parquet → DataFrame."""
    raise NotImplementedError


def download_all(symbols: Iterable[str], months_back: int = 6) -> dict:
    """主入口: 下载所有 symbol × 所有 timeframe + funding + OI + taker.

    Returns:
        统计 dict: {
            "total_klines": N, "ok_klines": M, "skipped_klines": K,
            "ok_funding": ..., "ok_oi": ..., "ok_taker": ...,
            "failed": [(sym, kind, err), ...]
        }
    """
    # TODO: orchestrate 4 个 timeframe K 线 + funding + OI + taker, 含 throttle / retry
    raise NotImplementedError


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V4 historical kline downloader")
    parser.add_argument("--symbols", nargs="*", help="symbol list, 默认从 V3 paper 抽")
    parser.add_argument("--months", type=int, default=6, help="回看月数 (默认 6)")
    parser.add_argument("--paper-history", type=Path,
                        default=Path.home() / "cresus-bot" / "paper_trades_history.json")
    args = parser.parse_args()

    if args.symbols:
        syms = args.symbols
    else:
        syms = list_v3_symbols(args.paper_history)
    print(f"下载 {len(syms)} symbol × {len(TIMEFRAMES)} timeframe × {args.months} 月...")
    stats = download_all(syms, months_back=args.months)
    print(json.dumps(stats, indent=2))
