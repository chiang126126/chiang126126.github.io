"""V4 Paper Engine — Position lifecycle management (phase A / B / C trail).

跟 V3 volume_velocity_scanner.py 的 _update_paper_trades 类似, 但:
  - 时间步进单位: 1h (vs V3 60s)
  - SL/TP 基于 ATR(4h) (vs V3 ATR(1m))
  - Timeout: 14 天 (vs V3 4h)
  - 多 phase trail 逻辑保留 (A→B→C 棘轮)

无 live trading 调用 — 纯 paper 端 + 回测引擎复用.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from v4_signals import V4Signal


# 持仓配置 (跟 V4_SPEC 一致)
ATR_SL_MULT_4H = 2.0           # SL = entry ∓ 2.0 × ATR(4h) ≈ 5%
ATR_TP1_MULT_4H = 2.0          # TP1 = entry ± 2.0 × ATR(4h)
ATR_TP2_MULT_4H = 4.0          # TP2 = entry ± 4.0 × ATR(4h)
ATR_TP3_MULT_4H = 6.0          # TP3 = entry ± 6.0 × ATR(4h)
TIMEOUT_HOURS = 14 * 24        # 14 天自动平
TRAIL_ATR_MULT = 2.0           # Phase B/C trail SL = HWM ∓ 2 × ATR(4h)

NOTIONAL_USDT = 400.0          # 单笔 notional (跟 paper $400 配 live $2000/5 一致)
FEE_RATE = 0.0004              # 0.04% taker (round-trip = 0.08%)
FUNDING_RATE_PER_8H = 0.0001   # 0.01% / 8h (估算, 1x 杠杆)


@dataclass
class V4Position:
    signal: V4Signal
    opened_at: pd.Timestamp
    qty: float                   # base 数量
    phase: str = "A"             # "A" / "B" / "C"
    sl_price: float = 0.0        # 当前 SL (Phase A=initial, B=BE, C=trail)
    high_water_mark: float = 0.0
    tp1_hit_at: Optional[pd.Timestamp] = None
    tp2_hit_at: Optional[pd.Timestamp] = None
    realized_pnl_pct: float = 0.0   # 分批了利的累计 PnL%
    realized_legs: list = field(default_factory=list)   # [(ts, price, frac, reason)]
    closed_at: Optional[pd.Timestamp] = None
    close_reason: Optional[str] = None
    final_pnl_usdt: Optional[float] = None


def open_position(signal: V4Signal, t: pd.Timestamp) -> V4Position:
    """从 Signal 开仓, 返回 V4Position with SL/TP/phase A 初始化."""
    # TODO: qty = NOTIONAL_USDT / entry_price; sl_price = signal.sl_price; phase = "A"
    raise NotImplementedError


def update_position(pos: V4Position, bar_1h: pd.Series, t: pd.Timestamp) -> V4Position:
    """单根 1h K 线推进 position 状态.

    Phase A: 检 SL / TP1 (3 等分一份, 33% 仓位平). 触 TP1 → 进 phase B.
    Phase B: SL 移到 entry (BE). 检 BE_SL / TP2 / trail_b. 触 TP2 → 进 phase C.
    Phase C: trail SL = max(prev_sl, HWM ∓ 2×ATR). 检 trail_sl / TP3.
    任何 phase: 检 timeout (14d).
    """
    # TODO: 实现 phase 转移 + SL trail + close 判断
    raise NotImplementedError


def compute_final_pnl(pos: V4Position) -> float:
    """累加 realized_legs 净 PnL (USDT, 含 fees)."""
    # TODO: sum legs + entry/close fees
    raise NotImplementedError
