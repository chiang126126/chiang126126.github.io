# 精灵捕手 L2 — 代码自审

这份文档对每个公开函数列出：
- 输入约束
- 关键不变式
- 边界情况
- 对应的测试

目的：让"我检查过了"这件事是**可验证的**而不是嘴上说说。

---

## `compute_chip_features`

**职责**：计算筹码集中度 + funder 去重 → `ChipFeatures`。

| 输入约束 | 检查 | 行为 | 测试 |
|---|---|---|---|
| `circulating > 0` | 强校验 | 否则 `raise ValueError` | `test_chip_circulating_zero_raises` |
| holders 非空 | 软检查 | 全部被排除时返回 inf cluster | `test_chip_all_excluded_returns_inf_cluster` |
| holders 排序 | 强制 | 内部按 balance 降序 | `test_chip_top10_share_basic` |

**关键不变式**
- top10/top50 计算前 **必须** 排除 CEX/burn/合约 ⇒ `test_chip_excludes_cex_and_burn`
- `excluded_count` 等于排除数 ⇒ 同上
- `cluster_factor = len(filtered) / clusters`，clusters=0 时为 `inf` ⇒ `test_chip_all_excluded_returns_inf_cluster`

---

## `funder_dedupe` / `_find_funder`

**职责**：沿入金链回溯，把同源地址合并成同一 cluster。

| 边界 | 行为 | 测试 |
|---|---|---|
| 空列表 | 返回 0 | `test_funder_empty` |
| 无入金记录 | 自己即 funder | `test_funder_no_incoming_self_funded` |
| 共同 root | 合并为 1 cluster | `test_funder_same_root_merges` |
| 链路经过 CEX 热钱包 | 在 CEX 处停止 | `test_funder_stops_at_cex` |
| 起点本身是 CEX | 自己作为 funder | `test_funder_starts_at_cex_address` |
| 形成环路 (A↔B) | visited 集合保护，不死循环 | `test_funder_cycle_protection` |
| 链路 > max_hops | 在 max_hops 处停下 | `test_funder_max_hops_respected` |
| 不同链路 | 分别计 cluster | `test_funder_two_independent_chains` |

**已知简化**（生产版需补强）
- 每跳只取 "第一个非 visited 的入金源"，未按金额加权 → 可能误判 dust funder 为主源
- BFS 是单线程的深度优先；多源情况下应改为图遍历 + 金额排序

---

## `stratify_oi`

**职责**：把 OI 拆成主力/Follow 两部分，计算综合操纵分（0-100）。

| 边界 | 行为 | 测试 |
|---|---|---|
| `total_oi <= 0` | 早返回 + `total_oi_zero` warning | `test_stratify_total_oi_zero` |
| 单一交易所 | `single_exchange_only` warning | `test_stratify_single_exchange_warning` |
| 不在 Binance（占比 = 0） | `no_binance_oi` warning，**不**触发 W_BINANCE_LOW | `test_stratify_not_on_binance_no_low_warning` |
| 相关性不可用（NaN） | `corr_undefined` warning，**不**触发 W_OPERATOR_HIGH | `test_stratify_nan_corr_does_not_penalize` |
| OI 与价格反向 | operator_pct = 1，触发 W_OPERATOR_HIGH | `test_stratify_operator_when_oi_anti_correlated` |
| 4 项全中 | 操纵分 = 100 | `test_stratify_full_manipulation_score` |
| 4 项全无 | 操纵分 = 0 | `test_stratify_friendly_score_zero` |

**关键修复**（实现时发现的 bug）
- **bug**：相关性 NaN 时把 corr 设为 0 → follow_pct=0 → operator_pct=1 → 错误地扣 W_OPERATOR_HIGH (25 分)
- **修复**：增加 `corr_available` 标志，NaN 时跳过该项的判定
- **教训**："数据不够"绝不能等同于"信号成立"——是测试逼出来的，不是看代码看出来的

---

## `_pearson_of_diff`

**职责**：用一阶差分计算 Pearson 相关性，避免趋势造成的虚假相关。

