"""V4 Backtest Engine — 历史数据上跑 V4 策略, 输出 PF / 胜率 / drawdown.

主循环 (跨 symbol portfolio, time-stepped 15min):
  for t in time_range (15min step):
    1. update_position(open_positions, bar_15m[t])  # 收 SL/TP, 推进 phase
                                                     # 15min 精度避免 1h K 内 SL/TP 顺序歧义
    2. 如果 t 是 1h 整点 + concurrent < MAX:
       sigs = check_all_signals(...)
       if sigs[0].conviction >= 6: open_position()
       # 信号决策仍是 1h 粒度 (用 1h+4h+1d K 线), 15min 仅做执行精度

跨 symbol 共享: 1 个 portfolio state (max 5 concurrent across all symbols).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable
import pandas as pd

from v4_data_fetcher import load_klines
from v4_regime import regime_series
from v4_signals import check_all_signals, V4Signal
from v4_conviction import compute_conviction
from v4_paper_engine import V4Position, open_position, update_position, compute_final_pnl


MAX_CONCURRENT = 5             # 跨所有 symbol 总并发 (跟 V4 设计 $2000 / $400 一致)
MIN_CONVICTION = 6             # Diamond 阈值


@dataclass
class BacktestResult:
    trades: list[V4Position] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)   # date → cumulative pnl

    def stats(self) -> dict:
        """计算 PF / 胜率 / drawdown / Sharpe."""
        # TODO
        raise NotImplementedError


def run_backtest(
    symbols: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> BacktestResult:
    """主入口.

    Returns:
        BacktestResult with trades + daily_pnl + stats()
    """
    # 1. 拉 BTC 1h, 算 regime series 一次性 (复用全 symbol)
    # TODO: btc_1h = load_klines("BTCUSDT", "1h"); btc_regime = regime_series(btc_1h)

    # 2. 跨 symbol 时间步进 (按 1h tick 推进)
    # TODO: for t in pd.date_range(start, end, freq="1h"):
    #         for sym in symbols:
    #             load_klines + check + open / update

    raise NotImplementedError


def export_csv(result: BacktestResult, output_path: str) -> None:
    """每笔 trade 导 CSV: entry_ts, symbol, direction, sub_strategy, regime, conviction,
       entry_price, exit_price, pnl_usdt, hold_hours, close_reason."""
    # TODO
    raise NotImplementedError


def group_metrics(result: BacktestResult, by: str = "regime") -> dict:
    """按维度切片 metrics. by = 'regime' | 'conviction' | 'symbol' | 'sub_strategy'."""
    # TODO
    raise NotImplementedError


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V4 backtest runner")
    parser.add_argument("--symbols", nargs="*", help="symbol list")
    parser.add_argument("--start", default="2025-11-24", help="start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-24", help="end date YYYY-MM-DD")
    parser.add_argument("--output", default="v4_backtest_result.csv")
    args = parser.parse_args()

    result = run_backtest(
        symbols=args.symbols or ["BTCUSDT"],
        start=pd.Timestamp(args.start, tz="UTC"),
        end=pd.Timestamp(args.end, tz="UTC"),
    )
    export_csv(result, args.output)
    print("=== Overall ===")
    print(result.stats())
    print("=== By regime ===")
    print(group_metrics(result, by="regime"))
    print("=== By conviction ===")
    print(group_metrics(result, by="conviction"))
