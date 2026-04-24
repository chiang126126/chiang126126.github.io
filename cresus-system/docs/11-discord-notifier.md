# P4：Discord 通知层（中文 UI）

本文档覆盖 Crésus 信号系统第四阶段：将 AI 判断结果通过 Discord Bot 以**简体中文**推送到对应频道。

---

## 架构概览

```
main_loop.py
    ↓
scan_top_coins()          ← P2 数据层
    ↓ (34 维快照)
judge(snapshot)           ← P3 AI 层（DeepSeek）
    ↓ (decision dict)
route_decision()          ← P4 路由层
    ↓
discord_client.py         ← P4 通知层（中文 Embed）
    ↓
Discord Channels
    ├── #high-confidence  (≥70 LONG/SHORT)
    ├── #watch-list       (50–69 / WATCH)
    └── #scan-log         (每次扫描摘要)
```

---

## 频道配置

在 `.env` 中配置以下变量（Bot Token 需已加入服务器并具备 `Send Messages` + `Embed Links` 权限）：

```env
DISCORD_BOT_TOKEN=Bot xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DISCORD_CHANNEL_HIGH_CONFIDENCE=111111111111111111
DISCORD_CHANNEL_WATCH_LIST=222222222222222222
DISCORD_CHANNEL_SCAN_LOG=333333333333333333
```

> `DISCORD_BOT_TOKEN` 值必须以 `Bot ` 开头（含空格）。

---

## 文件结构

```
src/
├── notifier/
│   ├── __init__.py
│   └── discord_client.py    # P4 Sprint 1
└── execution/
    ├── __init__.py
    └── signal_router.py     # P4 Sprint 1

scripts/
├── main_loop.py             # P4 Sprint 1（完整闭环）
└── test_discord.py          # P4 Sprint 1（四频道连通测试）
```

---

## `src/notifier/discord_client.py`

### 中文化映射表

```python
_DIRECTION_CN = {
    "LONG":  "做多",
    "SHORT": "做空",
    "WATCH": "观察",
    "SKIP":  "跳过",
}

_STRUCTURE_CN = {
    "A": "A · OI 积累做多",
    "B": "B · 资金费反转做空",
    "C": "C · V4A-Flash 做空",
    "D": "D · 分布出货做空",
    "E": "E · 强控盘回避",
    "F": "F · 财库公司做多 BTC",
    "G": "G · 盘前套利（未启用）",
    "H": "H · 新闻事件",
    "none": "无明显结构",
}
```

### 信号 Embed：`build_signal_embed(decision, snap_reasons)`

字段布局：

| 字段名 | 内容 | 示例 |
|--------|------|------|
| 结构类型 | 中文结构名 | `A · OI 积累做多` |
| 信心度 | 0–100 整数 | `80 / 100` |
| 建议杠杆 | 倍数 | `5×` |
| 入场价 | USDT 精度 | `0.02341` |
| 止损 | USDT 精度 | `0.02200` |
| 止盈 | USDT 精度 | `0.02600` |
| 硬规则 | 触发规则列表 | `H5` / `—` |
| 异常信号 | 预警描述 | `大户净空仓 +4.2%` |

**标题颜色**：

| 方向 | 标题 | 颜色 |
|------|------|------|
| LONG | 🟢 做多 · SYMBOL | `0x00c853`（绿） |
| SHORT | 🔴 做空 · SYMBOL | `0xff1744`（红） |
| WATCH | 🟡 观察 · SYMBOL | `0xffd600`（黄） |

### 扫描摘要 Embed：`build_scan_summary_embed(...)`

```
📊 扫描摘要
  总扫描 / 通过 H1 / 进入 AI 分析
  做多 N | 做空 N | 观察 N | 跳过 N

Top 信号（置信度降序）：
  LABUSDT   做空  70   A · OI 积累做多
  ENJUSDT   做多  70   A · OI 积累做多
```

### `send_embed(channel_id, embed)` 

使用 Discord REST API v10，直接 `httpx.post`：

```python
httpx.post(
    f"https://discord.com/api/v10/channels/{channel_id}/messages",
    headers={"Authorization": settings.discord_bot_token,
             "Content-Type": "application/json"},
    json={"embeds": [embed]},
    timeout=10,
)
```

---

## `src/execution/signal_router.py`

路由逻辑：

