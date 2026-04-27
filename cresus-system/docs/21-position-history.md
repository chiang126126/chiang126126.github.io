# P15：仓位历史看板

把「最近平仓」面板升级为「📜 仓位历史」，加入：
- 一句话日总结：今日开 N 笔 / 平 N 笔 / 爆仓 N 笔 / 已实现 / 手续费 / 净盈亏 (USDT)
- 完整列：时间 / Symbol / 方向 / **入场价** / **出场价** / 盈亏 / 手续费 / 净 / 类型（正常/爆仓）

---

## 数据流

```
OKX bills（subType 3/4 开 + 5/6 平 + type 5 爆仓）
    ↓
fetch_pnl.py
    ├─ 按 ts 排序所有 bills
    ├─ 维护 opens map（FIFO 队列）
    ├─ 遇到 close bill：从 opens 队列匹配最早同方向开仓，计算 entry+exit
    ├─ 标记 type=5 为 liquidated
    └─ 输出 summary.today_opened/today_liquidated/today_fees + closed_recent[]
        ↓
pnl.json
    ↓
dashboard.renderHistory()
```

---

## `scripts/fetch_pnl.py` 完整重写

```bash
cat > ~/cresus-bot/scripts/fetch_pnl.py << 'PYEOF'
"""Fetch OKX demo positions + bills, match opens/closes, output to pnl.json."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OKX = shutil.which("okx") or "okx"
OUT_FILE = Path.home() / "cresus-bot" / "pnl.json"


def _run(args):
    cmd = [OKX] + args + ["--demo", "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def fetch_positions():
    raw = _run(["account", "positions"])
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw:
        try:
            pos_size = float(p.get("pos") or 0)
            if pos_size == 0:
                continue
            out.append({
                "symbol": p.get("instId", ""),
                "side": p.get("posSide", "net"),
                "size": pos_size,
                "entry": float(p.get("avgPx") or 0),
                "mark": float(p.get("markPx") or 0),
                "upl": float(p.get("upl") or 0),
                "upl_ratio": float(p.get("uplRatio") or 0),
                "lever": int(float(p.get("lever") or 1)),
                "ctime": p.get("cTime", ""),
            })
        except (ValueError, TypeError):
            continue
    return out


def fetch_trade_history():
    """Process bills sequentially, match opens with closes via FIFO."""
    raw = _run(["account", "bills"])
    if not isinstance(raw, list):
        return []

    # Sort by timestamp ascending
    bills = sorted(raw, key=lambda b: int(b.get("ts") or 0))

    opens = {}   # {symbol: [{ts, px, sz, side}, ...]}  FIFO queue
    trades = [] # closed trade records

    for b in bills:
        sub = str(b.get("subType", ""))
        bill_type = str(b.get("type", ""))
        symbol = b.get("instId", "")
        ts_ms = int(b.get("ts") or 0)
        try:
            px = float(b.get("px") or 0)
            sz = abs(float(b.get("sz") or 0))
        except ValueError:
            continue

        if sub in ("3", "4"):  # open long / short
            side = "long" if sub == "3" else "short"
            opens.setdefault(symbol, []).append({
                "ts": ts_ms, "px": px, "sz": sz, "side": side,
            })
        elif sub in ("5", "6"):  # close long / short
            close_side = "long" if sub == "5" else "short"
            try:
                pnl = float(b.get("pnl") or 0)
                fee = float(b.get("fee") or 0)
            except ValueError:
                pnl, fee = 0.0, 0.0

            queue = opens.get(symbol, [])
            matched = None
            for o in queue:
                if o["side"] == close_side:
                    matched = o
                    queue.remove(o)
                    break

            trades.append({
                "symbol": symbol,
                "side": close_side,
                "open_px": matched["px"] if matched else None,
                "open_ts": matched["ts"] if matched else None,
                "close_px": px,
                "size": sz,
                "pnl": pnl,
                "fee": fee,
                "net": pnl + fee,
                "ts": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                "bill_id": b.get("billId", ""),
                "liquidated": bill_type == "5",
            })

    return sorted(trades, key=lambda t: t["ts"], reverse=True)


def count_today_opens():
    """Count subType 3/4 bills timestamped today (UTC)."""
    raw = _run(["account", "bills"])
    if not isinstance(raw, list):
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for b in raw:
        sub = str(b.get("subType", ""))
        if sub not in ("3", "4"):
            continue
        ts_ms = int(b.get("ts") or 0)
        d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        if d == today:
            n += 1
    return n


def summarize(positions, trades, today_opens):
    today = datetime.now(timezone.utc).date().isoformat()
    today_trades = [t for t in trades if t["ts"][:10] == today]

    total_upl = sum(p["upl"] for p in positions)
    today_realized = sum(t["pnl"] for t in today_trades)
    today_fees     = sum(t["fee"] for t in today_trades)
    today_wins     = sum(1 for t in today_trades if t["net"] > 0)
    today_losses   = sum(1 for t in today_trades if t["net"] < 0)
    today_liq      = sum(1 for t in today_trades if t.get("liquidated"))
    total_today    = today_wins + today_losses
    win_rate       = (today_wins / total_today) if total_today > 0 else 0
    total_realized = sum(t["net"] for t in trades)

    return {
        "open_positions":       len(positions),
        "total_unrealized_pnl": round(total_upl, 4),
        "today_opened":         today_opens,
        "today_closed":         len(today_trades),
        "today_liquidated":     today_liq,
        "today_realized_pnl":   round(today_realized, 4),
        "today_fees":           round(today_fees, 4),
        "today_wins":           today_wins,
        "today_losses":         today_losses,
        "win_rate":             round(win_rate, 4),
        "total_realized_pnl":   round(total_realized, 4),
    }


def main():
    positions = fetch_positions()
    trades    = fetch_trade_history()
    today_opens = count_today_opens()
    summary = summarize(positions, trades, today_opens)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary":    summary,
        "positions":  positions,
        "closed_recent": trades[:50],
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[pnl] open={summary['open_positions']} "
        f"upl={summary['total_unrealized_pnl']} "
        f"today_opened={summary['today_opened']} "
        f"today_closed={summary['today_closed']} "
        f"today_liq={summary['today_liquidated']} "
        f"today_realized={summary['today_realized_pnl']} "
        f"win_rate={summary['win_rate']:.1%}"
    )


if __name__ == "__main__":
    main()
PYEOF
ls -la ~/cresus-bot/scripts/fetch_pnl.py
```

