# P19c-v2：cooldown 差异化（盈亏分流）— 数据反思后的重写

> 关联 commit: 私有 repo `a04e94e`
> 替换：原 P19c 一刀切 cooldown（`42146bc`）

## 起因 — 一次反直觉的数据反思

**第二周复盘时（2026-05-04），我用全量 13 笔交易数据对照检查 P19c 规则的实际效果，发现一个反转**：

P19c 当初是基于 demo 早期 8 笔（其中 BIO 几笔早期亏损）做的判断——「BIO 是亏损源，给所有 symbol 加 6h cooldown + 24h 损失连击降级」。

**全量数据显示完全相反**：

```
按 symbol 拆 PnL（13 笔已平仓）:

symbol           笔数  胜负   净 PnL
─────────────────────────────────────
BIO-USDT-SWAP     9    5/4   +128.51   ⭐ 最大盈利来源
AXS-USDT-SWAP     1    0/1    -20.83
DOGE-USDT-SWAP    1    0/1    -19.29
XAU-USDT-SWAP     1    0/1     -0.07
XPL-USDT-SWAP     1    1/0     +0.04
```

**BIO 不是亏损黑洞，是 56% 胜率的最大盈利来源**。剔除 BIO 后体系 1W/3L 净 -40 USDT。

P19c 总共 BLOCK 了 BIO 的 17 个 high-confidence 信号——意味着我**主动拦掉了真正的盈利机会**。这是经典的「用近期亏损推断长期 alpha」错误。

---

## 新规则设计 — 盈亏分流

### 决策树

```
读 closes_all.jsonl 该 symbol 最近 7 天的所有 closes:
    ↓
如果没有任何历史 → 通过（正常开仓）
    ↓
计算 net_7d = sum(net) for last 7d
取 last_close = 最近一次平仓
elapsed_h = 距 last_close 已过去多少小时
    ↓
分流判断：
┌─────────────────────────────────────────────────────────┐
│ 层 1：7d-loser 严格保护                                  │
│ 条件：net_7d < 0 AND elapsed_h < 24                     │
│ 处置：BLOCKED                                            │
│ 适用：AXS / DOGE 这种纯亏损 symbol，避免追亏           │
└─────────────────────────────────────────────────────────┘
    ↓ (未触发层 1，进入层 2)
┌─────────────────────────────────────────────────────────┐
│ 层 2：根据上次平仓状态分流                               │
│  上次盈利 (last_net > 0) → cooldown = 1h               │
│      原理：让赢家继续触发，捕获趋势放大                 │
│  上次亏损 (last_net < 0) → cooldown = 6h               │
│      原理：保留原 P19c 的市场冷却时间                  │
└─────────────────────────────────────────────────────────┘
    ↓
未触发任何阻止 → 通过
```

### 当前数据下的实测结果

```
Symbol        7d 笔数  7d 净盈亏  距上次  处置（新规则）
────────────────────────────────────────────────────────
BIOUSDT          9    +128.51   ~40h    ✅ 通过 (post-win-1h 早过)
AXSUSDT          1     -20.83   139h    ✅ 通过 (24h-loser 已过)
DOGEUSDT         1     -19.29    92h    ✅ 通过 (24h-loser 已过)
XAUUSDT          1      -0.07    92h    ✅ 通过
XPLUSDT          1      +0.04    93h    ✅ 通过
```

**当前数据下，新规则不阻止任何 symbol**——因为所有亏损都已过 24h，所有上次平仓也都过了 1h/6h。

### 未来场景模拟

| 场景 | 旧 P19c | 新 P19c-v2 |
|---|---|---|
| BIO 刚赢 +30，30 分钟后又触发 LONG@70 | BLOCKED 6h cooldown | ✅ 通过（post-win 仅 1h 限制，且早过） |
| BIO 刚亏 -22，3 小时后又触发 LONG@70 | BLOCKED 6h cooldown | BLOCKED 6h（不变，保留保护）|
| AXS 已亏 -20，5 小时后又触发 SHORT@70 | BLOCKED loss-streak | BLOCKED 24h-loser（更严） |
| AXS 已亏 -20，30 小时后又触发 | ✅ 通过 | ✅ 通过 |
| 全新未交易过的 symbol | ✅ 通过 | ✅ 通过 |