```python
def route_decision(decision: dict, snap: MarketSnapshot) -> None:
    direction = decision["direction"]
    conf      = decision["confidence"]

    if direction in ("LONG", "SHORT") and conf >= 70:
        channel = settings.discord_channel_high_confidence
    elif direction == "WATCH" or conf >= 50:
        channel = settings.discord_channel_watch_list
    else:
        return   # SKIP — 静默丢弃

    embed = build_signal_embed(decision, snap.anomaly_reasons)
    send_embed(channel, embed)
```

阈值由 `.env` 控制：

```env
CONFIDENCE_OPEN_THRESHOLD=70    # LONG/SHORT → #high-confidence
CONFIDENCE_WATCH_THRESHOLD=50   # WATCH      → #watch-list
```

---

## `scripts/main_loop.py`

完整扫描闭环：

```python
def run_cycle(cycle: int) -> None:
    snaps     = scan_top_coins()                  # P2 扫描
    anomalies = [s for s in snaps if s.is_anomaly]

    decisions = []
    for snap in anomalies:
        data     = assemble(snap)                 # 34 维组装
        decision = judge(data)                    # P3 DeepSeek 判断
        decisions.append(decision)
        route_decision(decision, snap)            # P4 路由 + 发送

    # 每次扫描结束：摘要 → #scan-log
    embed = build_scan_summary_embed(
        total=len(snaps),
        passed=len(anomalies),
        anomalies=len(anomalies),
        decisions=decisions,
    )
    send_embed(settings.discord_channel_scan_log, embed)

def main() -> None:
    load_coin_list()           # CoinGecko 启动时加载一次
    # 启动通知 → #scan-log
    send_embed(settings.discord_channel_scan_log, {
        "title": "🤖 Crésus 系统启动",
        "description": f"扫描间隔：{settings.scan_interval_seconds}s | 扫描数量：{settings.scan_top_n_coins}",
        "color": 0x2196f3,
    })
    cycle = 0
    while True:
        run_cycle(cycle)
        cycle += 1
        time.sleep(settings.scan_interval_seconds)
```

---

## P4 Sprint 1 实测输出

运行命令：

```bash
SCAN_INTERVAL_SECONDS=99999 SCAN_TOP_N_COINS=20 uv run python scripts/main_loop.py
```

### #high-confidence 频道

| 信号 | 方向 | 信心度 | 结构 |
|------|------|--------|------|
| LABUSDT | 🔴 做空 | 70 | A · OI 积累做多 |
| ENJUSDT | 🟢 做多 | 70 | A · OI 积累做多 |

### #scan-log 频道

```
🤖 Crésus 系统启动
扫描间隔：99999s | 扫描数量：20

📊 扫描摘要
总扫描：20 | 通过 H1：4 | AI 分析：4
做多：1 | 做空：1 | 观察：0 | 跳过：2

Top 信号：
  LABUSDT  做空  70
  ENJUSDT  做多  70
```

---

## `scripts/test_discord.py`

四频道连通测试，运行一次即可验证 Bot Token + Channel ID 全部有效：

```bash
uv run python scripts/test_discord.py
```

预期输出：

```
✅ #high-confidence 发送成功
✅ #watch-list 发送成功
✅ #scan-log 发送成功
✅ #alert 发送成功（如有配置）
```

---

## ✅ P4 完成检查清单

- [x] `src/notifier/discord_client.py` — 中文 Embed 构建 + `send_embed`
- [x] `src/execution/signal_router.py` — 按信心度路由到正确频道
- [x] `scripts/main_loop.py` — 完整扫描闭环（启动通知 + 循环 + 摘要）
- [x] `scripts/test_discord.py` — 四频道连通测试全 ✅
- [x] Discord 信号卡片全中文（方向/结构类型/字段标签）
- [x] LONG/SHORT ≥70 → `#high-confidence`（已实测）
- [x] SKIP 静默（无 Discord 消息）
- [x] `git push` 成功

全部打勾 = P4 Discord 通知层完成，系统已可正常运行。

---

## 下一步（可选）

| 阶段 | 内容 |
|------|------|
| **P5（可选）** | PostgreSQL 存储层：持久化每次扫描结果、信号历史、回测数据 |
| **部署** | Mac Mini 后台 + `launchd` 守护进程 / VPS `systemd` 服务 |
| **监控** | `#alert` 频道：连续 3 次 API 失败、DeepSeek 余额耗尽等异常告警 |
