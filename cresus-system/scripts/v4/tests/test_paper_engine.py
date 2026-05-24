"""V4 paper_engine tests — phase 转换 / partial close / trail / timeout / PnL."""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v4_paper_engine import (
    V4Position, V4PositionLeg,
    open_position, update_position, compute_final_pnl,
    TIMEOUT_HOURS, TRAIL_ATR_MULT, PARTIAL_CLOSE_FRAC, NOTIONAL_USDT, FEE_RATE,
)
from v4_signals import V4Signal


# ── fixture ──────────────────────────────────────────────────────

def _long_signal(t_open=None, entry=100.0, atr=1.0):
    """LONG 信号: entry=100, ATR=1, SL=98, TP1=102, TP2=104, TP3=106."""
    if t_open is None:
        t_open = pd.Timestamp("2026-02-01 00:00", tz="UTC")
    return V4Signal(
        symbol="ETHUSDT", direction="LONG", sub_strategy="breakout_long",
        entry_time=t_open, entry_price=entry,
        sl_price=entry - 2 * atr, tp1_price=entry + 2 * atr,
        tp2_price=entry + 4 * atr, tp3_price=entry + 6 * atr,
        atr_4h=atr, btc_regime="up", features={},
    )


def _short_signal(t_open=None, entry=100.0, atr=1.0):
    """SHORT 信号: entry=100, ATR=1, SL=102, TP1=98, TP2=96, TP3=94."""
    if t_open is None:
        t_open = pd.Timestamp("2026-02-01 00:00", tz="UTC")
    return V4Signal(
        symbol="ETHUSDT", direction="SHORT", sub_strategy="breakout_short",
        entry_time=t_open, entry_price=entry,
        sl_price=entry + 2 * atr, tp1_price=entry - 2 * atr,
        tp2_price=entry - 4 * atr, tp3_price=entry - 6 * atr,
        atr_4h=atr, btc_regime="down", features={},
    )


def _bar(open_, high, low, close, volume=1000.0):
    """构造一根 15min K 的 Series."""
    return pd.Series({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


# ── open_position ───────────────────────────────────────────────

def test_open_position_basic():
    sig = _long_signal()
    t = sig.entry_time
    pos = open_position(sig, t)
    assert pos.is_open
    assert pos.phase == "A"
    assert pos.sl_price == 98.0
    assert pos.qty == NOTIONAL_USDT / 100.0   # 4.0
    assert pos.high_water_mark == 100.0
    assert len(pos.legs) == 0


# ── Phase A: SL hit ─────────────────────────────────────────────

def test_long_phase_a_sl_hit():
    """LONG bar_low ≤ SL 98 → close all, hit_sl."""
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 101, 97, 98), t1)
    assert not pos.is_open
    assert pos.close_reason == "hit_sl"
    assert len(pos.legs) == 1
    assert pos.legs[0].frac == 1.0   # close all
    assert pos.legs[0].close_price == 98.0


def test_short_phase_a_sl_hit():
    sig = _short_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 103, 99, 102), t1)
    assert not pos.is_open
    assert pos.close_reason == "hit_sl"
    assert pos.legs[0].close_price == 102.0


# ── Phase A → B: TP1 hit ───────────────────────────────────────

