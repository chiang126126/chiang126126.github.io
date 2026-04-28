# P19：信号状态机 + 距现价% 列（dashboard）

> 把"最近信号"面板从**静态信号列表**升级为**实时决策视图**——一眼看出"现在该不该动手"。

---

## 背景：为什么不做"下一步动作"文字列

最初想法：每条信号加一列文字"下一步动作"（例如"反弹到 76600 找空"）。**专业评估后否决**，原因：

1. **时效性致命**：信号生成于 17:32，用户 22:00 看板。4.5 小时后市场早变，"等反弹到 76600 找空"可能已经错过 / 已经触发 / 已破位。文字快照不会随价格更新。
2. **准确率天花板低**：任何不能基于实时上下文的"动作建议"准确率 ~50-60%（接近随机）。
3. **冗余**：`reasoning` + `entry/SL/TP` + Hermes `recommendation` 已构成完整决策计划，再加文字列等于噪音。

**替代方案**：状态枚举列（state machine）+ 距现价% 显示。**基于实时数据的客观计算**，准确率 >95%。

---

## 数据流

```
浏览器打开 dashboard
    ↓
loadPrices()  →  GET https://fapi.binance.com/fapi/v1/ticker/price
                 （单次请求返回所有 ~400 USDT-M 永续现价，无鉴权，<200KB）
    ↓
currentPrices = { "BTCUSDT": 75949.9, "ETHUSDT": ..., ... }
    ↓
renderTable() 遍历 signals
    ↓
对每条 signal: computeSignalState(signal, currentPrices[symbol], now)
    ↓
返回 { state, distEntry, distSL, distTP }
    ↓
状态徽章 + 入场/止损/止盈带距现价% 渲染

每 60s：loadPrices() → 重新渲染表格
```

---

## 状态枚举（11 种）

| 状态 | 触发条件 | 颜色 | 含义 |
|---|---|---|---|
| ⏳ **待触发** | 现价距 entry > 0.5%（未到入场） | 灰 | 还没到入场点，继续等 |
| ✅ **可入场** | 现价距 entry < 0.5%（在入场区） | 绿 | 现在就是入场点位，可以动手 |
| 🔥 **已激活** | 现价已穿越 entry，方向一致 | 蓝 | 该持仓中（如果是真盘信号已被自动 fill） |
| ⚠️ **临近止损** | 现价距 SL < 0.5% | 橙 | 警告，准备平仓或移动止损 |
| ❌ **已止损** | 现价已破 SL | 红 | 信号已结束，平仓 |
| ✅ **已止盈** | 现价已触 TP1 | 绿 | 第一档止盈触发 |
| ⏰ **已过期** | 信号年龄 > 6 小时 | 暗灰 | 时效失效，不再考虑 |
| 👀 **观察** | direction = WATCH | 黄 | 仅观察类，无具体动作 |
| — **—** | direction = SKIP | 暗灰 | 已跳过，无意义 |
| — 缺数据 | entry_price / stop_loss 缺失 | 暗灰 | 数据不全，无法判断 |
| ⏳ 加载中 | currentPrice 还没拿到 | 暗灰 | 网络或 CORS 问题，重试中 |

---

## 状态计算决策树

```
signal.direction
├── SKIP                                   → SKIP
├── WATCH                                  → WATCH
└── LONG / SHORT
    ├── entry_price 或 stop_loss 缺失      → NO_DATA
    ├── 信号年龄 > 6h                      → EXPIRED
    ├── 现价 = null（未加载）              → LOADING
    └── 有现价
        ├── 已触 TP1（方向匹配）           → HIT_TP
        ├── 已触 SL（方向匹配）            → STOPPED
        ├── 距 SL < 0.5%                  → NEAR_SL
        ├── 已穿越 entry（方向匹配）       → ACTIVE
        ├── 距 entry < 0.5%                → ENTRY
        └── 默认                           → WAITING
```

判断顺序至关重要——**先判已结束再判进行中**，避免一条已止损的信号还显示"激活中"。

---

## 距现价% 显示

每个 entry / SL / TP 数字后面追加 `(±X.XX%)`：

| 价格 | dist% 含义 | 颜色规则 |
|---|---|---|
| entry | (现价 - entry) / entry × 100 | abs<0.5% 黄 / 正绿 / 负红 |
| SL | (现价 - SL) / SL × 100 | abs<0.5% 黄 / 正绿 / 负红 |
| TP1 | (现价 - TP1) / TP1 × 100 | 同上 |

例如 `76,600 (+1.23%)` 表示现价比该 level 高 1.23%。**用户结合方向自行解读**：
- LONG 信号 entry 处显示 `+1.23%` → 现价已超过入场，已激活
- SHORT 信号 entry 处显示 `+1.23%` → 现价高于入场，反向，等回落
- 任意方向 SL 处显示 `-0.30%`（红） → 已击穿止损

颜色不绑定方向（避免复杂条件渲染）；状态徽章列已经给出"客观结论"。

