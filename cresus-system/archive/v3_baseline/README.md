# Cresus V3 — 5/24 Baseline 归档

**版本代号**: V3
**归档时间**: 2026-05-24
**归档触发**: V3 → V4 整套切换前的最终基线快照 (paper engine + live trader 将同步切换)

## V3 系统状态 (固化点)

### 策略参数
- 起始资金: $100
- 杠杆: 3x
- 单笔保证金: $20 (notional $60)
- 并发上限: 4 笔
- SL 距离: avg 0.98%, median 0.78%
- TP1/TP2/TP3: ~0.5% / 1.5% / 3%
- 信号语义: 30s/5min K 线短期反转 (technical reversion)
- 平均持仓: 36.9min (median 17.3min)

### 已部署的 V3 phase 累计
- Phase 4.A: A/B/C/D 四臂 (MD5 hash mod 4)
- Phase 4.B: B 组 SL 补偿
- Phase 4.C: C 组 wick filter
- Phase 4.D: D 组 regime gate
- Phase 4.F: Live 端 SL 防御 (sl_breach_client)
- Phase 4.G: A/B/C/D 平衡 dashboard
- Phase 4.H: conv >= 6 硬过滤
- Phase 4.J: regime_gate = always (down LONG 全拒)
- Phase 4.K: down sub_regime shadow log
- watchdog: Mac launchd 自愈服务

### Phase 4.I (E 组挂单) — **拒**
- v2 模拟显示 +$10/笔 改善, 真实世界打折后边缘价值, 决策放弃

## V3 实际数据 (截至 5/24)

### Paper engine (真实 edge 存在)
- 981 笔 closed
- 净 PnL: **+$1012.57**
- 胜率: 38.9% (382/981)
- 人均: **+$1.03/笔**
- PF > 1, 有边

### Live trader
- 406 笔 closed
- 累计 PnL: ~-$9 (链路损耗吃光 paper edge)
- 损耗源:
  - sl_breach_client: 237 笔 (其中 88 笔在 paper 端走 trail/be → 假止损, 漏赚 $450)
  - 滑点 + 限价拒单 + funding fee

### 关键洞察
1. Paper 有 edge, **瓶颈在链路损耗**, 不在策略本身
2. Paper hit_sl 565 笔中 167 笔 (29.6%) 曾 MFE > 0.5% → 可救空间存在
3. 信号语义是 30min 反转, **不支持 day-scale 长持**

## V3 期间发现的 7 个跨 phase 教训
1. 数据驱动 ≠ 等 100% 统计显著才动
2. A/B 测试不能成为"花真钱测已知答案"的借口
3. 用户质疑是最重要的 audit 工具
4. 简单数据 check > 复杂模拟
5. 模式补全的诱惑 (5/24 竞品分析事件)
6. Back-of-envelope 估算容易错 (5/24 SL 拓宽 +$25 → -$33 事件)
7. **字段名细节 bug 是最隐蔽的灾难** (`realized_pnl_usdt` vs `realized_usdt_pnl`)

## V3 → V4 切换决策

### V4 设计方向 (paper + live 同步)
- 资金: $100 → 渐进至 $2000 (不直接跳)
- 杠杆: 3x → **1x** (省 funding + 无 leverage decay)
- SL: ~1% → **5%**
- TP1/TP2/TP3: 0.5/1.5/3% → ~2/4/8% (与 SL 同比例放大)
- Hold time: 30min → **1-7 天**
- 信号语义: 短期反转 → **day-scale 趋势 + 突破**
- K 线源: 30s/1min/5min → **1h/4h/1d**

### V4 推荐执行路径 (4 步, 2-3 月)
| Step | 内容 | 时长 | 风险敞口 |
|---|---|---|---|
| A | Paper engine 重写 + 6 个月历史回测 (目标 PF > 1.3) | 1 周 | 无 (代码) |
| B | 新 paper engine 实时跑, 跟 V3 paper 并行 | 2 周 | 无 (paper) |
| C | Live 切换, **$200 + 1x** 起步 (非 $2000) | 2-4 周 | $200 |
| D | 阶梯扩容 $200 → $500 → $1000 → $2000, 每段需 30 天正 PnL | 1-2 月 | 阶梯 |

### 反对"立即整套切 + $2000 起步"的理由
- 新策略 edge 是 0 起步, 用 $2000 跑 0-edge = 大概率深亏
- 5 个变量同时改无法归因
- $400 notional × 5% SL = $20 风险/笔, 比 V3 ($0.6/笔) 放大 33x

## 归档内容
- `live_trades_history.json` — V3 实盘历史 (406 笔 closed)
- `paper_trades_history.json` — V3 paper 历史 (981 笔 closed)
- `paper_shadow_history.json` — Phase 4.K shadow log
- `pnl.json` — V3 PnL 累计快照
- `regime_history.jsonl` — V3 regime 时序
- `analyses.jsonl` — V3 信号分析日志
- `PHASE_NOTES.md` — V3 完整 phase 文档 (Phase 4.A → 4.K + 7 个跨 phase 教训)

## 后续比对方式
V4 上线后, 用以下指标跟 V3 baseline 对比:
- PF (paper): V3 baseline > 1, V4 目标 > 1.3
- 胜率 (paper): V3 38.9%, V4 目标 > 35%
- 链路损耗 (paper - live PnL): V3 ~$1000+, V4 目标降至 < $300
- 人均 PnL (live): V3 ~-$0.02, V4 目标 > +$0.50
