#!/usr/bin/env python3
"""Crésus 每日报告生成器 — 每天运行一次，输出 markdown 并保存到 ~/cresus-bot/reports/"""

import json
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

BOT_DIR    = Path.home() / "cresus-bot"
REPORT_DIR = BOT_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None

def load_jsonl(path):
    try:
        return [json.loads(l) for l in Path(path).read_text().strip().splitlines() if l.strip()]
    except Exception:
        return []

def fmt_usdt(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f} USDT"

def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v*100:.1f}%"

def age_str(ts_str):
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        mins = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
        if mins < 60:
            return f"{mins}m 前"
        return f"{mins//60}h{mins%60}m 前"
    except Exception:
        return ts_str[:16]

def main():
    today     = date.today()
    today_str = str(today)
    yesterday = str(today - timedelta(days=1))
    report_path = REPORT_DIR / f"{today_str}.md"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    add = lines.append

    add(f"# Crésus 每日报告 · {today_str}")
    add(f"生成时间：{now_str}\n")
    add("---\n")

    # ── Regime ───────────────────────────────────────────────────────────────
    regime = load_json(BOT_DIR / "regime.json")
    REGIME_ZH = {
        "ALT_SEASON_RUNNING": "山寨行情",
        "BTC_DOMINATING":     "BTC 主导",
        "RISK_OFF":           "风险释放",
        "PUMP_AND_DUMP_RISK": "拉砸风险",
        "RANGE_BORING":       "盘整死水",
    }
    if regime:
        rname = regime.get("regime", "—")
        add("## 📊 行情体制（V3 Regime）")
        add(f"| 体制 | 信心值 | 更新 |")
        add(f"|------|--------|------|")
        add(f"| **{REGIME_ZH.get(rname, rname)}** `{rname}` | {regime.get('confidence','—')} | {age_str(regime.get('ts',''))} |")
        add(f"\n> {regime.get('judgment','—')}\n")
    else:
        add("## 📊 行情体制（V3 Regime）\n_未加载_\n")

    # ── PnL ──────────────────────────────────────────────────────────────────
    pnl = load_json(BOT_DIR / "pnl.json")
    if pnl:
        s = pnl.get("summary", {})
        add("## 💰 今日 PnL")
        today_real  = s.get("today_realized_pnl")
        today_fees  = s.get("today_fees")
        net_today   = (today_real or 0) + (today_fees or 0)
        total_real  = s.get("total_realized_pnl")
        add(f"| 指标 | 今日 | 累计 |")
        add(f"|------|------|------|")
        add(f"| 已实现 PnL | {fmt_usdt(today_real)} | {fmt_usdt(total_real)} |")
        add(f"| 手续费     | {fmt_usdt(today_fees)} | — |")
        add(f"| 净收益     | **{fmt_usdt(net_today)}** | — |")
        add(f"| 开仓/平仓  | {s.get('today_opened','—')} / {s.get('today_closed','—')} | {s.get('all_time_closed','—')} 次 |")
        tw, tl = s.get("today_wins",0), s.get("today_losses",0)
        aw, al = s.get("all_time_wins",0), s.get("all_time_losses",0)
        add(f"| 胜率       | {fmt_pct(s.get('win_rate'))} ({tw}W/{tl}L) | {fmt_pct(s.get('all_time_win_rate'))} ({aw}W/{al}L) |")

        # 今日最佳/最差
        closes = pnl.get("closed_recent", [])
        today_closes = [c for c in closes if (c.get("ts") or "").startswith(today_str)]
        if today_closes:
            best  = max(today_closes, key=lambda c: c.get("net", 0))
            worst = min(today_closes, key=lambda c: c.get("net", 0))
            add(f"\n**今日最佳**：`{best['symbol']}` {best.get('side','?')} {fmt_usdt(best.get('net'))}")
            add(f"**今日最差**：`{worst['symbol']}` {worst.get('side','?')} {fmt_usdt(worst.get('net'))}")

        # 当前持仓
        positions = pnl.get("positions", [])
        if positions:
            add(f"\n**当前持仓（{len(positions)} 个）**")
            for p in positions:
                add(f"- `{p.get('symbol','?')}` {p.get('side','?')} × {p.get('size','?')}  未实现 {fmt_usdt(p.get('unrealized_pnl'))}")
        else:
            add("\n当前持仓：无")
        add("")

    # ── Signals ───────────────────────────────────────────────────────────────
    all_signals = load_jsonl(BOT_DIR / "signals.jsonl")
    today_sigs  = [s for s in all_signals if (s.get("ts") or "").startswith(today_str)]

    dir_cnt = defaultdict(int)
    for s in today_sigs:
        dir_cnt[s.get("direction","?")] += 1

    add("## 🔔 今日信号")
    add(f"总数 **{len(today_sigs)}** 条  ·  多 {dir_cnt['LONG']} · 空 {dir_cnt['SHORT']} · 观察 {dir_cnt['WATCH']} · 跳过 {dir_cnt['SKIP']}")

    # 路由分布
    routed = defaultdict(int)
    for s in today_sigs:
        routed[s.get("routed_to") or "—"] += 1
    if routed:
        route_parts = "  ·  ".join(f"{k} {v}" for k,v in sorted(routed.items(), key=lambda x:-x[1]))
        add(f"路由分布：{route_parts}")
    add("")

    # 高信心信号
    high = [s for s in today_sigs if (s.get("confidence") or 0) >= 70]
    if high:
        add("### ⭐ 高信心信号（≥70）")
        for s in sorted(high, key=lambda x: x.get("confidence",0), reverse=True):
            tp = s.get("take_profit", "—")
            if isinstance(tp, list):
                tp = " / ".join(str(x) for x in tp)
            route = s.get("routed_to") or "—"
            add(f"- `{s['symbol']}` **{s.get('direction')}** conf={s.get('confidence')}  结构{s.get('structure','?')}  [{route}]")
            add(f"  入场 {s.get('entry_price','?')}  止损 {s.get('stop_loss','?')}  止盈 {tp}")
        add("")

    # 被阻断信号
    blocked = [s for s in today_sigs if s.get("block_reason")]
    if blocked:
        add(f"### 🚫 被阻断信号（{len(blocked)} 条）")
        for s in blocked:
            add(f"- `{s['symbol']}` {s.get('direction')} conf={s.get('confidence')}  原因：{s.get('block_reason')}")
        add("")

    # ── 错误日志摘要 ──────────────────────────────────────────────────────────
    err_file = BOT_DIR / "bot.err"
    if err_file.exists():
        add("## ⚠️ 今日错误摘要")
        try:
            err_lines = err_file.read_text().splitlines()
            today_errs = [l for l in err_lines if today_str in l or yesterday in l]
            # 只取包含 ERROR/WARNING/fail/sCode 的关键行
            keywords = ("ERROR", "WARNING", "fail", "sCode", "51", "exception", "Exception")
            key_errs = [l for l in today_errs if any(k in l for k in keywords)]
            shown = key_errs[-15:] if key_errs else []
            if shown:
                add("```")
                lines.extend(shown)
                add("```")
            else:
                add("今日无关键错误 ✅")
        except Exception as e:
            add(f"读取失败：{e}")
        add("")

    # ── 手动备注 ──────────────────────────────────────────────────────────────
    add("## 📝 今日观察（手动填写）")
    add("")
    add("- 行情感受：")
    add("- 信号质量：")
    add("- 策略问题：")
    add("- 明日关注：")
    add("")
    add("---")
    add(f"_Crésus daily_report.py · {now_str}_")

    report = "\n".join(lines)
    report_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n✅ 已保存：{report_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
