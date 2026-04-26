# P8：每日 AI 复盘

每天 23:55 触发 Hermes 分析当日所有信号 + OKX 实际盈亏，生成胜率报告，推到 Discord + 写入 Obsidian。

---

## 数据流

```
launchd Calendar 23:55 (Asia/Hong_Kong)
    ↓
daily_review.py
    ├─ 读取 signals.jsonl（当日所有信号）
    ├─ 读取 pnl.json（持仓 + 已实现 P&L）
    ├─ 拼装 prompt
    └─ 调用 hermes chat -Q
        ↓
    ├─ 写入 ~/vault/reports/YYYY-MM-DD.md
    └─ 推送到 Discord #scan-log
```

---

## `scripts/daily_review.py`

```python
"""Daily AI review: aggregate today's signals + PnL → Hermes → Discord + Obsidian."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

SIGNALS_FILE = Path.home() / "cresus-bot" / "signals.jsonl"
PNL_FILE     = Path.home() / "cresus-bot" / "pnl.json"
PROMPT_FILE  = Path.home() / "cresus-bot" / "prompts" / "daily_review.txt"
VAULT_DIR    = Path.home() / "vault" / "reports"

HERMES = shutil.which("hermes") or "hermes"
HK_TZ  = timezone(timedelta(hours=8))


def load_signals_today() -> list[dict]:
    if not SIGNALS_FILE.exists():
        return []
    today = datetime.now(HK_TZ).date().isoformat()
    out = []
    for line in SIGNALS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            ts_local = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone(HK_TZ)
            if ts_local.date().isoformat() == today:
                out.append(r)
        except Exception:
            continue
    return out


def load_pnl() -> dict:
    if not PNL_FILE.exists():
        return {}
    try:
        return json.loads(PNL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_input(signals: list[dict], pnl: dict) -> str:
    today = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    summary = pnl.get("summary", {})

    sig_compact = []
    for s in signals:
        sig_compact.append({
            "symbol": s.get("symbol"),
            "direction": s.get("direction"),
            "confidence": s.get("confidence"),
            "leverage": s.get("leverage"),
            "entry": s.get("entry_price"),
            "anomaly": s.get("anomaly", []),
            "routed_to": s.get("routed_to"),
        })

    payload = {
        "date": today,
        "signal_stats": {
            "total": len(signals),
            "long":  sum(1 for s in signals if s.get("direction") == "LONG"),
            "short": sum(1 for s in signals if s.get("direction") == "SHORT"),
            "watch": sum(1 for s in signals if s.get("direction") == "WATCH"),
            "high_confidence": sum(1 for s in signals if (s.get("confidence") or 0) >= 70),
        },
        "okx_pnl": {
            "open_positions": summary.get("open_positions", 0),
            "unrealized_pnl": summary.get("total_unrealized_pnl", 0),
            "today_realized_pnl": summary.get("today_realized_pnl", 0),
            "today_closed": summary.get("today_closed", 0),
            "today_wins": summary.get("today_wins", 0),
            "today_losses": summary.get("today_losses", 0),
            "win_rate": summary.get("win_rate", 0),
        },
        "open_positions": pnl.get("positions", []),
        "today_closes": [
            c for c in pnl.get("closed_recent", [])
            if c.get("ts", "")[:10] == datetime.now(timezone.utc).date().isoformat()
        ],
        "signals": sig_compact[:50],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def call_hermes(prompt: str, payload: str) -> str:
    full = prompt + "\n\n" + payload
    cmd = [HERMES, "chat", "-Q", "--source", "tool", "--max-turns", "8", "-q", full]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return f"Hermes 调用失败：{r.stderr.strip()}"
    out = r.stdout.strip()
    for marker in ("\nResume this session with:", "\nSession:", "\nsession_id:", "\nDuration:"):
        if marker in out:
            out = out.split(marker)[0].rstrip()
    return out


def write_obsidian(today: str, content: str, payload: str) -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    p = VAULT_DIR / f"{today}.md"
    text = f"""---
date: {today}
type: daily-review
---

# Crésus 每日复盘 · {today}

{content}

---

## 原始数据

```json
{payload}
```
"""
    p.write_text(text, encoding="utf-8")
    return p


def push_discord(today: str, content: str, signal_count: int, pnl_summary: dict) -> None:
    channel = os.environ.get("DISCORD_CHANNEL_SCAN_LOG") or os.environ.get("DISCORD_CHANNEL_ANALYSIS")
    token   = os.environ.get("DISCORD_BOT_TOKEN")
    if not (channel and token):
        return

    realized = pnl_summary.get("today_realized_pnl", 0)
    upl      = pnl_summary.get("total_unrealized_pnl", 0)
    win_rate = pnl_summary.get("win_rate", 0) * 100
    closed   = pnl_summary.get("today_closed", 0)

    color = 0x00c853 if realized > 0 else (0xff1744 if realized < 0 else 0x2196f3)

    embed = {
        "title": f"📊 每日复盘 · {today}",
        "color": color,
        "fields": [
            {"name": "信号", "value": f"{signal_count} 条", "inline": True},
            {"name": "今日已实现", "value": f"{realized:+.2f} USDT", "inline": True},
            {"name": "浮动盈亏", "value": f"{upl:+.2f} USDT", "inline": True},
            {"name": "今日胜率", "value": f"{win_rate:.0f}% ({closed} 笔)", "inline": True},
            {"name": "Hermes 复盘", "value": content[:1000] + ("…" if len(content) > 1000 else ""), "inline": False},
        ],
    }

    try:
        httpx.post(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            headers={"Authorization": token, "Content-Type": "application/json"},
            json={"embeds": [embed]},
            timeout=15,
        )
    except Exception as e:
        print(f"[review] discord push failed: {e}")


def main() -> None:
    today = datetime.now(HK_TZ).strftime("%Y-%m-%d")
    signals = load_signals_today()
    pnl     = load_pnl()

    print(f"[review] {today} signals={len(signals)} positions={len(pnl.get('positions', []))}")

    if not PROMPT_FILE.exists():
        print(f"[review] missing prompt: {PROMPT_FILE}")
        return

    prompt  = PROMPT_FILE.read_text(encoding="utf-8")
    payload = build_input(signals, pnl)
    review  = call_hermes(prompt, payload)

    md_path = write_obsidian(today, review, payload)
    print(f"[review] obsidian: {md_path}")

    push_discord(today, review, len(signals), pnl.get("summary", {}))
    print("[review] done")


if __name__ == "__main__":
    main()
```