def test_long_phase_a_tp1_hit_transitions_to_b():
    """LONG bar_high ≥ TP1 102 → 平 1/3, phase=B, sl=entry.

    bar_low > entry (100.5) 避免同 bar BE SL 误触 (单 step 也不会, 这里 reinforces 测试明确性).
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 103, 100.5, 102), t1)
    assert pos.is_open
    assert pos.phase == "B"
    assert pos.sl_price == 100.0   # BE
    assert len(pos.legs) == 1
    assert pos.legs[0].reason == "hit_tp1"
    assert pos.legs[0].frac == PARTIAL_CLOSE_FRAC
    assert pos.legs[0].close_price == 102.0
    assert pos.tp1_hit_at == t1


# ── Phase A: SL + TP1 both 触, SL wins (保守) ──────────────────

def test_long_phase_a_sl_and_tp1_same_bar_sl_wins():
    """LONG bar_low=97 (≤SL) AND bar_high=103 (≥TP1) → SL 保守先触."""
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 103, 97, 100), t1)
    assert not pos.is_open
    assert pos.close_reason == "hit_sl"


# ── Phase B: BE-SL hit ─────────────────────────────────────────

def test_long_phase_b_be_sl_clean():
    """HWM 不足以 trail 超过 entry → SL 停在 entry → hit_be_sl."""
    sig = _long_signal()   # entry=100, atr=1
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    # Bar 1: TP1 hit, HWM = 102, bar_low=100.5 (避免同 bar BE SL)
    update_position(pos, _bar(100, 102.0, 100.5, 101), t1)
    assert pos.phase == "B"
    assert pos.sl_price == 100.0

    # Bar 2: 价格回落 99.5. trail = HWM(102) - 2 = 100, SL = max(100, 100, entry=100) = 100
    # bar_low=99.5 < 100 → SL fires. SL = entry → hit_be_sl
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 101.5, 99.5, 100), t2)
    assert not pos.is_open
    assert pos.close_reason == "hit_be_sl"


# ── Phase B: b_trail hit (SL ratchet > entry) ─────────────────

def test_long_phase_b_b_trail_hit():
    """TP1 hit, HWM 涨到 103.5, 下根 bar trail SL ratchet > entry, 再回落触发 → hit_b_trail."""
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    # Bar 1: TP1 hit, HWM 飙到 103.5, bar_low=100.5 避免 BE SL
    update_position(pos, _bar(100, 103.5, 100.5, 102), t1)
    assert pos.phase == "B"
    # 单步: TP1 fires 后 SL=entry=100 (trail 留到下根 bar)
    assert pos.sl_price == 100.0
    assert pos.high_water_mark == 103.5

    # Bar 2: 价格回落, 计算 trail: max(100, 103.5-2=101.5, 100)=101.5
    # bar_low=101.0 < 101.5 → SL fires at 101.5 (b_trail)
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(102, 102.5, 101.0, 101.5), t2)
    assert not pos.is_open
    assert pos.close_reason == "hit_b_trail"


# ── Phase B → C: TP2 hit ──────────────────────────────────────

def test_long_phase_b_tp2_hit_transitions_to_c():
    """Bar 1: TP1 → B; Bar 2: TP2=104 → C.

    Bar 2 需 bar_low > trail(=HWM-2). bar_high=104.5 → HWM=104.5, trail=102.5.
    取 bar_low=102.6 严格 > 102.5 避免 SL fire.
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 102.0, 100.5, 101), t1)
    assert pos.phase == "B"

    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 104.5, 102.6, 104), t2)
    assert pos.is_open
    assert pos.phase == "C"
    assert pos.tp2_hit_at == t2
    assert len(pos.legs) == 2
    assert pos.legs[1].reason == "hit_tp2"
    assert pos.legs[1].close_price == 104.0


# ── Phase C: TP3 hit ──────────────────────────────────────────

def test_long_full_winner_tp1_tp2_tp3():
    """A → TP1 → B → TP2 → C → TP3 全胜. 每根 bar 1 次 phase 转换.

    每根 bar 设置 bar_low > 新 trail SL 避免误触发.
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    # Bar 1: bar_high=102 (TP1), bar_low=100.5 > entry → no BE SL
    update_position(pos, _bar(100, 102, 100.5, 101), t1)
    # Bar 2: bar_high=104 (TP2), HWM 仍为 104, trail=102, bar_low=102.5 > 102 → no SL
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 104, 102.5, 103), t2)
    # Bar 3: bar_high=106.5 (TP3), HWM=106.5, trail=104.5. bar_low > 104.5 必需 → 104.6
    t3 = t2 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(103, 106.5, 104.6, 105), t3)
    assert not pos.is_open
    assert pos.close_reason == "hit_tp3"
    assert len(pos.legs) == 3
    assert pos.legs[0].reason == "hit_tp1"
    assert pos.legs[1].reason == "hit_tp2"
    assert pos.legs[2].reason == "hit_tp3"
    assert pos.legs[2].close_price == 106.0


# ── Phase C: trail SL hit ─────────────────────────────────────

def test_long_phase_c_trail_hit():
    """A→TP1→B→TP2→C; Bar 3 HWM 涨 105.5, trail=103.5; Bar 4 回落 → hit_trail."""
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 102, 100.5, 101), t1)
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 104, 102.5, 103), t2)
    assert pos.phase == "C"
    # Bar 3: HWM 涨到 105.5, bar_low=103.6 > new trail(103.5)
    t3 = t2 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(103.5, 105.5, 103.6, 105), t3)
    assert pos.is_open
    # SL ratchet 到 103.5
    assert pos.sl_price == pytest.approx(103.5)

    # Bar 4: 回落到 103 (< 103.5)
    t4 = t3 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(105, 105.5, 103.0, 104), t4)
    assert not pos.is_open
    assert pos.close_reason == "hit_trail"


# ── Timeout ────────────────────────────────────────────────────

def test_timeout_after_14_days():
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    # 移到 14 天后
    t_late = sig.entry_time + pd.Timedelta(hours=TIMEOUT_HOURS + 1)
    update_position(pos, _bar(100, 100.5, 99.5, 100.0), t_late)
    assert not pos.is_open
    assert pos.close_reason == "timeout"


def test_timeout_before_14_days_no_close():
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    # 13.9 天后 (差 1 小时)
    t = sig.entry_time + pd.Timedelta(hours=TIMEOUT_HOURS - 1)
    update_position(pos, _bar(100, 100.5, 99.5, 100.0), t)
    assert pos.is_open
    assert pos.close_reason is None


# ── 1 bar 1 transition: 同 bar 多 TP 只触发当前 phase 的 ──────────

def test_long_single_bar_only_one_phase_transition():
    """单根 bar 即使 bar_high ≥ TP2/TP3, 也只触发 TP1 (phase A → B 一次).
    新 SL/TP 留到下根 bar 检.
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    # bar_high=106 (≥ TP3), bar_low=100. 但只 TP1 fires.
    update_position(pos, _bar(100, 106, 100, 105), t1)
    assert pos.is_open
    assert pos.phase == "B"        # 只升到 B, 没到 C
    assert len(pos.legs) == 1
    assert pos.legs[0].reason == "hit_tp1"