| 输入 | 输出 | 测试 |
|---|---|---|
| 差分成比例（同向） | r = +1 | `test_pearson_perfectly_positive` |
| 差分成反比 | r = -1 | `test_pearson_perfectly_negative` |
| **线性趋势序列** | NaN（差分常量，方差 0） | `test_pearson_linear_trend_yields_nan` |
| 一边常量 | NaN | `test_pearson_constant_series_returns_nan` |
| 长度不一致 | NaN | `test_pearson_length_mismatch_nan` |
| 时间戳错位 | NaN | `test_pearson_ts_mismatch_nan` |
| 样本不足（< 3） | NaN | `test_pearson_insufficient_samples` |

**重要不变式**
- "两个线性递增序列" → NaN 是**预期行为**而非 bug。这正是为什么用 diff 而不是 levels：避免"BTC 和 ETH 都涨"被解读成"两者强相关"。

---

## `detect_distribution_divergence`

**职责**：检测"价涨 + OI 跌 + holders 持平"的三层背离。

| 条件组合 | 输出 | 测试 |
|---|---|---|
| 三条全中 | `DISTRIBUTION_CONFIRMED` (strength=1.0) | `test_divergence_confirmed` |
| 价 + OI 中，holders 不可用 | `DISTRIBUTION_LIKELY` (strength=0.6) | `test_divergence_likely_when_holders_unknown` |
| 价 + OI 中，holders 增长太多 | `DISTRIBUTION_LIKELY` | `test_divergence_likely_when_holders_growing` |
| 价跌 OR OI 涨 | `NO_DIVERGENCE` | `test_divergence_no_divergence_*` |
| 样本不足 | `insufficient_data` | `test_divergence_insufficient_data` |
| 时间戳错位 | `ts_mismatch` | `test_divergence_ts_mismatch` |
| `operator_oi_fraction = 0` | OI 斜率被抹零 → `NO_DIVERGENCE` | `test_divergence_operator_oi_fraction_dampens` |

**纯函数承诺**
- 不做任何 I/O；所有数据从参数传入
- holders_change_pct 可选，None 时最高只能给 LIKELY

---

## `_linear_slope`

**职责**：OLS 线性回归斜率，纯 Python 无依赖。

| 输入 | 输出 | 测试 |
|---|---|---|
| 常量序列 | 0 | `test_slope_constant_is_zero` |
| 递增 [1,2,3,4] | +1 | `test_slope_ascending_positive` |
| 递减 [4,3,2,1] | -1 | `test_slope_descending_negative` |
| 单点 / 空 | 0（安全降级） | `test_slope_single_value_zero`, `test_slope_empty_zero` |

**手算验证**：
对 [1,2,3,4]：n=4, Σx=6, Σy=10, Σxy=20, Σx²=14
slope = (4×20 − 6×10) / (4×14 − 36) = 20/20 = 1 ✓

---

## `route_to_pool`

**职责**：把标的分到 4 个池中的一个，规则互斥，按优先级返回。

| 优先级 | 规则 | 触发条件 | 测试 |
|---|---|---|---|
| 1 (最高) | BLACKLIST: 极端 top10 | top10 > 0.85 | `test_pool_blacklist_extreme_top10` |
| 1 | BLACKLIST: 极端 manipulation | level > 85 | `test_pool_blacklist_extreme_manipulation` |
| 1 | BLACKLIST: 加速期 | daily_pump > 5.0 (=+500%) | `test_pool_blacklist_daily_pump` |
| 1 | BLACKLIST: Binance 占比过低 | 0 < binance_share < 0.10 | `test_pool_blacklist_binance_share_too_low` |
| 1 | BLACKLIST: 刷量 | vol/oi > 20 | `test_pool_blacklist_vol_oi_wash` |
| 2 | FRIENDLY (AND) | top10 ≤ 0.20 ∧ cluster_factor ≤ 3 ∧ level < 40 | `test_pool_friendly_baseline` |
| 3 | OPERATOR (AND) | top10 ≥ 0.50 ∧ level ≥ 50 | `test_pool_operator_baseline` |
| 4 | NEUTRAL | 默认 | `test_pool_neutral_when_between` |