---

## `prompts/daily_review.txt`

```
你是 Crésus 加密信号系统的每日复盘助手。

任务：基于下面的当日 JSON 数据（信号 + OKX 演示账户实际持仓与盈亏），用中文 markdown 输出每日复盘报告。总字数 ≤ 600 字。

报告必须包含 4 个段落：

## 一、当日总结
- 信号数量分布、平均信心度、最高信心信号
- 演示账户当日已实现盈亏 / 浮动盈亏 / 胜率

## 二、亮点信号
- 列出 1-3 个表现最好的信号（按已实现盈亏或浮盈排序）
- 简述为什么有效（结构匹配、异常指标命中）

## 三、问题信号
- 列出 1-2 个亏损或方向错误的信号
- 分析失败原因（结构误判、风控漏洞、市场环境变化）

## 四、明日建议
- 是否调整 confidence 阈值
- 是否需要规避特定结构 / 时段
- 风控提示

—— 当日数据 JSON 见下文 ——
```

---

## launchd 每日 23:55

`~/Library/LaunchAgents/com.cresus.daily-review.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cresus.daily-review</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>export PATH="/Users/mangzi/.local/bin:/usr/local/bin:/usr/bin:/bin"; cd /Users/mangzi/cresus-bot; set -a; source .env; set +a; exec uv run python scripts/daily_review.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>23</integer>
    <key>Minute</key><integer>55</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/mangzi/cresus-bot/logs/review.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/mangzi/cresus-bot/logs/review.err</string>
</dict>
</plist>
```

> launchd 用系统时区。Mac 系统时区是香港，触发时间就是 23:55 HKT。

---

## ✅ P8 完成检查清单

- [ ] `~/cresus-bot/scripts/daily_review.py` 写入
- [ ] `~/cresus-bot/prompts/daily_review.txt` 写入
- [ ] 手动测试：`uv run python scripts/daily_review.py`
- [ ] `~/vault/reports/YYYY-MM-DD.md` 生成
- [ ] Discord #scan-log 收到复盘 Embed
- [ ] launchd plist 加载，`launchctl list | grep daily-review` 显示
- [ ] 第二天 23:55 自动触发（次日早上检查 `~/cresus-bot/logs/review.log`）

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P9** | 风控层：单笔限额、日亏损熔断、持仓数上限、反向信号自动平仓 |
| **P10** | OCO 止损：进场同时挂止损 + 止盈条件单 |
| **P11** | 看板加复盘历史页：`/dashboard/reports/` 列出每日复盘 |
