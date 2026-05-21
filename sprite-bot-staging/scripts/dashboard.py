"""Sprite Catcher · 双轨看板（Solana + CEX Futures）。

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
    load_scan_meta,
    load_status_file,
)

DATA_DIR = ROOT / "data"
REFRESH_S = 30
DAILY_HELIUS_LIMIT = 3300
CEX_STARTING_CAPITAL = 200.0   # CEX paper 账户起始资金 USD


# ============================================================
# CEX 数据加载工具
# ============================================================

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


# ============================================================
# Page config
# ============================================================

st.set_page_config(
    page_title="Sprite Catcher",
    page_icon="⚡",
    layout="wide",
)
st.markdown(
    f'<meta http-equiv="refresh" content="{REFRESH_S}">',
    unsafe_allow_html=True,
)

# ============================================================
# 全局 Header（两轨共享）
# ============================================================

hdr1, hdr2, hdr3 = st.columns([3, 3, 1])

with hdr1:
    st.title("⚡ Sprite Catcher")

with hdr2:
    # Solana daemons
    sol_scan_status = load_status_file(DATA_DIR / "daemon_status.json")
    sol_paper_status = load_status_file(DATA_DIR / "paper_daemon_status.json")
    cex_status = _load_json(DATA_DIR / "cex_daemon_status.json", default={})

    st.markdown(daemon_status_indicator(
        sol_scan_status, name="🌙 Solana scan",
        stale_threshold_s=4 * 3600))
    st.markdown(daemon_status_indicator(
        sol_paper_status, name="🌙 Solana paper",
        stale_threshold_s=5 * 60))

    cex_running = cex_status.get("running", False)
    cex_last = cex_status.get("last_update_at", "—")
    cex_dot = "🟢" if cex_running else "🔴"
    st.markdown(f"{cex_dot} **CEX scan** · last: `{cex_last}`")

with hdr3:
    st.caption(f"Auto-refresh: {REFRESH_S}s")
    st.caption(f"As of: {datetime.now().strftime('%H:%M:%S')}")

st.divider()

# ============================================================
# 双轨 Tabs
# ============================================================

tab_sol, tab_cex = st.tabs(["🌙 Solana · Memecoins", "📈 CEX · Futures"])


# ============================================================
# Tab 1：Solana（原有内容）
# ============================================================

with tab_sol:

    open_positions = load_open_positions(DATA_DIR / "paper_positions.json")
    closed_trades = load_closed_trades(DATA_DIR / "paper_trades", days=14)
    budget = load_status_file(DATA_DIR / "budget.json")
    kpis = compute_kpis(open_positions, closed_trades,
                        starting_capital=STARTING_CAPITAL_USD)
    pnl_df = compute_pnl_curve(closed_trades,
                               starting_capital=STARTING_CAPITAL_USD)
    scan_stats = compute_scan_stats(DATA_DIR / "scans", days=1)

    # KPI Row
    k1, k2, k3, k4, k5 = st.columns(5)
    roi_pct = kpis["roi_pct"] * 100
    k1.metric("💰 Equity", f"${kpis['equity']:.2f}",
              f"{roi_pct:+.1f}%" if abs(roi_pct) > 0.01 else None)
    k2.metric("📊 Realized", f"${kpis['realized_pnl']:+.2f}",
              f"{kpis['closed_count']} closed")
    k3.metric("🎯 Win Rate",
              f"{kpis['win_rate']*100:.1f}%" if kpis['closed_count'] > 0 else "—",
              f"{kpis['wins']}W / {kpis['losses']}L")
    k4.metric("📂 Open", f"{kpis['open_count']} / 10",
              f"${kpis['value_in_market']:.0f} in market")
    helius_used = int(budget.get("calls_today", 0))
    helius_pct = helius_used / DAILY_HELIUS_LIMIT if DAILY_HELIUS_LIMIT > 0 else 0
    k5.metric("⛽ Helius", f"{helius_used:,}",
              f"{helius_pct*100:.0f}% of {DAILY_HELIUS_LIMIT:,}")

    st.divider()

    # P&L Curve
    st.subheader("📈 P&L Curve (14 days)")
    if not pnl_df.empty and len(pnl_df) > 1:
        st.line_chart(pnl_df.set_index("ts")["equity"], height=260)
    else:
        st.info("No closed trades yet.")

    st.divider()

    # Open Positions
    st.subheader(f"🎯 Open Positions ({len(open_positions)})")
    if open_positions:
        now = datetime.now(timezone.utc)
        rows = []
        for p in open_positions:
            entry = p.get("entry_price_usd", 0)
            current = p.get("current_price_usd", 0)
            size = p.get("size_usd", 0)
            opened_str = p.get("opened_at", "")
            try:
                opened_at = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
                hold_h = (now - opened_at).total_seconds() / 3600
            except ValueError:
                hold_h = 0.0
            pnl_pct = ((current / entry - 1) * 100) if entry > 0 else 0.0
            rows.append({
                "Mint": format_mint_short(p.get("mint", "")),
                "Entry $": f"${entry:.8f}".rstrip("0").rstrip("."),
                "Current $": f"${current:.8f}".rstrip("0").rstrip("."),
                "P&L %": f"{pnl_pct:+.1f}%",
                "P&L $": f"${size * pnl_pct / 100:+.2f}",
                "Hold": f"{hold_h:.1f}h",
            })
        rows.sort(key=lambda r: float(r["Hold"].rstrip("h")), reverse=True)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")

    st.divider()

    # Closed Trades
    st.subheader(f"📜 Closed Trades · 14 days ({len(closed_trades)})")
    if closed_trades:
        rows = []
        for t in sorted(closed_trades, key=lambda x: x.get("ts", ""), reverse=True):
            entry = t.get("entry_price_usd", 0)
            exitp = t.get("exit_price_usd", 0)
            pnl_pct = t.get("pnl_pct", 0) * 100
            rows.append({
                "Mint": format_mint_short(t.get("mint", "")),
                "Entry $": f"${entry:.8f}".rstrip("0").rstrip("."),
                "Exit $": f"${exitp:.8f}".rstrip("0").rstrip("."),
                "P&L %": f"{pnl_pct:+.1f}%",
                "P&L $": f"${t.get('pnl_usd', 0):+.2f}",
                "Reason": t.get("exit_reason", "?"),
                "Closed": t.get("ts", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No closed trades yet.")

    st.divider()

    # Scan Stats
    st.subheader("📡 Today's Scan Stats")
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Scans (24h)", scan_stats["scans_in_window"])
    sc2.metric("Tokens scanned", scan_stats["tokens_scanned"])
    sc3.metric("Actionable", scan_stats["actionable_signals"])
    sc4.metric("Actionable rate",
               f"{scan_stats['actionable_rate']*100:.1f}%"
               if scan_stats["tokens_scanned"] > 0 else "—")


# ============================================================
# Tab 2：CEX Futures（新内容）
# ============================================================

with tab_cex:

    cex_open = load_cex_open_positions(DATA_DIR / "cex_paper_positions.json")
    cex_closed = load_cex_closed_trades(DATA_DIR / "cex_paper_trades", days=14)
    cex_kpis = compute_cex_kpis(cex_open, cex_closed, CEX_STARTING_CAPITAL)
    cex_pnl_df = compute_cex_pnl_curve(cex_closed, CEX_STARTING_CAPITAL)
    cex_scan = load_cex_scan_stats(DATA_DIR / "cex_scans", days=1)

    # KPI Row
    ck1, ck2, ck3, ck4 = st.columns(4)
    roi = cex_kpis["roi_pct"] * 100
    ck1.metric("💰 Equity",
               f"${cex_kpis['equity']:.2f}",
               f"{roi:+.1f}%" if abs(roi) > 0.01 else None)
    ck2.metric("📊 Realized",
               f"${cex_kpis['realized_pnl']:+.2f}",
               f"{cex_kpis['closed_count']} closed")
    ck3.metric("🎯 Win Rate",
               f"{cex_kpis['win_rate']*100:.1f}%" if cex_kpis['closed_count'] > 0 else "—",
               f"{cex_kpis['wins']}W / {cex_kpis['losses']}L")
    ck4.metric("📂 Open",
               f"{cex_kpis['open_count']}",
               f"${cex_kpis['qty_in_market']:.0f} in market")

    st.divider()

    # P&L Curve
    st.subheader("📈 P&L Curve (14 days)")
    if not cex_pnl_df.empty and len(cex_pnl_df) > 1:
        st.line_chart(cex_pnl_df.set_index("ts")["equity"], height=260)
    else:
        st.info("No closed CEX trades yet — P&L curve appears after first close.")

    st.divider()

    # Open Positions
    st.subheader(f"🎯 Open Positions ({len(cex_open)})")
    if cex_open:
        now = datetime.now(timezone.utc)
        rows = []
        for p in cex_open:
            opened_str = p.get("opened_at", "")
            try:
                opened_at = datetime.fromisoformat(opened_str.replace("Z", "+00:00"))
                hold_h = (now - opened_at).total_seconds() / 3600
            except ValueError:
                hold_h = 0.0
            side = p.get("side", "?")
            side_emoji = "🟢" if side == "long" else "🔴"
            rows.append({
                "Symbol": p.get("symbol", "?"),
                "方向": f"{side_emoji} {side.upper()}",
                "策略": p.get("strategy_id", "?"),
                "入场价": f"{p.get('entry_price', 0):.6g}",
                "止损": f"{p.get('stop_loss', 0):.6g}",
                "止盈": f"{p.get('take_profit', '—') or '—'}",
                "仓位$": f"${p.get('qty_usd', 0):.0f}",
                "持仓": f"{hold_h:.1f}h",
            })
        rows.sort(key=lambda r: float(r["持仓"].rstrip("h")), reverse=True)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No open CEX positions.")

    st.divider()

    # Closed Trades
    st.subheader(f"📜 Closed Trades · 14 days ({len(cex_closed)})")
    if cex_closed:
        rows = []
        for t in sorted(cex_closed, key=lambda x: x.get("ts", ""), reverse=True):
            pnl_pct = t.get("pnl_pct", 0) * 100
            side = t.get("side", "?")
            side_emoji = "🟢" if side == "long" else "🔴"
            rows.append({
                "Symbol": t.get("symbol", "?"),
                "方向": f"{side_emoji} {side.upper()}",
                "策略": t.get("strategy_id", "?"),
                "入场价": f"{t.get('entry_price', 0):.6g}",
                "出场价": f"{t.get('exit_price', 0):.6g}",
                "P&L %": f"{pnl_pct:+.1f}%",
                "P&L $": f"${t.get('pnl_usd', 0):+.2f}",
                "原因": t.get("exit_reason", "?"),
                "持仓": f"{t.get('hold_hours', 0):.1f}h",
                "时间": t.get("ts", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No closed CEX trades yet.")

    st.divider()

    # Scan Stats + 策略分布
    st.subheader("📡 Today's Scan Stats")
    cs1, cs2, cs3, cs4 = st.columns(4)
    cs1.metric("扫描轮次", cex_scan["scans"])
    cs2.metric("深度分析", cex_scan["analyzed"])
    cs3.metric("触发信号", cex_scan["intents"])
    cs4.metric("信号率",
               f"{cex_scan['intent_rate']*100:.1f}%"
               if cex_scan["analyzed"] > 0 else "—")

    # 策略触发分布（从 cex_paper_trades 统计）
    all_open_strategies: dict[str, int] = {}
    for p in cex_open:
        s = p.get("strategy_id", "unknown")
        all_open_strategies[s] = all_open_strategies.get(s, 0) + 1
    if all_open_strategies:
        st.caption("当前持仓策略分布：" + " · ".join(
            f"**{k}** {v}" for k, v in all_open_strategies.items()
        ))

    st.caption("Sprite Catcher · Paper Mode · CEX Futures (Binance USDT-M)")