**关键不变式**
- BLACKLIST 优先于 FRIENDLY：哪怕筹码很分散，操纵分极高时也必须拉黑 ⇒ `test_pool_priority_blacklist_over_friendly`
- "不在 Binance" (occupation = 0) ≠ "占比过低" ⇒ `test_pool_not_on_binance_is_not_blacklist`
- 友好需要 *所有* 条件同时满足 ⇒ `test_pool_friendly_requires_low_manipulation`
- 操纵池需要筹码 *和* OI 同时满足 ⇒ `test_pool_operator_requires_both_chip_and_oi`

---

## `evaluate_long_safety` / `evaluate_short_safety`

**职责**：策略下单前的一票否决关。多头/空头规则不同，所以拆成两个入口。

### 多头 (Module A) 通过条件
| 项 | 阈值 (config) | 测试 |
|---|---|---|
| 合约一票否决（mintable/freezeable/pausable/blacklist） | 全为 False | `test_long_rejects_mintable` + parametrized |
| owner 危险 = renounced=False ∧ has_privileges=True | 拒绝 | `test_long_owner_dangerous_rejected` |
| owner 仅 renounced=False（无权限）| 通过 | `test_long_owner_not_renounced_but_no_privileges_passes` |
| buy_tax / sell_tax | ≤ 5% | `test_long_rejects_high_taxes` |
| LP 锁定 | ≥ 90% & 剩余 ≥ 180d | `test_long_rejects_lp_*` |
| 流动性 | ≥ $200k | `test_long_rejects_thin_liquidity` |
| 池子年龄 | ≥ 14d | `test_long_rejects_young_pool` |
| top10 | ≤ 30% | `test_long_rejects_concentrated_chip` |
| dev 历史 | 无 rug 记录 | `test_long_rejects_dev_rug_history` |
| 模拟试卖 | 买和卖都成功（若提供）| `test_long_rejects_honeypot_cannot_sell` |

### 空头 (Module B) 通过条件
| 项 | 阈值 | 测试 |
|---|---|---|
| has_futures_market | 必须 True | `test_short_rejects_no_futures` |
| 合约一票否决 | 同多头 | `test_short_audit_one_veto_still_applies` |
| buy/sell_tax | ≤ 10%（比多头宽）| `test_short_higher_tax_tolerance` |
| 流动性 | ≥ $500k（比多头深）| `test_short_higher_liquidity_floor` |
| 池子年龄 | ≥ 7d（要先有顶可空）| `test_short_pool_age_lower_threshold` |
| 蜜罐 | 仍要检查 | `test_short_honeypot_kills` |
| LP 锁 / dev / top10 | 不检查 | `test_short_does_not_check_lp_lock` + `test_short_does_not_check_chip_concentration` |

**关键不变式**
- 所有失败原因 *全部* 进 `rejected_reasons`，不做 short-circuit ⇒ `test_long_multiple_failures_all_reported`
- 多头审计签名不含某些参数等于"该规则对空头不适用"的设计承诺 ⇒ `test_short_does_not_check_chip_concentration` 用 inspect 反向验证

---

## `detect_support_collapse`

**职责**：支撑崩塌信号。pump ≥ 40% → peak 后 ≥ 4 根 → 跌破派发区低点 + 放量。

| 输入条件 | 输出 | 测试 |
|---|---|---|
| 数据不足 | `insufficient_data` | `test_sc_insufficient_data` |
| 横盘市场（无 pump） | `pump_too_small` | `test_sc_no_pump_returns_pump_too_small` |
| peak 在当前或刚发生 | `peak_too_recent` | `test_sc_peak_too_recent` |
| 派发区支撑没破 | `support_holds:close=X>=Y` | `test_sc_support_holds` |
| 破位但缩量 | `volume_too_low:vol_ratio` | `test_sc_volume_too_low` |
| pump < 40% | `pump_too_small:pct` | `test_sc_pump_too_small` |
| 全条件满足 | `SUPPORT_COLLAPSE` (strength > 0) | `test_sc_full_trigger` |
| base_low ≤ 0（坏数据）| `invalid_base_low` | `test_sc_invalid_base_low` |
| 历史量中位 = 0 | `zero_median_volume` | `test_sc_zero_median_volume` |

