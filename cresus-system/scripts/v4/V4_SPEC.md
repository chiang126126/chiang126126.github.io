# Cresus V4 Paper Engine — 设计 Spec

**版本**: V4 Step A (paper engine 重写 + 6 月回测)
**创建日期**: 2026-05-24
**目标**: 在 6 个月历史数据上验证 day-scale hybrid regime-adaptive 策略是否有 edge.

通过门槛: **PF > 1.3, 胜率 > 35%, max drawdown < 30%**.

---

## 1. 跟 V3 的核心差异

| 维度 | V3 (现行, 已归档) | V4 (设计中) |
|---|---|---|
| K 线周期 | 1m (扫 30s/1m/5m/4h) | **15min + 1h + 4h + 1d** |
| 信号决策周期 | 1m 量价突变 | **1h + 4h + 1d** (15min 仅用于 SL/TP 精度) |
| 持仓时间 | avg 36.9min (median 17.3min) | **目标 1-7 天 (max 14 天)** |
| SL 距离 | ATR(1m) × 1.0 ≈ 1% | **ATR(4h) × 2.0 ≈ 5%** |
| TP1/TP2/TP3 | 1.5x / 3.0x ATR | **2.0x / 4.0x / 6.0x ATR(4h)** |
| Regime 频率 | 30min BTC 检测 | **1h BTC 检测 + 日级 trend filter** |
| 信号语义 | 短期 momentum 反转 | **日级趋势 + 突破 / 回踩 / 反转** |
| Conviction | 0-10 (短期 features) | **0-10 (日级 features, 重新校准 — 见 §2.4)** |
| Funding/OI/Taker 时间窗 | 瞬时 / 5m / 1m-5m 加权 | **当前+7d 平均 / 24h+7d delta / 24h 加权** |
| 杠杆 | 3x (live) | **1x (live, 省 funding + 无 decay)** |
| 单笔保证金 | $20 (live) | **$400 起步 (live, $2000 / 5 并发)** |
| 并发 | 4 (live) | **5 (live, 跟 paper $2000 / $400 一致)** |

### 1.1 为什么加 15min K 线
回测中检 SL/TP 触发, 1h K 内若 [low, high] 同时穿过 SL 和 TP1, 无法判断顺序 → PnL 估算可能错.
**用 15min K 线作为 SL/TP 精度层** (信号决策仍用 1h+4h+1d). 4 倍精度, 数据量 +60MB 可接受.

---

## 2. Hybrid Regime-Adaptive 信号假设

### 2.1 Regime 检测 (BTC 1h K 线, EMA 滤波)
- **Up regime**: BTC 1h close > EMA(50) × 1.005 + EMA(50) slope > 0
- **Down regime**: BTC 1h close < EMA(50) × 0.995 + EMA(50) slope < 0
- **Chop regime**: 其它 (或 1h ATR % < 阈值)

切换 hysteresis: 需连续 3 根 1h K 线满足才确认 regime change (避免 flip-flop).

### 2.2 信号生成 (按 regime 分支)

#### Up regime → Long Breakout
- 价格突破 20 日 Donchian 上轨 (`high.rolling(20).max()`)
- 量能确认: 1d volume > 1.5 × 20d MA volume
- BTC 同向 (BTC 1d 涨)
- 入场: 突破日收盘价

#### Down regime → Short Breakout
- 价格跌破 20 日 Donchian 下轨
- 量能确认: 1d volume > 1.5 × 20d MA volume
- BTC 同向 (BTC 1d 跌)
- 入场: 跌破日收盘价

#### Chop regime → Mean Reversion
- RSI(14, 1d) < 30 且 4h K 线出现下影线 (lower wick > body × 2)
- 量能: 1h volume > 1.5 × 24h MA
- 入场: 下影线确认 1h K 收盘 → LONG
- 镜像逻辑对应 SHORT (RSI > 70 + 上影线)

### 2.3 SL/TP 计算 (ATR(14) on 4h K 线)
- **SL**: entry ∓ 2.0 × ATR(4h) → ~5% 距离
- **TP1**: entry ± 2.0 × ATR(4h) → 锁 1/3 仓, 移 SL 到 entry (BE)
- **TP2**: entry ± 4.0 × ATR(4h) → 锁 1/3 仓, 启 trail (HWM ∓ 2.0×ATR)
- **TP3**: entry ± 6.0 × ATR(4h) → 锁 1/3 仓 或 全平
- **Timeout**: 14 天自动关 (vs V3 4h)

