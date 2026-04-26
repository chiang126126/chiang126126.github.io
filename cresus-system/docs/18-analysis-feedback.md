# P11：Hermes 分析反馈 → 看板（操作建议 + 风险评估）

之前 Hermes 二次分析的「操作建议」「风险评估」只写到 Obsidian markdown，看板看不到。本阶段闭环：把 Hermes 输出的结构化字段写到 `analyses.jsonl`，看板按 `ts` join 后多渲染 3 列。

---

## 数据流

```
hermes_bridge.py（每 30s 轮询）
    ├── 读取 signals.jsonl 新增的信号
    ├── 调用 hermes chat -Q
    ├── 写 ~/vault/signals/YYYY-MM/SYMBOL_*.md   ← 原行为
    ├── 解析 Hermes 输出结构化字段              ← 新增
    │   ├── recommendation (跟/观察/不跟)
    │   ├── risk_level (低/中/高)
    │   ├── risk_score (0-12)
    │   ├── structure_match (高/中/低)
    │   └── structure_judge (A型 OI 积累做多 等)
    ├── 写 ~/cresus-bot/analyses.jsonl         ← 新增
    └── （可选）推 Discord #analysis            ← 原行为

sync_signals.sh
    └── cp analyses.jsonl 到公共仓库

dashboard/index.html
    ├── fetch signals.jsonl
    ├── fetch analyses.jsonl
    ├── 按 ts 建立 map
    └── 信号表格新增 3 列：推荐 / 风险 / 结构匹配
```

---

## `analyses.jsonl` 字段

```json
{
  "ts": "2026-04-26T17:16:38.596182+00:00",
  "symbol": "PIPPINUSDT",
  "direction": "LONG",
  "confidence": 70,
  "structure_match": "高",
  "structure_judge": "A型 OI积累做多",
  "risk_level": "低",
  "risk_score": 0,
  "recommendation": "观察",
  "reason": "结构偏多没问题，但缺少 MA20 偏离与资金费率精确值..."
}
```

ts + symbol 是 join key，一一对应 signals.jsonl 中的同一条信号。

---

## Hermes 输出解析

Hermes 标准输出格式（v0.7 prompt）：

```markdown
## 结构复核
- 框架判断：`A型 OI积累做多`
- 合理性：`合理`
- 匹配度：`高`
- 依据：...

## 风险评估
- MA20 偏离>30%：`缺数据`
- OI 24h >50%：`0`
- 资金费率绝对值>0.1%：`0`
- 北京高峰时段 UTC21-00：`0`
- 总分：`0（按已知项）` → `低风险`

## 操作建议
- `观察`
- 理由：...
```

正则提取：

| 字段 | 正则 |
|------|------|
| `structure_match` | `匹配度[:：]\s*[`'\"]*(高\|中\|低)` |
| `structure_judge` | `框架判断[:：]\s*[`'\"]*([^`'\"\n]+)` |
| `risk_level` | `(低\|中\|高)\s*风险` |
| `risk_score` | `总分[:：]\s*[`'\"]*(\d+)` |
| `recommendation` | `##\s*操作建议[\s\S]*?[`'\"]*(跟\|观察\|不跟)` |
| `reason` | `理由[:：]\s*([^\n]+)` |

---

## 完整 `hermes_bridge.py`