# ── SHORT mirror ──────────────────────────────────────────────

def test_short_phase_a_tp1_hit():
    sig = _short_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 100.5, 97.5, 98), t1)
    assert pos.is_open
    assert pos.phase == "B"
    assert pos.legs[0].reason == "hit_tp1"
    assert pos.legs[0].close_price == 98.0


def test_short_full_winner():
    sig = _short_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    # SHORT TP1: bar_low ≤ 98. bar_high < SL=102 (no SL).
    update_position(pos, _bar(100, 100, 98, 98.5), t1)
    # Bar 2: TP2. After TP1, SL=entry=100. HWM_min=98. Trail (SHORT)=98+2=100. SL=min(100, 100, 100)=100.
    # bar_high < trail (=100) to avoid SL. bar_low ≤ TP2=96.
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(98, 97.5, 96, 96.5), t2)
    # Bar 3: TP3. After TP2 → C. HWM_min=96. Trail=96+2=98 → SL升级到 min(100, 98)=98.
    # bar_high < trail (HWM_min after bar 3 = 94, trail = 96) — need bar_high < 96.
    # bar_low ≤ TP3=94.
    t3 = t2 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(96, 95.5, 94, 94), t3)
    assert not pos.is_open
    assert pos.close_reason == "hit_tp3"


# ── compute_final_pnl 数学 ───────────────────────────────────

def test_compute_final_pnl_sl_loss():
    """LONG SL: close all at 98, entry 100.
    qty = 400/100 = 4. PnL gross = (98-100) × 4 = -8.
    Fees = 100 × 4 × 0.0004 × 2 = 0.32. Net = -8 - 0.32 = -8.32.
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 100.5, 97, 98), t1)
    pnl = compute_final_pnl(pos)
    assert pnl == pytest.approx(-8.32, abs=0.01)


def test_compute_final_pnl_full_winner():
    """LONG 全胜: TP1=102, TP2=104, TP3=106. Entry 100, qty 4. 各 leg frac=1/3.
    Gross = (2+4+6) × 4/3 = 16. Fees per leg ≈ 0.1067. Total fees ≈ 0.32.
    Net ≈ 15.68.
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 102, 100.5, 101), t1)
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 104, 102.5, 103), t2)
    t3 = t2 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(103, 106, 104.6, 105), t3)
    assert pos.close_reason == "hit_tp3"   # 先验证全胜
    pnl = compute_final_pnl(pos)
    assert pnl == pytest.approx(15.68, abs=0.01)


def test_compute_final_pnl_be_sl_partial_win():
    """TP1 hit (+) then BE_SL (0): 1/3 wins, 2/3 zero.
    Leg 1 (TP1): (102-100)×(4/3) - fees = 2.667 - 0.1067 = 2.56
    Leg 2 (BE_SL at 100): 0 - fees = -0.2133 (2 × leg_frac fees)
    """
    sig = _long_signal()
    pos = open_position(sig, sig.entry_time)
    t1 = sig.entry_time + pd.Timedelta(minutes=15)
    update_position(pos, _bar(100, 102, 100.5, 101), t1)   # TP1
    t2 = t1 + pd.Timedelta(minutes=15)
    update_position(pos, _bar(101, 101, 99.5, 100), t2)    # BE_SL hit
    assert pos.close_reason == "hit_be_sl"
    pnl = compute_final_pnl(pos)
    # Leg 1: gross 2.667, fee 0.1067, net 2.56
    # Leg 2: gross 0, fee 0.2133, net -0.2133
    assert pnl == pytest.approx(2.56 - 0.2133, abs=0.02)


# ── 常量验证 ────────────────────────────────────────────────

def test_constants():
    assert TIMEOUT_HOURS == 14 * 24
    assert TRAIL_ATR_MULT == 2.0
    assert PARTIAL_CLOSE_FRAC == pytest.approx(1/3)
    assert NOTIONAL_USDT == 400.0
    assert FEE_RATE == 0.0004
