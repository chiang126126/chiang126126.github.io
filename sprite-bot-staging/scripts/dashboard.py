"""Sprite Catcher · 左右双轨看板（Solana | CEX Futures）。

启动：
    uv run streamlit run scripts/dashboard.py

数据来源（Solana）：
    data/daemon_status.json / paper_daemon_status.json
    data/budget.json / data/paper_positions.json
    data/paper_trades/*.jsonl / data/scans/*.jsonl

数据来源（CEX）：
    data/cex_daemon_status.json
    data/cex_paper_positions.json
    data/cex_paper_trades/*.jsonl
    data/cex_scans/*.jsonl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from sprite_bot.dashboard_data import (
    STARTING_CAPITAL_USD,
    compute_kpis,
    compute_pnl_curve,
    compute_scan_stats,
    daemon_status_indicator,
    format_mint_short,
    load_closed_trades,
    load_open_positions,
    load_status_file,
)

DATA_DIR = ROOT / "data"
REFRESH_S = 30
DAILY_HELIUS_LIMIT = 3300
CEX_STARTING_CAPITAL = 200.0

_STRATEGY_ABBR: dict[str, str] = {
    "trend_follow":      "trend↑",
    "support_collapse":  "s.collapse",
    "short_vacuum":      "vacuum",
    "distribution":      "distrib",
    "plan_trend_follow": "trend↑",
    "plan_support_collapse": "s.collapse",
    "plan_short_vacuum": "vacuum",
    "plan_distribution": "distrib",
}

def _abbr_strategy(s: str) -> str:
    return _STRATEGY_ABBR.get(s, s[:11] + ("…" if len(s) > 11 else ""))


def _fmt_ts(ts: str) -> str:
    """从 ISO 时间串提取 HH:MM，兼容空字符串。"""
    return ts[11:16] if len(ts) >= 16 else ts


# ── CEX helpers ───────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def load_cex_open_positions(path: Path) -> list[dict]:
    return _load_json(path, default=[])


def load_cex_closed_trades(trades_dir: Path, days: int = 14) -> list[dict]:
    trades: list[dict] = []
    if not trades_dir.exists():
        return trades
    for p in sorted(trades_dir.glob("*.jsonl"))[-days:]:
        for line in p.read_text().splitlines():
            try:
                obj = json.loads(line)
                if obj.get("type") == "close":
                    trades.append(obj)
            except json.JSONDecodeError:
                pass
    return trades


def load_cex_scan_stats(scans_dir: Path, days: int = 1) -> dict:
    scans, analyzed, intents = 0, 0, 0
    if not scans_dir.exists():
        return {"scans": 0, "analyzed": 0, "intents": 0, "intent_rate": 0.0}
    for p in sorted(scans_dir.glob("*.jsonl"))[-days:]:
        for line in p.read_text().splitlines():
            try:
                obj = json.loads(line)
                if obj.get("type") == "scan_meta" and not obj.get("skipped"):
                    scans += 1
                    analyzed += obj.get("analyzed", 0)
                    intents += obj.get("intents_fired", 0)
            except json.JSONDecodeError:
                pass
    return {
        "scans": scans,
        "analyzed": analyzed,
        "intents": intents,
        "intent_rate": intents / analyzed if analyzed > 0 else 0.0,
    }


def compute_cex_kpis(open_pos: list[dict], closed: list[dict], starting: float) -> dict:
    realized = sum(t.get("pnl_usd", 0) for t in closed)
    wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
    losses = sum(1 for t in closed if t.get("pnl_usd", 0) <= 0)
    n = wins + losses
    qty_open = sum(p.get("qty_usd", 0) for p in open_pos)
    equity = starting + realized
    return {
        "equity": equity,
        "realized_pnl": realized,
        "roi_pct": realized / starting if starting > 0 else 0,
        "win_rate": wins / n if n > 0 else 0,
        "wins": wins,
        "losses": losses,
        "closed_count": n,
        "open_count": len(open_pos),
        "qty_in_market": qty_open,
    }


def compute_cex_pnl_curve(closed: list[dict], starting: float) -> pd.DataFrame:
    if not closed:
        return pd.DataFrame(columns=["ts", "equity"])
    rows = sorted(closed, key=lambda x: x.get("ts", ""))
    equity = starting
    pts = []
    for t in rows:
        equity += t.get("pnl_usd", 0)
        pts.append({"ts": t["ts"], "equity": equity})
    return pd.DataFrame(pts)


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Sprite Catcher",
    page_icon="⚡",
    layout="wide",
)
st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_S}">', unsafe_allow_html=True)
st.markdown("""
<style>
/* 压缩顶部空白 */
.block-container { padding-top: 1rem !important; }