**已知简化**
- 派发区"支撑"定义为 peak 后所有 low 的最小值；更精细做法是用 swing low 识别
- 量能用中位数；可改 EMA 或 ATR 加权
- 不区分时间段（用户可改 `pump_lookback_bars` 调）

---

## `detect_short_vacuum`

**职责**：插针清算空头 + OI 骤降 → 庄失去拉抬动力。

| 输入条件 | 输出 | 测试 |
|---|---|---|
| OI 序列 < 2 点 | `oi_series_too_short` | `test_sv_oi_series_too_short` |
| 价格序列 < 2 点 | `price_series_too_short` | `test_sv_price_series_too_short` |
| 1m K 线 < window | `not_enough_candles` | `test_sv_not_enough_candles` |
| OI 下降 < 15% | `oi_drop_too_small:pct` | `test_sv_oi_drop_too_small` |
| 窗口内无上影 ≥ 3% | `no_significant_wick:ratio` | `test_sv_no_wick` |
| 前置 pump < 20% | `no_recent_pump:pct` | `test_sv_no_recent_pump` |
| 全条件满足 | `SHORT_VACUUM` | `test_sv_full_trigger` |
| oi_start ≤ 0 | `oi_start_non_positive` | `test_sv_oi_start_zero` |
| price_low ≤ 0 | `price_low_non_positive` | `test_sv_price_low_zero` |

**strength 单调性**：OI 下降幅度越大，strength 越大（其它条件相同时）⇒ `test_sv_strength_monotonic_in_oi_drop`

**已知简化**（v0）
- OI 下降未区分"主力 OI"还是"follow OI"。理论上应该是"follow OI 在跌、operator OI 还在"——这种才是真正的真空。需要先用 `stratify_oi` 拿到 OI 分层结果再喂入
- "插针"用 max(open, close) 算上影；更严的版本可以同时看 wick 占整根 K 线的比例

---

## `ema` / `consecutive_up_bars`

**职责**：被多个信号模块复用的纯数学指标工具。

`ema(values, period)`：
- 第一个值用前 period 个值的 SMA 做种子 ⇒ `test_ema_seed_is_sma`
- 后续按 α = 2/(period+1) 递推 ⇒ `test_ema_smoothing_factor`
- period ≤ 0 抛错 ⇒ `test_ema_invalid_period`
- 输入长度 < period 返回空 ⇒ `test_ema_too_few_values`
- 常量输入返回常量 ⇒ `test_ema_constant_series`
- 递增输入产出递增 ⇒ `test_ema_monotonic_for_monotonic_input`

`consecutive_up_bars(values)`：从末尾向前数连续严格递增根数。
- 完整参数化测试覆盖 8 种 case ⇒ `test_consecutive_up_bars`

---

## `detect_trend_follow`

**职责**：Module A 主力多头入场信号。4H 多头排列 + EMA 持续向上 + 1D 突破 + 持有人增速。

| 输入条件 | 输出 | 测试 |
|---|---|---|
| 4H 不够 50 + N 根 | `insufficient_4h_data` | `test_tf_insufficient_4h` |
| 1D 不够 N+1 根 | `insufficient_1d_data` | `test_tf_insufficient_1d` |
| price < EMA20 或 EMA20 < EMA50 | `not_bullish_stack:...` | `test_tf_not_bullish_stack` |
| EMA20 未连续向上 ≥ 3 根 | `ema20_not_rising:bars=N` | （隐式覆盖于 baseline）|
| 1D 未突破前 20 日高点 | `no_daily_breakout:...` | `test_tf_no_daily_breakout` |
| 持有人增速 < 30% | `holders_growth_too_low:pct` | `test_tf_holders_growth_too_low` |
| 持有人输入 = None | 不阻塞但 strength × 0.7 | `test_tf_holders_none_does_not_block` |
| 全条件满足 | `TREND_FOLLOW` | `test_tf_full_trigger` |

**关键不变式**：触发时 price > EMA20 > EMA50 ⇒ `test_tf_ema_relations_correct`

---

## `size_position` / `size_long_position` / `size_short_position`

**职责**：基于固定风险 + 三道上限计算单笔仓位。

