# P6：信号看板（GitHub Pages）

本文档覆盖 Crésus 信号系统第六阶段：将 `signals.jsonl` 历史数据通过 GitHub Pages 静态页面可视化。

---

## 在线访问

部署后地址：

```
https://chiang126126.github.io/cresus-system/dashboard/
```

---

## 页面功能

| 模块 | 内容 |
|------|------|
| 顶部统计卡 | 总信号数 / 做多 / 做空 / 观察 / 高信心(≥70) / 平均信心度 |
| 每日柱状图 | 最近 30 天每日信号数量 |
| 信号表格 | 时间 / Symbol / 方向 / 信心度 / 杠杆 / 入场 / 止损 / 止盈 / 结构 / 路由 |
| 过滤器 | 全部 / 做多 / 做空 / 观察 / ≥70 + Symbol 搜索 |
| 自动刷新 | 每 60 秒自动重新加载 `signals.jsonl` |

---

## 文件结构

```
cresus-system/dashboard/
├── index.html          # 静态看板页面（纯 HTML + JS，零依赖）
└── signals.jsonl       # 信号数据（从 cresus-bot 私库同步）
```

---

## 数据同步：Mac → 公共仓库

`signals.jsonl` 由 `signal_router.py` 在 cresus-bot **私有仓库**追加。

为了让看板页面读取，需要把它**同步到公共仓库** `chiang126126.github.io` 的 `cresus-system/dashboard/` 目录。

### 一次性同步（手动）

```bash
# 1. 复制最新 signals.jsonl
cp ~/cresus-bot/signals.jsonl \
   ~/path-to-public-repo/cresus-system/dashboard/signals.jsonl

# 2. 提交并推送
cd ~/path-to-public-repo
git add cresus-system/dashboard/signals.jsonl
git commit -m "data: sync signals.jsonl ($(date +%Y-%m-%d))"
git push
```

### 自动同步（推荐：launchd 每小时）

创建 `~/cresus-bot/scripts/sync_signals.sh`：

```bash
#!/bin/bash
set -e

PUBLIC_REPO=~/chiang126126.github.io
SIGNALS_SRC=~/cresus-bot/signals.jsonl
SIGNALS_DST=$PUBLIC_REPO/cresus-system/dashboard/signals.jsonl

# 复制最新 jsonl
cp "$SIGNALS_SRC" "$SIGNALS_DST"

# 仅当有变化时提交
cd "$PUBLIC_REPO"
if ! git diff --quiet cresus-system/dashboard/signals.jsonl; then
  git add cresus-system/dashboard/signals.jsonl
  git commit -m "data: sync signals.jsonl ($(date '+%Y-%m-%d %H:%M'))"
  git push
  echo "[sync] $(wc -l < $SIGNALS_DST) signals pushed"
else
  echo "[sync] no new signals"
fi
```

赋权：

```bash
chmod +x ~/cresus-bot/scripts/sync_signals.sh
```

### launchd 每小时调用

`~/Library/LaunchAgents/com.cresus.sync-signals.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cresus.sync-signals</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/mangzi/cresus-bot/scripts/sync_signals.sh</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>StandardOutPath</key>
  <string>/Users/mangzi/cresus-bot/logs/sync.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/mangzi/cresus-bot/logs/sync.err</string>
</dict>
</plist>
```

加载：

```bash
launchctl load ~/Library/LaunchAgents/com.cresus.sync-signals.plist
launchctl list | grep sync-signals     # 确认 PID 存在
```

---

## `signals.jsonl` 字段定义

每行一条 JSON 对象，由 `signal_router.py` 中 `_append_signal` 写入：

```json
{
  "ts": "2026-04-25T17:16:38.596182+00:00",
  "symbol": "PIPPINUSDT",
  "direction": "LONG",
  "confidence": 70,
  "structure": null,
  "entry_price": 0.03017,
  "stop_loss": 0.02715,
  "take_profit": [0.03319, 0.0362],
  "leverage": 3,
  "reasoning": "结构匹配A型：OI 24h增长20.6%且价格横盘，积累信号明确。",
  "hard_rules": [],
  "anomaly": ["OI 24h +20.6%"],
  "routed_to": "high-confidence"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | ISO8601 string | UTC 时间戳 |
| `symbol` | string | Binance 现货 symbol（如 `BTCUSDT`） |
| `direction` | `LONG` / `SHORT` / `WATCH` / `SKIP` | DeepSeek 判断方向 |
| `confidence` | int 0-100 | 信心度 |
| `structure` | string \| null | 匹配的 Master Framework 结构（A-H），目前 DeepSeek 未结构化输出，暂为 null |
| `entry_price` | float | 建议入场价（USDT） |
| `stop_loss` | float | 止损价 |
| `take_profit` | float \| float[] | 止盈价（可能多个目标） |
| `leverage` | int | 建议杠杆倍数 |
| `reasoning` | string | DeepSeek 文字推理 |
| `hard_rules` | string[] | 触发的硬规则（如 `["H5"]`） |
| `anomaly` | string[] | 异常指标列表 |
| `routed_to` | `high-confidence` / `watch-list` | 实际路由到的 Discord 频道 |

---

## ✅ P6 完成检查清单

- [x] `cresus-system/dashboard/index.html` — 静态看板页面
- [x] `cresus-system/dashboard/signals.jsonl` — 占位数据
- [ ] `~/cresus-bot/scripts/sync_signals.sh` — 同步脚本
- [ ] `~/Library/LaunchAgents/com.cresus.sync-signals.plist` — launchd 每小时
- [ ] 首次手动 `cp` 全量同步
- [ ] 浏览器访问 `https://chiang126126.github.io/cresus-system/dashboard/` 显示正常
- [ ] 1 小时后看板 `最后更新` 时间自动刷新

---

## 下一步（可选）

| 阶段 | 内容 |
|------|------|
| **P6b** | 看板增加 P&L 面板：`okx account positions` 拉取演示账户持仓，关联 signals 计算盈亏 |
| **P6c** | 信号详情页：点击行 → 展开 reasoning 全文 + Hermes 二次分析 markdown |
| **P7** | 胜率统计：信号触发后 24h/7d 价格 vs entry，统计 hit-SL / hit-TP / 时间止损 |
| **P8** | DeepSeek prompt 优化：把 `structure` 改为强制结构化输出（response_format JSON schema） |