```bash
cat > ~/cresus-bot/scripts/hermes_bridge.py << 'PYEOF'
"""Hermes bridge: poll signals.jsonl -> hermes review -> obsidian + analyses.jsonl + Discord."""
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

POLL_INTERVAL = 30
SIGNALS_FILE  = Path.home() / "cresus-bot" / "signals.jsonl"
ANALYSES_FILE = Path.home() / "cresus-bot" / "analyses.jsonl"
OFFSET_FILE   = Path.home() / "cresus-bot" / ".hermes_offset"
PROMPT_FILE   = Path.home() / "cresus-bot" / "prompts" / "hermes_analyst.txt"
VAULT_BASE    = Path.home() / "vault" / "signals"

HERMES = shutil.which("hermes") or "hermes"
DISCORD_CHANNEL = os.environ.get("DISCORD_CHANNEL_ANALYSIS")
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN")

_FOOTER_MARKERS = (
    "\nResume this session with:",
    "\nSession:",
    "\nsession_id:",
    "\nDuration:",
)


def load_offset():
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip())
        except ValueError:
            return 0
    return 0


def save_offset(n):
    OFFSET_FILE.write_text(str(n))


def read_new_signals(offset):
    if not SIGNALS_FILE.exists():
        return []
    lines = SIGNALS_FILE.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(lines[offset:], start=offset):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            r["_line_no"] = i
            out.append(r)
        except Exception:
            continue
    return out


def call_hermes(prompt, signal_json):
    full = prompt + "\n\n" + signal_json
    cmd = [HERMES, "chat", "-Q", "--source", "tool", "--max-turns", "5", "-q", full]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return f"Hermes error: {r.stderr.strip()}"
    out = r.stdout.strip()
    for marker in _FOOTER_MARKERS:
        if marker in out:
            out = out.split(marker)[0].rstrip()
    return out


def parse_review(md):
    """Extract structured fields from Hermes review markdown."""
    out = {
        "structure_match": None,
        "structure_judge": None,
        "risk_level": None,
        "risk_score": None,
        "recommendation": None,
        "reason": None,
    }
    m = re.search(r"匹配度[:：]\s*[`'\"]*(高|中|低)", md)
    if m: out["structure_match"] = m.group(1)
    m = re.search(r"框架判断[:：]\s*[`'\"]*([^`'\"\n]+)", md)
    if m: out["structure_judge"] = m.group(1).strip()
    m = re.search(r"(低|中|高)\s*风险", md)
    if m: out["risk_level"] = m.group(1)
    m = re.search(r"总分[:：]\s*[`'\"]*(\d+)", md)
    if m:
        try: out["risk_score"] = int(m.group(1))
        except ValueError: pass
    m = re.search(r"##\s*操作建议[\s\S]*?[`'\"]*(跟|观察|不跟)", md)
    if m: out["recommendation"] = m.group(1)
    m = re.search(r"理由[:：]\s*([^\n]+)", md)
    if m: out["reason"] = m.group(1).strip()[:200]
    return out


def write_obsidian(signal, review):
    ts = datetime.fromisoformat(signal["ts"].replace("Z", "+00:00")).astimezone()
    month_dir = VAULT_BASE / ts.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    fn = f"{signal['symbol']}_{signal['direction']}_{ts.strftime('%Y%m%d_%H%M')}.md"
    p = month_dir / fn

    tp = signal.get("take_profit")
    tp_str = json.dumps(tp) if isinstance(tp, list) else (str(tp) if tp else "")

    fm = (
        "---\n"
        f"ts: {signal['ts']}\n"
        f"symbol: {signal['symbol']}\n"
        f"direction: {signal['direction']}\n"
        f"confidence: {signal['confidence']}\n"
        f"entry: {signal.get('entry_price', '')}\n"
        f"stop: {signal.get('stop_loss', '')}\n"
        f"tp: {tp_str}\n"
        "status: pending\n"
        "---\n\n"
    )
    body = (
        "## 原始信号\n\n"
        f"> {signal.get('reasoning', '')}\n\n"
        f"**异常指标**：{signal.get('anomaly', [])}\n\n"
        "## Hermes 二次分析\n\n"
        f"{review}\n"
    )
    p.write_text(fm + body, encoding="utf-8")
    return p


def append_analysis(signal, parsed, md_path):
    record = {
        "ts": signal["ts"],
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "confidence": signal["confidence"],
        **parsed,
        "review_md": str(md_path),
    }
    ANALYSES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ANALYSES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def push_discord(signal, review, parsed):
    if not (DISCORD_CHANNEL and DISCORD_TOKEN):
        return
    if signal.get("confidence", 0) < 75 or signal.get("direction") == "WATCH":
        return

    auth = DISCORD_TOKEN if DISCORD_TOKEN.startswith("Bot ") else f"Bot {DISCORD_TOKEN}"
    rec = parsed.get("recommendation") or "—"
    risk = parsed.get("risk_level") or "—"
    match = parsed.get("structure_match") or "—"
    color_map = {"跟": 0x00c853, "观察": 0xffd600, "不跟": 0xff1744}
    color = color_map.get(rec, 0x2196f3)
    embed = {
        "title": f"🧠 Hermes 分析 · {signal['symbol']} {signal['direction']}@{signal['confidence']}",
        "color": color,
        "fields": [
            {"name": "推荐", "value": rec, "inline": True},
            {"name": "风险", "value": risk, "inline": True},
            {"name": "结构匹配", "value": match, "inline": True},
            {"name": "复盘", "value": review[:1000] + ("..." if len(review) > 1000 else ""), "inline": False},
        ],
    }
    try:
        httpx.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL}/messages",
            headers={"Authorization": auth, "Content-Type": "application/json"},
            json={"embeds": [embed]},
            timeout=15,
        )
    except Exception as e:
        print(f"  ! discord push failed: {e}")


