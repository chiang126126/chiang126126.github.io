# 精灵捕手 · 审计发现 & 未决项

> 这份文档是 **"上线前的体检报告"**。`AUDIT.md` 记录每个函数的正确性证明；
> 本文档记录**还没修 / 还没测 / 还可能出问题**的事项，按风险等级排序。
>
> 任何一条 SEVERE 没解决 → 不应实盘。

---

## ✅ 已修复（本次审计直接处理）

| ID | 问题 | 修复 |
|---|---|---|
| A1 | `_mc_momentum` 忽略 `lookback_days` 参数 | 改用 cutoff 时间戳搜基线点；跨度不足时返回 `mc_data_sufficient=False`，禁止判 BULL/BEAR |
| A2 | 没有"同标的累计暴露"上限 | `can_admit_intent` 加 `max_per_symbol_pct` 参数（**生产必须传 0.03**）|
| A3 | `samples.jsonl` 字段 `listing_date` 语义不一致 | 重命名为 `rally_start_date`，含义统一为"本次行情起点" |
| A4 | 没有数据新鲜度检查 | 新增 `features/freshness.py`：`is_fresh()` / `assert_fresh(StaleDataError)` |
| A6 | 没有 ATR helper（L6 trailing stop 必备）| `features/indicators.py` 加 `true_range()` / `atr(Wilder smoothing)` |

---

## 🔴 SEVERE — 上线前必须处理

### S1 · 信号 strength 跨策略不可比，没有 tiebreaker

**症状**：4 套策略对 4 个不同标的同时触发，进入 L7 admission 检查时按"哪笔先到达"决定，可能丢失最强信号。

**根因**：
- `TrendFollowSignal.strength` = ema_spread × 5 × holders_factor
- `SupportCollapseSignal.strength` = vol_ratio / (mul × 2)
- `ShortVacuumSignal.strength` = 0.5 × oi_drop + 0.5 × wick_ratio
- `DivergenceSignal.strength` = 1.0 或 0.6 (固定档)

→ 数值范围都 0-1 但分布完全不同，sort 出来没意义。

**应对（私仓做）**：
- 短期：给每套策略**独立**的"信号到达队列"，分别按到达顺序消费
- 中期：维护一份"历史 PnL 期望"表（每策略每信号强度档），实盘 PnL 加权后做跨策略排序

### S2 · 没有"同标的冷却时间"

**症状**：trend_follow 在 X 标的亏损平仓后，第二天又触发同一标的 → 革命战士再次接刀。

**应对**：
- 私仓 L7 维护 `last_loss_ts_by_symbol[symbol]`
- 同标的距离上次亏损 < 72h → admission 拒

### S3 · `manipulation_level` 在 OI 数据稀薄时仍然算出
**症状**：刚上交易所的 token，OI 历史 < 3 小时，`_pearson_of_diff` 仍然产出一个值，pool_router 可能错把它路由到 OPERATOR 或 FRIENDLY。

**应对**：
- 私仓在 stratify_oi 上层包一层："warnings 里含 `single_exchange_only` / `corr_undefined` → 强制走 NEUTRAL 池"
- 或：要求 stratify_oi 在 warnings 非空时把 manipulation_level 设为 None，下游显式处理

### S4 · 蜜罐检测可被静默跳过

**症状**：`evaluate_long_safety(sim_result=None)` 不会拒绝，调用方忘传 → 错过蜜罐。

**应对**：
- 私仓 strategy orchestrator 必须把 sim_result 列为**必填**字段；如果暂时拿不到（如 CEX 现货不需要 sim）→ 显式传一个 "skipped" 标识，**不能默默 None**

### S5 · `samples.jsonl` 是研究估算，不是真实数据

**症状**：base_low/peak_high 是从公开报道拼出来的，±10–20% 偏差。直接用它跑回测会校准出错误的阈值。

**应对**：
- 私仓 L8 真的开始回测之前，要从 Binance/CoinGecko/CoinMarketCap 拉**实际日线**，覆盖 22 个样本的窗口
- 用脚本根据真实数据更新 samples.jsonl（peak_high / base_low / pump_multiplier）

---

## 🟠 HIGH — 不影响功能，但会拉低胜率/期望

### H1 · `detect_distribution_divergence` 默认用总 OI 作为主力 OI 代理