### 2.4 Conviction Score (0-10, 重新校准)

**设计原则**: 硬条件 (信号触发) vs 软加分 (conviction) 严格分离.
- 硬条件 → 决定**开不开仓** (在 §2.2 信号生成)
- 软加分 → 决定**信号几分** → 是否过 ≥6 门槛

**Base: 3 分** (满足 §2.2 entry 硬条件)

**软加分** (最多 +7, cap at 10):

技术指标类 (+3):
- +1: 1d volume > 2x MA (vs 信号触发的 1.5x base)
- +1: 4h MACD histogram 同向 (LONG signal + hist > 0)
- +1: 周线趋势同向 (5d MA > 20d MA for LONG)

宏观环境类 (+2):
- +1: BTC 同向 1d 涨/跌 > 1.5%
- +1: 当前 regime 已确立 > 7 天 (regime stability)

资金流 / 主力意图 (+3, **V3 验证过有 alpha**):
- +1: Funding rate 极端同向 (`|funding_8h| ≥ 0.05%`, LONG signal + funding 负, SHORT signal + funding 正)
- +1: 24h OI delta 方向同向 (LONG + OI 增 > 2%, SHORT + OI 减 > 2%)
- +1: 24h taker buy ratio 方向同向 (LONG + ratio > 55%, SHORT + ratio < 45%)

历史胜率 (+1):
- +1: 该 symbol 在最近 30 天 V4 历史信号胜率 > 50%
  - **第一版回测**: 没 V4 历史数据, 用 V3 paper 30d 胜率作 prior (有偏差但 conservatively 可用)
  - **第二版回测**: 用 V4 自己的 rolling 30d 胜率

**Diamond 阈值**: ≥ **6** 分 (vs V3 ≥ 5).

### 2.5 V4 不用的 V3 指标 (审计后明确剔除)
- ❌ 1m volume burst (1m vs 30m 5x) — day-scale 不看分钟噪声
- ❌ 5m / 15m 涨跌 — 短窗对 day-scale 决策无信息
- ❌ ATR(1m) — 改用 ATR(4h)
- ❌ V3 历史 30m 胜率作为 conviction 因子 — 改成 V4 历史胜率 (持仓窗一致)

---

## 3. 回测引擎设计 (v4_backtest.py)

### 3.1 输入
- 6 个月 1h + 4h + 1d K 线 (V3 paper 接触过的 237 symbol)
- Range: 2025-11-24 → 2026-05-24

### 3.2 主循环 (per-symbol, time-stepped)
```
for symbol in symbols:
    klines_1d, klines_4h, klines_1h = load_local_cache(symbol)
    state = init_state()
    for t in time_range:
        # 1. 检 open positions, update phase / 收 SL/TP
        for pos in state.open:
            update_position(pos, klines_1h[t])
        # 2. 检新信号
        if can_open_new(state):
            signal = check_signal(symbol, klines_1d[t], klines_4h[t], klines_1h[t], btc_regime)
            if signal and signal.conviction >= 6:
                state.open.append(open_position(signal))
```

### 3.3 限制 (跟 live 一致)
- Max concurrent: 5
- Notional: $400/笔
- Fees: 0.04% taker + 0.04% maker = 0.08% round-trip
- Funding: 1x 杠杆 → 0.01% / 8h × hold_days × 3 (估算)

### 3.4 输出 metrics
- **核心**: PF, 胜率, max drawdown, Sharpe (日级)
- **明细**: 每笔 entry/exit/PnL/regime/conviction/symbol → CSV
- **分组**: by regime (up/chop/down), by conviction tier (6/7/8/9/10), by symbol

---

## 4. 历史数据下载 (v4_data_fetcher.py)

### 4.1 来源 (Binance perp API)
- K 线: `/fapi/v1/klines` (V3 已用)
- Funding: `/fapi/v1/fundingRate` (8h 一次, 6 月 = ~540 行/symbol)
- OI 历史: `/futures/data/openInterestHist` (5m 粒度, 6 月 = ~52000 行/symbol, 抽样到 4h)
- Taker buy ratio: `/futures/data/takerlongshortRatio` (4h 粒度, 6 月 = ~1080 行/symbol)