---

## 测试

```bash
cd ~/cresus-bot && uv run python scripts/fetch_pnl.py
cat ~/cresus-bot/pnl.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
s = d['summary']
print(f'今日: {s[\"today_opened\"]} 开 / {s[\"today_closed\"]} 平 / {s[\"today_liquidated\"]} 爆仓')
print(f'已实现: {s[\"today_realized_pnl\"]} | 手续费: {s[\"today_fees\"]} | 净: {s[\"today_realized_pnl\"]+s[\"today_fees\"]:.2f}')
print(f'最近平仓 {len(d[\"closed_recent\"])} 笔')
for t in d['closed_recent'][:3]:
    print(f\"  {t['symbol']:18s} {t['side']:5s} 入={t['open_px']} 出={t['close_px']} pnl={t['pnl']} {'(爆仓)' if t.get('liquidated') else ''}\")
"
```

---

## 看板新面板

```
📜 仓位历史   今日 12 开 · 8 平 · 1 爆仓 · 已实现 +5.32 · 手续费 -1.40 · 净 +3.92 USDT

时间      Symbol         方向   入场      出场      盈亏      手续费    净      类型
17:01    LDOUSDT-SWAP   平多   0.3969    0.4123   +0.15    0.0023   +0.15   正常
16:45    XPLUSDT-SWAP   平空   0.1009    0.0875   +0.13    0.0019   +0.13   正常
14:22    BTCUSDT-SWAP   平多   77380     76800    -5.80    0.5800   -6.38   爆仓
...
```

---

## ✅ P15 检查清单

- [ ] `fetch_pnl.py` 重写
- [ ] `pnl-tracker.plist` 不变（仍每 5 分钟跑）
- [ ] 看板 closed-panel → history-panel 替换
- [ ] renderClosed → renderHistory 重写
- [ ] 手机看版能看到一句话总结 + 完整表

---

## 已知限制

| 限制 | 说明 |
|------|------|
| OKX bills 只返回最近一段 | 默认拉最近 100 条；要更多用 `--archive` |
| open/close FIFO 匹配 | 同一币种多次开仓时，按时间顺序匹配。极少数情况可能错配 |
| 部分平仓 | 若一次平了一半再平一半，会拆成两条记录 |
| open_ts 不在窗口里 | 老仓位的 open bill 已被冲掉 → entry 显示 `—` |