/* 减少 metric 内边距 */
[data-testid="stMetric"] { padding: 0.25rem 0 !important; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
[data-testid="stMetricValue"] { font-size: 1.3rem !important; }
[data-testid="stMetricDelta"] { font-size: 0.7rem !important; }

/* 小节标题 */
.sec {
    font-size: 0.68rem;
    font-weight: 700;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 0.9rem 0 0.25rem 0;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid #2a2a2a;
}

/* 列标题 */
.col-hdr {
    font-size: 1.05rem;
    font-weight: 800;
    padding: 0.35rem 0 0.35rem 0.75rem;
    margin-bottom: 0.6rem;
}
.col-hdr-sol { border-left: 4px solid #7c83fd; }
.col-hdr-cex { border-left: 4px solid #52d97a; }
</style>
""", unsafe_allow_html=True)


# ── 加载数据 ──────────────────────────────────────────────────────────────────

sol_scan_status  = load_status_file(DATA_DIR / "daemon_status.json")
sol_paper_status = load_status_file(DATA_DIR / "paper_daemon_status.json")
cex_status       = _load_json(DATA_DIR / "cex_daemon_status.json", default={})

open_positions = load_open_positions(DATA_DIR / "paper_positions.json")
closed_trades  = load_closed_trades(DATA_DIR / "paper_trades", days=14)
budget         = load_status_file(DATA_DIR / "budget.json")
kpis           = compute_kpis(open_positions, closed_trades, starting_capital=STARTING_CAPITAL_USD)
pnl_df         = compute_pnl_curve(closed_trades, starting_capital=STARTING_CAPITAL_USD)
scan_stats     = compute_scan_stats(DATA_DIR / "scans", days=1)

cex_open    = load_cex_open_positions(DATA_DIR / "cex_paper_positions.json")
cex_closed  = load_cex_closed_trades(DATA_DIR / "cex_paper_trades", days=14)
cex_kpis    = compute_cex_kpis(cex_open, cex_closed, CEX_STARTING_CAPITAL)
cex_pnl_df  = compute_cex_pnl_curve(cex_closed, CEX_STARTING_CAPITAL)
cex_scan    = load_cex_scan_stats(DATA_DIR / "cex_scans", days=1)


# ── Header：标题 + 刷新时间 ───────────────────────────────────────────────────

h_title, h_time = st.columns([9, 1])
with h_title:
    st.markdown("### ⚡ Sprite Catcher")
with h_time:
    st.caption(f"🕐 {datetime.now().strftime('%H:%M:%S')}")
    st.caption(f"↻ {REFRESH_S}s")

st.markdown("---")

# 预计算状态文本（供各栏顶部使用）
sol_scan_ind  = daemon_status_indicator(sol_scan_status,  name="Sol-scan",  stale_threshold_s=4 * 3600)
sol_paper_ind = daemon_status_indicator(sol_paper_status, name="Sol-paper", stale_threshold_s=5 * 60)
cex_dot  = "🟢" if cex_status.get("running") else "🔴"
cex_last = _fmt_ts(cex_status.get("last_update_at", "—"))


# ── 左右双栏 ─────────────────────────────────────────────────────────────────

sol_col, cex_col = st.columns(2, gap="large")


# ══════════════════════════════════════════════════════════════════════════════
# 左栏：Solana · Memecoins
# ══════════════════════════════════════════════════════════════════════════════

with sol_col:
    st.markdown('<div class="col-hdr col-hdr-sol">🌙 Solana · Memecoins</div>', unsafe_allow_html=True)
    st.markdown(f"{sol_scan_ind} &nbsp;&nbsp; {sol_paper_ind}", unsafe_allow_html=True)

    # ── KPI 两行各 3 格 ───────────────────────────────────────────────────────
    roi_pct    = kpis["roi_pct"] * 100
    helius_used = int(budget.get("calls_today", 0))

    m1, m2, m3 = st.columns(3)
    m1.metric("💰 净值",   f"${kpis['equity']:.2f}",    f"{roi_pct:+.1f}%")
    m2.metric("🎯 胜率",
              f"{kpis['win_rate']*100:.1f}%" if kpis["closed_count"] > 0 else "—",
              f"{kpis['wins']}W / {kpis['losses']}L")
    m3.metric("📂 持仓",   f"{kpis['open_count']} / 10",
              f"${kpis['value_in_market']:.0f}")

    m4, m5, m6 = st.columns(3)
    m4.metric("📊 已实现", f"${kpis['realized_pnl']:+.2f}", f"{kpis['closed_count']} 笔")
    m5.metric("⛽ Helius", f"{helius_used:,}",
              f"{helius_used / DAILY_HELIUS_LIMIT * 100:.0f}% / day")
    m6.metric("📡 扫描",   str(scan_stats["scans_in_window"]),
              f"{scan_stats['actionable_signals']} 信号")

    # ── P&L 曲线 ──────────────────────────────────────────────────────────────
    st.markdown('<div class="sec">P&L 曲线 · 14天</div>', unsafe_allow_html=True)
    if not pnl_df.empty and len(pnl_df) > 1:
        st.line_chart(pnl_df.set_index("ts")["equity"], height=160)
    else:
        st.caption("首次平仓后自动生成曲线。")

    # ── 当前持仓 ──────────────────────────────────────────────────────────────
    st.markdown(f'<div class="sec">持仓中 ({len(open_positions)})</div>', unsafe_allow_html=True)
    if open_positions:
        now = datetime.now(timezone.utc)
        rows = []
        for p in open_positions:
            entry   = p.get("entry_price_usd", 0)
            current = p.get("current_price_usd", 0)
            size    = p.get("size_usd", 0)
            try:
                opened_at = datetime.fromisoformat(p.get("opened_at", "").replace("Z", "+00:00"))
                hold_h = (now - opened_at).total_seconds() / 3600
            except ValueError:
                hold_h = 0.0
            pnl_pct = ((current / entry - 1) * 100) if entry > 0 else 0.0
            rows.append({
                "Mint":   format_mint_short(p.get("mint", "")),
                "入场$":  f"${entry:.6g}",
                "现价$":  f"${current:.6g}",
                "P&L%":   f"{pnl_pct:+.1f}%",
                "P&L$":   f"${size * pnl_pct / 100:+.2f}",
                "持仓":   f"{hold_h:.1f}h",
            })
        rows.sort(key=lambda r: float(r["持仓"].rstrip("h")), reverse=True)
        tbl_h = min(38 * len(rows) + 40, 220)
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True, height=tbl_h,
            column_config={
                "Mint":  st.column_config.TextColumn(width="small"),
                "P&L%":  st.column_config.TextColumn(width="small"),
                "P&L$":  st.column_config.TextColumn(width="small"),
                "持仓":  st.column_config.TextColumn(width="small"),
            },
        )
    else:
        st.caption("暂无持仓。")

    # ── 已平仓（最近 30 笔） ──────────────────────────────────────────────────
    st.markdown(f'<div class="sec">已平仓 · 14天 ({len(closed_trades)})</div>',
                unsafe_allow_html=True)
    if closed_trades:
        rows = []
        for t in sorted(closed_trades, key=lambda x: x.get("ts", ""), reverse=True)[:30]:
            pnl_pct = t.get("pnl_pct", 0) * 100
            rows.append({
                "Mint":  format_mint_short(t.get("mint", "")),
                "P&L%":  f"{pnl_pct:+.1f}%",
                "P&L$":  f"${t.get('pnl_usd', 0):+.2f}",
                "原因":  t.get("exit_reason", "?"),
                "时间":  _fmt_ts(t.get("ts", "")),
            })
        tbl_h = min(38 * len(rows) + 40, 260)
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True, height=tbl_h,
            column_config={
                "P&L%": st.column_config.TextColumn(width="small"),
                "P&L$": st.column_config.TextColumn(width="small"),
                "原因": st.column_config.TextColumn(width="small"),
                "时间": st.column_config.TextColumn(width="small"),
            },
        )
    else:
        st.caption("暂无平仓记录。")


# ══════════════════════════════════════════════════════════════════════════════
# 右栏：CEX · Futures
# ══════════════════════════════════════════════════════════════════════════════

with cex_col:
    st.markdown('<div class="col-hdr col-hdr-cex">📈 CEX · Futures (Binance USDT-M)</div>',
                unsafe_allow_html=True)
    st.markdown(f"{cex_dot} CEX scan &nbsp; · &nbsp; 最近更新 `{cex_last}`", unsafe_allow_html=True)

    # ── KPI 两行各 3 格 ───────────────────────────────────────────────────────
    roi = cex_kpis["roi_pct"] * 100

    c1, c2, c3 = st.columns(3)
    c1.metric("💰 净值",   f"${cex_kpis['equity']:.2f}", f"{roi:+.1f}%")
    c2.metric("🎯 胜率",
              f"{cex_kpis['win_rate']*100:.1f}%" if cex_kpis["closed_count"] > 0 else "—",
              f"{cex_kpis['wins']}W / {cex_kpis['losses']}L")
    c3.metric("📂 持仓",   str(cex_kpis["open_count"]),
              f"${cex_kpis['qty_in_market']:.0f} 在市")

    c4, c5, c6 = st.columns(3)
    c4.metric("📊 已实现", f"${cex_kpis['realized_pnl']:+.2f}", f"{cex_kpis['closed_count']} 笔")
    c5.metric("📡 扫描轮次", str(cex_scan["scans"]),
              f"{cex_scan['analyzed']} 深度分析")
    c6.metric("💡 信号率",
              f"{cex_scan['intent_rate']*100:.1f}%" if cex_scan["analyzed"] > 0 else "—",
              f"{cex_scan['intents']} 触发")

    # ── P&L 曲线 ──────────────────────────────────────────────────────────────
    st.markdown('<div class="sec">P&L 曲线 · 14天</div>', unsafe_allow_html=True)
    if not cex_pnl_df.empty and len(cex_pnl_df) > 1:
        st.line_chart(cex_pnl_df.set_index("ts")["equity"], height=160)
    else:
        st.caption("首次平仓后自动生成曲线。")

    # ── 当前持仓 ──────────────────────────────────────────────────────────────
    st.markdown(f'<div class="sec">持仓中 ({len(cex_open)})</div>', unsafe_allow_html=True)
    if cex_open:
        now = datetime.now(timezone.utc)
        rows = []
        for p in cex_open:
            try:
                opened_at = datetime.fromisoformat(p.get("opened_at", "").replace("Z", "+00:00"))
                hold_h = (now - opened_at).total_seconds() / 3600
            except ValueError:
                hold_h = 0.0
            side = p.get("side", "?")
            tp_raw = p.get("take_profit")
            rows.append({
                "Symbol":  p.get("symbol", "?"),
                "方向":    "🟢多" if side == "long" else "🔴空",
                "策略":    _abbr_strategy(p.get("strategy_id", "?")),
                "入场":    f"{p.get('entry_price', 0):.5g}",
                "止损":    f"{p.get('stop_loss', 0):.5g}",
                "止盈":    f"{tp_raw:.5g}" if tp_raw is not None else "trailing",
                "仓位$":   f"${p.get('qty_usd', 0):.0f}",
                "持仓":    f"{hold_h:.1f}h",
            })
        rows.sort(key=lambda r: float(r["持仓"].rstrip("h")), reverse=True)
        tbl_h = min(38 * len(rows) + 40, 220)
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True, height=tbl_h,
            column_config={
                "方向":   st.column_config.TextColumn(width="small"),
                "策略":   st.column_config.TextColumn(width="small"),
                "入场":   st.column_config.TextColumn(width="small"),
                "止损":   st.column_config.TextColumn(width="small"),
                "止盈":   st.column_config.TextColumn(width="small"),
                "仓位$":  st.column_config.TextColumn(width="small"),
                "持仓":   st.column_config.TextColumn(width="small"),
            },
        )
    else:
        st.caption("暂无 CEX 持仓。")

    # ── 已平仓（最近 30 笔） ──────────────────────────────────────────────────
    st.markdown(f'<div class="sec">已平仓 · 14天 ({len(cex_closed)})</div>',
                unsafe_allow_html=True)
    if cex_closed:
        rows = []
        for t in sorted(cex_closed, key=lambda x: x.get("ts", ""), reverse=True)[:30]:
            pnl_pct = t.get("pnl_pct", 0) * 100
            side = t.get("side", "?")
            rows.append({
                "Symbol":  t.get("symbol", "?"),
                "方向":    "🟢多" if side == "long" else "🔴空",
                "策略":    _abbr_strategy(t.get("strategy_id", "?")),
                "P&L%":    f"{pnl_pct:+.1f}%",
                "P&L$":    f"${t.get('pnl_usd', 0):+.2f}",
                "原因":    t.get("exit_reason", "?"),
                "持仓":    f"{t.get('hold_hours', 0):.1f}h",
                "时间":    _fmt_ts(t.get("ts", "")),
            })
        tbl_h = min(38 * len(rows) + 40, 260)
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True, height=tbl_h,
            column_config={
                "方向":  st.column_config.TextColumn(width="small"),
                "策略":  st.column_config.TextColumn(width="small"),
                "P&L%": st.column_config.TextColumn(width="small"),
                "P&L$": st.column_config.TextColumn(width="small"),
                "原因":  st.column_config.TextColumn(width="small"),
                "持仓":  st.column_config.TextColumn(width="small"),
                "时间":  st.column_config.TextColumn(width="small"),
            },
        )
    else:
        st.caption("暂无 CEX 平仓记录。")

    # 策略分布（有数据才显示）
    strategies: dict[str, int] = {}
    for p in cex_open:
        s = p.get("strategy_id", "unknown")
        strategies[s] = strategies.get(s, 0) + 1
    if strategies:
        st.caption("持仓策略：" + " · ".join(f"**{k}** ×{v}" for k, v in strategies.items()))