def main():
    print("🌉 Hermes 桥接启动")
    print(f"   监听：{SIGNALS_FILE}")
    offset = load_offset()
    print(f"   起始 offset：{offset}")
    print(f"   分析频道：{DISCORD_CHANNEL or '(未配置，跳过推送)'}")

    if not PROMPT_FILE.exists():
        print(f"   ❌ prompt 缺失：{PROMPT_FILE}")
        return
    prompt = PROMPT_FILE.read_text(encoding="utf-8")

    while True:
        new_sigs = read_new_signals(offset)
        for sig in new_sigs:
            line_no = sig.pop("_line_no", offset)
            ts = sig.get("ts", "")[:19]
            print(f"[{ts}] #{line_no+1} {sig['symbol']} {sig['direction']}@{sig['confidence']}")

            review = call_hermes(prompt, json.dumps(sig, ensure_ascii=False, indent=2))
            md_path = write_obsidian(sig, review)
            print(f"  → Obsidian: {md_path.name}")

            parsed = parse_review(review)
            append_analysis(sig, parsed, md_path)
            print(f"  → analyses.jsonl: rec={parsed['recommendation']} risk={parsed['risk_level']} match={parsed['structure_match']}")

            push_discord(sig, review, parsed)

            offset = line_no + 1
            save_offset(offset)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
PYEOF
ls -la ~/cresus-bot/scripts/hermes_bridge.py
```

---

## 重启 hermes-bridge

```bash
launchctl unload ~/Library/LaunchAgents/com.cresus.hermes-bridge.plist
launchctl load ~/Library/LaunchAgents/com.cresus.hermes-bridge.plist
sleep 5
launchctl list | grep hermes-bridge
```

---

## 测试一条已有信号

让 bridge 重新分析最近一条信号生成 analyses.jsonl 首条记录：

```bash
# 把 offset 退回 1 条
TOTAL=$(wc -l < ~/cresus-bot/signals.jsonl)
echo $((TOTAL - 1)) > ~/cresus-bot/.hermes_offset
sleep 60
tail -3 ~/cresus-bot/analyses.jsonl
```

应该看到一行 JSON，包含 `recommendation`、`risk_level`、`structure_match` 等字段。

---

## 同步脚本加 analyses.jsonl

```bash
cat > ~/cresus-bot/scripts/sync_signals.sh << 'SHEOF'
#!/bin/bash
set -e
PUBLIC_REPO=~/chiang126126.github.io
cd "$PUBLIC_REPO"
git checkout main
git pull --quiet
cp ~/cresus-bot/signals.jsonl  cresus-system/dashboard/signals.jsonl
cp ~/cresus-bot/pnl.json       cresus-system/dashboard/pnl.json
[ -f ~/cresus-bot/analyses.jsonl ] && cp ~/cresus-bot/analyses.jsonl cresus-system/dashboard/analyses.jsonl
if ! git diff --quiet cresus-system/dashboard/; then
  git add cresus-system/dashboard/
  git commit -m "data: sync signals + pnl + analyses ($(date '+%Y-%m-%d %H:%M'))"
  git push
fi
SHEOF
chmod +x ~/cresus-bot/scripts/sync_signals.sh
~/cresus-bot/scripts/sync_signals.sh
```

---

## 看板新增 3 列

`dashboard/index.html` 已在公共仓库由我修改提交，你只需 `git pull` main 后等 GitHub Pages 重建。新列：

| 推荐 | 风险 | 结构匹配 |
|------|------|----------|
| 🟢 跟 | 🟢 低 | 高 |
| 🟡 观察 | 🟡 中 | 中 |
| 🔴 不跟 | 🔴 高 | 低 |

---

## ✅ P11 完成检查清单

- [ ] `hermes_bridge.py` 重写完成
- [ ] hermes-bridge launchd 重启 exit 0
- [ ] `analyses.jsonl` 出现 + 字段填充正常
- [ ] `sync_signals.sh` 包含 analyses.jsonl
- [ ] 看板新增 3 列展示推荐/风险/结构匹配

---

## 下一步：Q2 — DeepSeek 强制 JSON Schema

把 `structure: null` 修掉。需要先看你的 prompt 文件位置。继续执行：

```bash
grep -rn "deepseek\|DeepSeek\|response_format\|structure" ~/cresus-bot/src/ai_layer/ 2>/dev/null | head -20
```

把输出贴给我，我会基于你的实际 prompt 给出精确的修改方案。
