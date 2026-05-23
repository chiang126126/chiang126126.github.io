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

# 支持的时间框 (供 V4 各模块引用)
TIMEFRAMES = ("1h", "4h", "1d")


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


def download_all(symbols: Iterable[str], months_back: int = 6) -> dict:
    """主入口: 下载所有 symbol × 所有 timeframe 的历史 K 线.

    Returns:
        统计 dict: {"total": N, "ok": M, "skipped": K, "failed": [(sym, tf, err), ...]}
    """
    # TODO: orchestrate fetch_klines + save_to_parquet, 含 throttle / retry
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
