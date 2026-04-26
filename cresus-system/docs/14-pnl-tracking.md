# P7：P&L 持仓追踪

P5 OKX 纸交易开启后，下单成功 ≠ 赚钱。本阶段把 OKX 演示账户的实时持仓 + 历史平仓数据接回看板，形成完整闭环：**信号 → 下单 → 持仓 → 盈亏**。

---

## 数据流

```
launchd 每 5 分钟
    ↓
fetch_pnl.py
    ├─ okx account positions --demo --json   ← 当前持仓 + 浮动盈亏
    ├─ okx account bills --demo --json       ← 全量成交流水（含已实现 PnL）
    └─ 聚合 → pnl.json
        ↓
sync_signals.sh 推送到公共仓库
    ↓
dashboard/index.html 渲染
    ├─ 顶部 P&L 概要卡（今日盈亏 / 胜率 / 持仓数）
    ├─ 当前持仓表
    └─ 最近平仓表
```

---

## `scripts/fetch_pnl.py`

放在 `~/cresus-bot/scripts/fetch_pnl.py`：

```python
"""Fetch OKX demo positions + bills and aggregate to pnl.json for dashboard."""
import json
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

OKX = shutil.which("okx") or "okx"
OUT_FILE = Path.home() / "cresus-bot" / "pnl.json"


def _run(args: list[str]) -> list | dict:
    cmd = [OKX] + args + ["--demo", "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def fetch_positions() -> list[dict]:
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


def fetch_bills() -> list[dict]:
    """Recent bills (close trades = realized PnL)."""
    raw = _run(["account", "bills"])
    if not isinstance(raw, list):
        return []
    closes = []
    for b in raw:
        # subType 5 = close long, 6 = close short
        sub = str(b.get("subType", ""))
        if sub not in ("5", "6"):
            continue
        try:
            pnl = float(b.get("pnl") or 0)
            fee = float(b.get("fee") or 0)
            ts_ms = int(b.get("ts") or 0)
            closes.append({
                "symbol": b.get("instId", ""),
                "side": "long" if sub == "5" else "short",
                "pnl": pnl,
                "fee": fee,
                "net": pnl + fee,
                "ts": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                "bill_id": b.get("billId", ""),
            })
        except (ValueError, TypeError):
            continue
    return sorted(closes, key=lambda x: x["ts"], reverse=True)


def summarize(positions: list[dict], closes: list[dict]) -> dict:
    today = datetime.now(timezone.utc).date()
    today_closes = [c for c in closes if c["ts"][:10] == today.isoformat()]

    total_upl = sum(p["upl"] for p in positions)
    today_realized = sum(c["net"] for c in today_closes)
    today_wins = sum(1 for c in today_closes if c["net"] > 0)
    today_losses = sum(1 for c in today_closes if c["net"] < 0)
    total_today = today_wins + today_losses
    win_rate = (today_wins / total_today) if total_today > 0 else 0
    total_realized = sum(c["net"] for c in closes)

    return {
        "open_positions": len(positions),
        "total_unrealized_pnl": round(total_upl, 4),
        "today_realized_pnl": round(today_realized, 4),
        "today_closed": total_today,
        "today_wins": today_wins,
        "today_losses": today_losses,
        "win_rate": round(win_rate, 4),
        "total_realized_pnl": round(total_realized, 4),
    }


def main() -> None:
    positions = fetch_positions()
    closes = fetch_bills()
    summary = summarize(positions, closes)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "positions": positions,
        "closed_recent": closes[:50],
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pnl] open={summary['open_positions']} "
          f"upl={summary['total_unrealized_pnl']} "
          f"today_realized={summary['today_realized_pnl']} "
          f"win_rate={summary['win_rate']:.1%}")


if __name__ == "__main__":
    main()
```

测试：

```bash
uv run python ~/cresus-bot/scripts/fetch_pnl.py
cat ~/cresus-bot/pnl.json
```

预期输出：

```json
{
  "updated_at": "2026-04-25T22:35:00.000000+00:00",
  "summary": {
    "open_positions": 1,
    "total_unrealized_pnl": -0.5,
    "today_realized_pnl": 0,
    ...
  },
  "positions": [{"symbol": "ETH-USDT-SWAP", "side": "long", ...}],
  "closed_recent": []
}
```

---

## launchd 每 5 分钟

`~/Library/LaunchAgents/com.cresus.pnl-tracker.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cresus.pnl-tracker</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>export PATH="/Users/mangzi/.local/bin:/usr/local/bin:/usr/bin:/bin"; cd /Users/mangzi/cresus-bot; exec uv run python scripts/fetch_pnl.py</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>StandardOutPath</key>
  <string>/Users/mangzi/cresus-bot/logs/pnl.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/mangzi/cresus-bot/logs/pnl.err</string>
</dict>
</plist>
```

加载：

```bash
launchctl load ~/Library/LaunchAgents/com.cresus.pnl-tracker.plist
launchctl list | grep pnl-tracker
```

---

## 同步 `pnl.json` 到看板

修改 `~/cresus-bot/scripts/sync_signals.sh`：

```bash
#!/bin/bash
set -e
PUBLIC_REPO=~/chiang126126.github.io
cd "$PUBLIC_REPO"
git checkout main
git pull --quiet

cp ~/cresus-bot/signals.jsonl cresus-system/dashboard/signals.jsonl
cp ~/cresus-bot/pnl.json      cresus-system/dashboard/pnl.json

if ! git diff --quiet cresus-system/dashboard/; then
  git add cresus-system/dashboard/signals.jsonl cresus-system/dashboard/pnl.json
  git commit -m "data: sync signals + pnl ($(date '+%Y-%m-%d %H:%M'))"
  git push
fi
```

---

## ✅ P7 完成检查清单

- [ ] `~/cresus-bot/scripts/fetch_pnl.py` 写入
- [ ] `uv run python scripts/fetch_pnl.py` 测试输出 `pnl.json`
- [ ] `~/Library/LaunchAgents/com.cresus.pnl-tracker.plist` 加载
- [ ] `launchctl list | grep pnl-tracker` 显示 PID
- [ ] `sync_signals.sh` 增加 pnl.json 同步
- [ ] 看板 P&L 面板正常显示（持仓数 / 浮盈 / 当日胜率）

全部打勾 = P7 P&L 追踪完成。

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P8** | 每日 AI 复盘：23:55 触发 Hermes，生成胜率报告 → Discord |
| **P9** | 风控层：单笔限额、日亏损熔断、持仓数上限、反向信号自动平仓 |
| **P10** | OCO 止损：进场同时挂止损 + 止盈条件单 |