**症状**：`operator_oi_fraction=1.0` 默认值意味着没接入 `stratify_oi` 的分层结果。
- 真实主力 OI 平稳但 follow OI 暴增 → 总 OI 涨 → 不会触发 distribution
- 真实主力 OI 在撤、follow OI 跌得更快 → 总 OI 跌 → 会触发，但可能不是真出货

**应对**：私仓串联时，先调 `stratify_oi`，把 `result.operator_oi / result.total_oi` 传进来。

### H2 · `_linear_slope` 不返回显著性

**症状**：弱斜率（如噪声里偶然连续涨 6 根）和强斜率（明显趋势）一视同仁。

**应对**：私仓可选升级到 scipy.stats.linregress，按 p_value < 0.1 才认为信号成立。

### H3 · `signal_strength` 没和历史 PnL 校准

**症状**：strength 是规则里硬编码的算式，没和"过去这个 strength 档位实盘赚了多少"挂钩。

**应对**：私仓 L8 周报跑完之后，把每策略每 strength 档的真实 T+N 回报反喂回打分公式。

### H4 · 4 套策略可能同时被同一根 K 线触发

**症状**：trend_follow 和 support_collapse 在同一 token、同一 K 线、相反方向同时触发不太可能但理论上可能（pool 不同所以正常情况下不会，但若 pool 边界波动 …）。

**应对**：L7 admission 加一道："同 token 当前已有反向 intent 待确认 → 拒"。

### H5 · 没有 funding rate 反噬保护（Module B 专属）

**症状**：spec doc 28 提到 funding > 0.1% 减仓 50%，但 L6 实现是私仓的事；公仓的 plan_short_* 都没把 funding 作为入场考量。

**应对**：私仓 L6 在持仓监控循环里查 funding，触发后调用减仓函数。

---

## 🟡 MEDIUM — 已知简化

### M1 · `samples.jsonl` 数据精度
- 价格估算 ±10–20%
- 日期到天的精度，没小时级
- top10_share / vol_oi_ratio 大多 null

### M2 · funder dedup 取第一非访问源，未按金额加权
**影响**：多源资金的 holder 可能被归错簇；上限不大但可能低估同源度。

### M3 · 没有最小下单量 / step size 处理
**影响**：sizing.py 算出来的 qty 在 Binance 下单时可能 < minNotional 或 step 不对齐。私仓必须自己 round。

### M4 · EMA 用 SMA 作种子
**影响**：前几根与 TradingView/Binance 显示的 EMA 略有差异；长期收敛，短期注意。

### M5 · short_vacuum 假设 candles 是 1m，没校验
**影响**：私仓传错周期会让 `window_minutes` 失真。文档已说明。

### M6 · 没有最大杠杆 tier 感知（Binance Futures）
**影响**：sizing.py 假设 max_leverage 是固定值；实际 Binance 在大仓位时会降杠杆。私仓必须按 tier 调用。

---

## 🟢 LOW — 文档已经说明

### L1 · datetime 是 naive UTC（按约定，不强制）
### L2 · `_pearson_of_diff` 要求严格时间戳对齐，不做插值
### L3 · 没有覆盖率报告 / 集成测试 / 性能基准

---

## 私仓必读清单

私仓 `cresus-bot` 实现 L4/L6/L8 时，必须显式处理：

1. **A2 修复后**：每次调用 `can_admit_intent` **必须**显式传 `max_per_symbol_pct=0.03`
2. **A4 修复后**：每次进入信号检测之前，对所有时序数据跑一次 `assert_fresh(...)`，stale 直接抛错跳过
3. **S1**：4 套策略各自独立队列消费，不要混跨策略 sort
4. **S2**：维护 last_loss_ts_by_symbol，admission 加冷却检查
5. **S3**：stratify_oi 的 warnings 非空时强制 NEUTRAL
6. **S4**：sim_result 在策略入口列为必填字段
7. **H1**：先调 stratify_oi 拿到 operator_oi/total_oi，再传 fraction 给 divergence
8. **M3**：下单前一律 `round_to_step(qty, step_size)` + 检查 `qty * price >= minNotional`
9. **M6**：拉 Binance Futures leverage bracket，按实际 tier 卡 max_leverage

每一条**在 PR review checklist 里都要打勾**，否则不合并到主 branch。