**关键差异**：
- BIO 这种 7d 净盈利的 symbol 在赢一笔后**1h 即可重开**（旧规则要 6h）→ 不再错失趋势
- AXS 这种 7d 净亏 symbol 在 24h 内**严格不能开**（旧规则只 6h）→ 防追亏更严

---

## 阈值（hardcoded 在 src/execution/cooldown.py）

```python
LOOKBACK_DAYS              = 7    # 7d 累计净盈亏窗口
LOSER_COOLDOWN_HOURS       = 24   # 7d 净亏 → 严保护
POST_WIN_COOLDOWN_HOURS    = 1    # 上次盈利 → 短冷却
POST_LOSS_COOLDOWN_HOURS   = 6    # 上次亏损 → 长冷却（保留 P19c 原值）
```

未来可移到 `common/config.py` 暴露给 `.env`。

---

## 实现 — 单函数 `check_block_reason`

```python
def check_block_reason(symbol: str) -> Optional[str]:
    """检查 symbol 是否应该被阻止开仓。
    返回 None（OK）或阻止原因（中文，会写入 signals.jsonl 的 block_reason 字段）。
    """
    closes_7d = _load_recent_closes(symbol, hours=LOOKBACK_DAYS * 24)
    if not closes_7d:
        return None  # 没历史，正常开仓

    last_close = closes_7d[-1]
    last_ts = datetime.fromisoformat(last_close["ts"].replace("Z", "+00:00"))
    elapsed_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600

    net_7d = sum((c.get("net") or 0) for c in closes_7d)

    # 层 1：7d-loser 严格保护
    if net_7d < 0 and elapsed_hours < LOSER_COOLDOWN_HOURS:
        return f"7d-loser: 7天净亏 {net_7d:+.2f} USDT, 距今 {elapsed_hours:.1f}h"

    # 层 2：上次平仓状态分流
    last_net = last_close.get("net") or 0
    if last_net > 0:
        cd = POST_WIN_COOLDOWN_HOURS
        label = f"win {last_net:+.2f}"
    else:
        cd = POST_LOSS_COOLDOWN_HOURS
        label = f"loss {last_net:+.2f}"

    if elapsed_hours < cd:
        return f"post-{label.split()[0]}-cooldown: 上次{label}, 距今 {elapsed_hours:.1f}h < {cd}h"

    return None  # 通过
```

`signal_router.py` 调用方式简化为单次：

```python
block_reason = check_block_reason(snap.symbol)
if block_reason:
    tag = "watch-list"
    channel = settings.discord_channel_watch_list
    logger.warning(f"[route] {snap.symbol} BLOCKED: {block_reason}")
```

---

## 向后兼容

保留旧 API（`is_in_cooldown` / `loss_streak_count`）让旧代码不至于 import 失败，但 `signal_router.py` 已切换到新 `check_block_reason`。

---

## 观察期 + 评估指标

未来 2 周 demo 期，每天观察 `bot.err` 中的 `BLOCKED` 日志，回答：

| 问题 | 评估方法 |
|---|---|
| BIO 类盈利 symbol 的 1h cooldown 是否真的让它"继续触发更多机会"？ | 数 `[route] BIOUSDT ... → high-confidence` 的频次 |
| AXS/DOGE 类 24h-loser 是否触发过严？ | 数 `BLOCKED: 7d-loser` 出现次数 + 验证那时是否真的不该开 |
| 新规则下整体 P&L 是否更好？ | 对照 `closes_all.jsonl` 5-04 之后的盈亏 vs 之前 |

如果 14 天后**新规则下 BIO 类胜率维持 + 总 P&L 不降反升**——说明差异化规则成立，可以推广。

如果**累计胜率反而下降**——说明 1h post-win 太短，赢家被惯性反弹击中，需要回调到 2h 或 3h。

---

## 关联工作

- 上游：[`P17`](23-hermes-live-quote.md) Hermes 实时行情工具
- 上游：[`P19a`](#) closes_all.jsonl 持久化层（让本规则可读历史 closes）
- 同期：[`P19f`](27-i-structure-squeeze-detection.md) I 型 Squeeze 检测（也是 5-04 复盘的产物）
- 下游：未来 [`P20`](#) 用本规则的 BLOCKED 日志反哺 LONG/SHORT 频次平衡

---

## 一行价值总结

> **P19c 用近期数据做 sample-based 决策，把 alpha symbol 误伤了。
> P19c-v2 用 7d 累计 + 状态分流，把"保护"和"放权"分开 — 给赢家更多机会，给输家更严约束。**