**核心公式**
```
risk_usd       = equity × risk_per_trade_pct
risk_per_unit  = |entry - sl| / entry
risk_based_qty = risk_usd / risk_per_unit

single_cap     = equity × max_single_position_pct × max_leverage
portfolio_cap  = equity × max_portfolio_pct − open_position_value_usd
leverage_cap   = equity × max_leverage

final_qty      = min(risk_based, single_cap, portfolio_cap, leverage_cap)
capped_by      = winner 的名字（risk_based 时为 None）
```

| 场景 | 期望 | 测试 |
|---|---|---|
| 风险-based 是最小 | capped_by=None | `test_sizing_risk_based` |
| 单仓上限卡 | capped_by="single_position_cap" | `test_sizing_capped_by_single_position` |
| 总仓上限卡（已开仓占用预算）| capped_by="portfolio_cap" | `test_sizing_capped_by_portfolio` |
| 杠杆上限卡（spot, 风险密度极小）| capped_by="leverage_cap" 或同等 | `test_sizing_capped_by_leverage` |
| 已开仓恰好等于预算 | qty=0, capped_by="portfolio_cap" | `test_sizing_portfolio_already_full` |
| short 方向（sl 在 entry 上方）| 与 long 同密度时 qty 相同 | `test_sizing_works_for_short_direction` |
| Module B 2x 杠杆 | leverage ≤ 2 | `test_sizing_short_with_leverage_2x` |
| equity ≤ 0 / entry ≤ 0 / sl ≤ 0 / sl == entry / max_leverage < 1 | ValueError | `test_sizing_*_raises` |

**默认 wrapper**：
- `size_long_position`：用 A_* 系列默认值（1% risk, 2% single, 70% portfolio, 1x）
- `size_short_position`：用 B_* 系列（0.5% risk, 1% single, 20% portfolio, 2x）

---

## 整体审计反思

### 已修正的逻辑错误（实现过程中暴露的）
1. **NaN 当 0 处理**导致"数据缺失"被错误判定为"操纵存在" → `stratify_oi` 已加 `corr_available` 标志。
2. **测试构造时漏算 `recent` 切片窗口**，导致 `invalid_base_low` 防御实际未被测试覆盖 → 已修正测试数据。

### 已知简化（v0 故意留的）
1. `_find_funder` 单线 BFS，没按金额加权 → 多源资金的 holder 可能错判 funder。
2. `_linear_slope` 不返回 R²，没法判断"斜率显著性" → 真实生产应改为带 p-value 的回归。
3. `detect_distribution_divergence` 用整段窗口的 OLS 斜率 → 对突变不敏感。
4. `_pearson_of_diff` 用等步长假设 → 时间戳必须严格对齐，缺一个点都会失败而非插值。
5. `detect_short_vacuum` 看的是总 OI，没有区分"主力 OI 还在 / follow OI 在跌" → 理想是与 `stratify_oi` 串联。
6. `detect_support_collapse` 用 peak 后所有 low 的最小值作支撑参考 → 真实策略应该用 swing low / 上升通道下沿。
7. `ema` 用 SMA 做种子 → 与 TradingView/Binance 的 EMA 在最初几根上有小差异，长期收敛。

### 仍需在 L4+ 处理（不在本仓库公开实现）
- AI 评分层与确定性下单代码的隔离（OTOCO 参数禁止读取 LLM 输出）
- OTOCO 挂单实现 + 三级熔断 + Telegram /halt
- 跨所 OI 归一化（USDT-M vs Coin-M 计价不一致）
- 历史快照存档（防 survivorship bias）
- 时钟同步监控（chrony + offset > 500ms 报警）

### 安全 / 风格
- 全部公开 API 都是 `frozen=True` dataclass → 下游不能意外篡改。
- 全部 I/O 走 Protocol 接口 → 测试无需 mock 任何真实数据源。
- 阈值集中在 `config.py` → 调参只需改一个文件。
- 零外部运行时依赖（仅测试需要 pytest）→ 可在任何 Python 3.10+ 环境运行。

### 仍需在 L3+ 中处理（不在本模块职责内）
- 数据源限频 / 重试 / 缓存
- 历史快照存档（防 survivorship bias）
- AI 评分层与确定性下单代码的隔离
- OTOCO 挂单 / 三级熔断 / Telegram /halt