### 4.2 存储
- K 线: `~/cresus-bot/v4_klines/{symbol}_{timeframe}.parquet`
  - 4 个 timeframe × 237 symbol = 948 文件
- Funding: `~/cresus-bot/v4_funding/{symbol}.parquet`
- OI: `~/cresus-bot/v4_oi/{symbol}_4h.parquet`
- Taker: `~/cresus-bot/v4_taker/{symbol}_4h.parquet`
- 总量估算: K 线 ~80MB + funding/OI/taker ~20MB = **~100MB**

### 4.3 容错
- Rate limit: 1200 weight/min, K-line = 1 weight, OI/funding = 1 weight
- Throttle: 0.1s 间隔
- 失败重试 3 次指数退避
- Symbol 下架 → log 跳过, 不阻塞
- 数据缺口 (新上币 symbol 历史不足 6 月) → log + continue, 回测时用现有数据

---

## 5. 文件结构

```
cresus-system/scripts/v4/
├── V4_SPEC.md                   ← 本文档
├── __init__.py
├── v4_data_fetcher.py           ← K 线 (15m/1h/4h/1d) + funding + OI + taker 下载
├── v4_indicators.py             ← ATR / RSI / EMA / Donchian / MACD
├── v4_regime.py                 ← Day-scale BTC regime 检测 (1h EMA + hysteresis)
├── v4_signals.py                ← Hybrid 信号生成 (3 个 sub-strategy, 硬条件)
├── v4_conviction.py             ← Conviction 评分 (软加分: tech + macro + flow + history)
├── v4_paper_engine.py           ← Position lifecycle (phase A/B/C, 15min 精度)
├── v4_backtest.py               ← 回测主循环 (跨 symbol portfolio state)
└── tests/
    ├── test_indicators.py
    ├── test_regime.py
    ├── test_signals.py
    ├── test_conviction.py
    └── test_backtest.py
```

---

## 6. Step A 1 周计划

| Day | 内容 | 产出 |
|---|---|---|
| 1 (今日) | V4_SPEC + 文件骨架 + data_fetcher 跑通 BTC 6 月 1h | spec + 骨架代码 + 1 个 symbol 数据 |
| 2 | data_fetcher 完整 (237 symbol × 3 timeframe) + indicators 实现 + 单测 | ~50MB 本地缓存 + indicator 库 |
| 3 | regime + signals 实现 (3 个 sub-strategy 各自单测) | 信号生成可在历史数据上跑出 events |
| 4 | conviction + paper_engine + backtest 框架 | 单 symbol 端到端跑通 |
| 5 | 跑全量 237 symbol × 6 月回测, 收集 metrics | 第一版 PF / 胜率 / drawdown 数据 |
| 6 | 调参 (regime 阈值 / ATR 倍数 / conviction 门槛) | 第二版数据 |
| 7 | 写决策报告 + 跟 V3 baseline 对比 + Step B 决策 | 报告 + go/no-go 决定 |

通过门槛: PF > 1.3 + 胜率 > 35% + drawdown < 30%, 三者全过才进入 Step B (实时跑 paper engine).

---

## 7. 不做的事 (明确边界)

- ❌ 不接 live trading (Step C 才做, 起步 $200, 不是 $2000)
- ❌ 不改 V3 代码 (V3 继续跑, V4 完全独立)
- ❌ 不写新 dashboard (Step B 后再说)
- ❌ 不优化到 90 分, 60 分通过门槛即可 (避免无止境调参)
- ❌ 不引入 ML / 神经网络 (rules-based 优先, 数据少不适合 ML)

---

## 8. 风险 + 退路

**风险 1**: 6 个月数据可能 regime 单一, 回测 PF 高但实战崩.
**退路**: 看回测内部 BTC up/chop/down 各占多少, 如果 chop > 60%, 警告样本偏置.

**风险 2**: 237 symbol 含大量 low-cap, day-scale 突破容易 false positive.
**退路**: 输出按 symbol 分组的 metric, 看 top-50 vs 其余的差异.

**风险 3**: 自写回测引擎可能有 bug, PF 虚高.
**退路**: 用 BTC 单 symbol 做 sanity check — 跟 vectorbt 对比 1 个简单策略 (golden cross) 的结果.

**Step A 失败 → 不进 Step B**: 如果 PF < 1.3, 重审信号假设. 不为了"启动" 强行进 live.
