"""V4 Paper Engine — Position lifecycle (phase A / B / C with partial close + trail).

跟 V3 volume_velocity_scanner.py 的 _update_paper_trades 类似, 但:
  - 时间步进单位: 15min K (vs V3 60s)
       — 15min 解决 1h K 内同根 SL/TP 顺序歧义
       — 信号决策 (开仓) 仍 1h 整点
  - SL/TP 基于 ATR(4h) (vs V3 ATR(1m))
  - Timeout: 14 天 (vs V3 4h)
  - 多 phase trail 棘轮 (A initial → B TP1+BE → C TP2+trail)

无 live trading 调用 — 纯 paper + 回测引擎复用.

Within-bar 顺序约定:
  1. 更新 HWM (LONG: high; SHORT: low)
  2. 检 timeout
  3. 单 phase step (每根 bar 最多 1 次 phase transition):
     a. 检 SL (保守先于 TP)
     b. SL 触 → close
     c. 否则检 TP → fire phase transition (新 SL 留到下根 bar 才检)

  设计取舍: 限制 1 步/bar 让 TP1 不会同 bar 触发 BE_SL (BE SL 留给下根 bar).
            多 phase 转换 (A→B→C) 需要 2-3 根 bar. 15min 粒度下这是合理保守.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from v4_signals import V4Signal


# 持仓配置 (跟 V4_SPEC 一致)
TIMEOUT_HOURS = 14 * 24            # 14 天自动平
TRAIL_ATR_MULT = 2.0               # Phase B/C trail SL = HWM ∓ 2 × ATR(4h)
PARTIAL_CLOSE_FRAC = 1.0 / 3       # TP1/TP2 各平 1/3

NOTIONAL_USDT = 400.0              # 单笔 notional ($2000 / 5 并发)
FEE_RATE = 0.0004                  # 0.04% taker (round-trip per leg = 0.08%)


@dataclass
class V4PositionLeg:
    """已平仓的一份 leg (持仓 1/3 / 2/3 / 1 的某次分批平)."""
    closed_at: pd.Timestamp
    close_price: float
    frac: float                    # 占原始 qty 的比例 (0 < frac ≤ 1)
    reason: str                    # 'hit_tp1' / 'hit_sl' / 'hit_be_sl' / 'hit_b_trail' / 'hit_tp2' / 'hit_trail' / 'hit_tp3' / 'timeout'


@dataclass
class V4Position:
    signal: V4Signal
    opened_at: pd.Timestamp
    qty: float                     # 总 qty (= NOTIONAL_USDT / entry_price)
    phase: str = "A"
    sl_price: float = 0.0          # 当前 SL (Phase A=initial, B=BE/b_trail, C=trail)
    high_water_mark: float = 0.0   # LONG: max favorable; SHORT: min favorable
    tp1_hit_at: Optional[pd.Timestamp] = None
    tp2_hit_at: Optional[pd.Timestamp] = None
    legs: list[V4PositionLeg] = field(default_factory=list)
    closed_at: Optional[pd.Timestamp] = None
    close_reason: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def remaining_frac(self) -> float:
        return max(0.0, 1.0 - sum(l.frac for l in self.legs))


# ── 开仓 ────────────────────────────────────────────────────────

def open_position(signal: V4Signal, t: pd.Timestamp) -> V4Position:
    """从 V4Signal 开仓, 返回 V4Position with SL/TP/phase A 初始化."""
    qty = NOTIONAL_USDT / signal.entry_price
    return V4Position(
        signal=signal,
        opened_at=t,
        qty=qty,
        phase="A",
        sl_price=signal.sl_price,
        high_water_mark=signal.entry_price,
    )


# ── 内部 helpers ────────────────────────────────────────────────

def _is_sl_hit(direction: str, sl_price: float, bar_low: float, bar_high: float) -> bool:
    if direction == "LONG":
        return bar_low <= sl_price
    return bar_high >= sl_price


def _is_tp_hit(direction: str, tp_price: float, bar_low: float, bar_high: float) -> bool:
    if direction == "LONG":
        return bar_high >= tp_price
    return bar_low <= tp_price


def _close_remaining(pos: V4Position, t: pd.Timestamp, price: float, reason: str) -> None:
    """平仓全部剩余 leg, 标记 position closed."""
    remaining = pos.remaining_frac
    if remaining > 0:
        pos.legs.append(V4PositionLeg(t, price, remaining, reason))
    pos.closed_at = t
    pos.close_reason = reason


def _step_phase(pos: V4Position, bar_15m: pd.Series, t: pd.Timestamp) -> bool:
    """单次 phase step: 检 SL/TP, 可能 close 或 transition.

    Returns: True if phase changed or closed (caller should loop), False if no change.
    """
    sig = pos.signal
    direction = sig.direction
    entry = sig.entry_price
    bar_high = float(bar_15m["high"])
    bar_low = float(bar_15m["low"])

    # Phase B/C: 更新 trail SL (棘轮, 只往有利方向移)
    if pos.phase == "B":
        if direction == "LONG":
            trail_target = pos.high_water_mark - TRAIL_ATR_MULT * sig.atr_4h
            pos.sl_price = max(pos.sl_price, trail_target, entry)
        else:
            trail_target = pos.high_water_mark + TRAIL_ATR_MULT * sig.atr_4h
            pos.sl_price = min(pos.sl_price, trail_target, entry)
    elif pos.phase == "C":
        if direction == "LONG":
            trail_target = pos.high_water_mark - TRAIL_ATR_MULT * sig.atr_4h
            pos.sl_price = max(pos.sl_price, trail_target)
        else:
            trail_target = pos.high_water_mark + TRAIL_ATR_MULT * sig.atr_4h
            pos.sl_price = min(pos.sl_price, trail_target)

    # 1) SL 先检 (保守)
    if _is_sl_hit(direction, pos.sl_price, bar_low, bar_high):
        # 区分 SL 原因
        if pos.phase == "A":
            reason = "hit_sl"
        elif pos.phase == "B":
            is_above_entry = (direction == "LONG" and pos.sl_price > entry) or \
                             (direction == "SHORT" and pos.sl_price < entry)
            reason = "hit_b_trail" if is_above_entry else "hit_be_sl"
        else:  # Phase C
            reason = "hit_trail"
        _close_remaining(pos, t, pos.sl_price, reason)
        return True

    # 2) TP 检 (按 phase)
    if pos.phase == "A":
        if _is_tp_hit(direction, sig.tp1_price, bar_low, bar_high):
            pos.legs.append(V4PositionLeg(t, sig.tp1_price, PARTIAL_CLOSE_FRAC, "hit_tp1"))
            pos.tp1_hit_at = t
            pos.phase = "B"
            pos.sl_price = entry   # Move to BE
            return True
    elif pos.phase == "B":
        if _is_tp_hit(direction, sig.tp2_price, bar_low, bar_high):
            pos.legs.append(V4PositionLeg(t, sig.tp2_price, PARTIAL_CLOSE_FRAC, "hit_tp2"))
            pos.tp2_hit_at = t
            pos.phase = "C"
            # SL 升级到 Phase C trail
            if direction == "LONG":
                pos.sl_price = max(pos.sl_price, pos.high_water_mark - TRAIL_ATR_MULT * sig.atr_4h)
            else:
                pos.sl_price = min(pos.sl_price, pos.high_water_mark + TRAIL_ATR_MULT * sig.atr_4h)
            return True
    elif pos.phase == "C":
        if _is_tp_hit(direction, sig.tp3_price, bar_low, bar_high):
            _close_remaining(pos, t, sig.tp3_price, "hit_tp3")
            return True

    return False   # 没变化


# ── 主入口: 推进 position 一根 15min bar ─────────────────────

def update_position(pos: V4Position, bar_15m: pd.Series, t: pd.Timestamp) -> V4Position:
    """单根 15min K 线推进 position 状态.

    SL/TP 同 bar 触发: SL 先触 (保守).
    Phase 转换每根 bar 最多 1 次 (TP1→B 后新 SL 留到下根 bar 检).
    """
    if not pos.is_open:
        return pos

    sig = pos.signal
    direction = sig.direction
    bar_high = float(bar_15m["high"])
    bar_low = float(bar_15m["low"])
    bar_close = float(bar_15m["close"])

    # 1. 更新 HWM
    if direction == "LONG":
        pos.high_water_mark = max(pos.high_water_mark, bar_high)
    else:
        pos.high_water_mark = min(pos.high_water_mark, bar_low) if pos.high_water_mark > 0 else bar_low

    # 2. Timeout 检
    if (t - pos.opened_at) >= pd.Timedelta(hours=TIMEOUT_HOURS):
        _close_remaining(pos, t, bar_close, "timeout")
        return pos

    # 3. 单 phase step (1 次 transition 上限)
    _step_phase(pos, bar_15m, t)

    return pos


# ── PnL 计算 ───────────────────────────────────────────────────

def compute_final_pnl(pos: V4Position) -> float:
    """计算 position 净 PnL (USDT, 含 fees).

    每个 leg 净 PnL = (close_price - entry) × leg_qty × side_sign - leg_notional × FEE_RATE × 2
    """
    if not pos.legs:
        return 0.0
    sig = pos.signal
    entry = sig.entry_price
    side_sign = 1 if sig.direction == "LONG" else -1

    total = 0.0
    for leg in pos.legs:
        leg_qty = pos.qty * leg.frac
        gross = (leg.close_price - entry) * leg_qty * side_sign
        leg_notional = entry * leg_qty
        fees = leg_notional * FEE_RATE * 2   # round-trip per leg
        total += gross - fees
    return total