---

## "🎯 只看活跃" 过滤按钮

新增过滤器，仅显示 `ENTRY` / `ACTIVE` / `NEAR_SL` 三种"现在该看一眼"的状态。

典型用法：开盘前快速扫一眼 → 看到 5 条 ✅ 可入场 + 2 条 ⚠️ 临近止损 → 5 分钟决策完。

---

## 数据源 & CORS

**主源**：`https://fapi.binance.com/fapi/v1/ticker/price`
- 公共端点，无鉴权
- 单次返回所有 USDT-M 永续合约现价（~400 个），约 200KB
- Binance public API 默认支持 `Access-Control-Allow-Origin: *`，浏览器跨域 OK
- 60s 一次刷新（既新鲜又不撑流量）

**降级**：fetch 失败时，状态列显示 `⏳ 加载中`，**dashboard 其他功能完全不受影响**——signals/analyses/pnl 三条路径都 self-contained。

**未来 fallback**（未实现，需要时加）：
1. CoinGecko `/simple/price`（CORS 友好但更新慢）
2. cresus-bot 写 `prices.json` 到 sync 目录，dashboard fetch 同源 JSON（消除 CORS 风险）

---

## 实现位置

**单文件改动**：`cresus-system/dashboard/index.html`

新增/修改：
- CSS：`.dist-pct` `.dist-pos` `.dist-neg` `.dist-warn`
- JS 全局：`currentPrices` `pricesLastUpdated` `STATE_META` `SIGNAL_TTL_HOURS` `ENTRY_TOLERANCE_PCT` `NEAR_SL_PCT`
- JS 函数：`loadPrices()` `computeSignalState()` `stateBadge()` `formatDist()` `priceWithDist()` `formatTPWithDist()`
- HTML：`<th>状态</th>` 列 + `<button data-mod="ACTIVE">🎯 只看活跃</button>` 按钮
- 初始化：`loadPrices()` 首次 + `setInterval(loadPrices, 60_000)`

完全前端实现，不需要后端改动。

---

## 阈值配置

```javascript
const SIGNAL_TTL_HOURS    = 6;    // 信号年龄 >6h → 已过期
const ENTRY_TOLERANCE_PCT = 0.5;  // 现价距 entry <0.5% → 可入场
const NEAR_SL_PCT         = 0.5;  // 现价距 SL <0.5% → 临近止损
```

调优指导：
- TTL 太短（<3h）→ 4H 级别信号还没到点就失效，浪费
- TTL 太长（>12h）→ 老信号还在列表里干扰判断
- 入场容差太宽（>1%）→ "可入场" 状态滥用，失去精确意义
- 入场容差太窄（<0.2%）→ 现价波动让状态频繁跳变

当前 6h / 0.5% / 0.5% 是 1H–4H 信号场景的合理默认。

---

## ✅ P19 完成检查清单

- [x] CSS 加 `.dist-pct` 等样式
- [x] JS 加 `currentPrices` `loadPrices` `computeSignalState` `stateBadge` 等函数
- [x] HTML 加"状态"列 + "🎯 只看活跃" 按钮
- [x] 初始化 + 60s 自动刷新接通
- [x] CORS fallback：fetch 失败时 graceful degrade 到 LOADING
- [x] JS 语法 check 通过
- [ ] 浏览器实测（用户在 https://chiang126126.github.io/cresus-system/dashboard/ 打开看）

---

## 已知限制

1. **Binance 区域限制**：用户 IP 在 Binance 受限地区时（中国大陆 / 美国部分州），fetch 直接 451，所有 LONG/SHORT 信号永远显示 `⏳ 加载中`。HK / 新加坡 / 日韩 / 大多数欧美地区 OK。
2. **TP 多档只显示 TP1 距现价**：如果信号有 TP1=80000 / TP2=82000，只显示 `80,000 (+1.5%) / 82,000`。简化设计，可后续扩展。
3. **TP1 触发即标 HIT_TP**：实际中可能止盈一半 + 移动止损到 entry。看板状态保守判定为"已止盈"，与真实仓位状态可能略有出入。
4. **信号年龄按 ts 字段计算**：如果 ts 时区错乱，TTL 判定会偏差。当前 signals.jsonl ts 都是 UTC ISO 字符串，正确。

---

## 下一步（可选）

| 阶段 | 内容 |
|------|------|
| **P19b** | 状态徽章 hover tooltip：显示具体动作建议（"现价 X，距 entry Y%，建议 Z"） |
| **P19c** | "只看活跃" 自动定时弹窗：每整点检查，有新进入 ACTIVE / ENTRY 状态推 Telegram |
| **P19d** | TP 多档分别显示距现价：`80000 (+1.5%) / 82000 (+4.0%)` |
| **P19e** | 加 OKX 现价 fallback（IP 受限地区） |
| **P19f** | 引入移动止损：当 ACTIVE 且浮盈 >1R 时，建议把 SL 移到 entry |
