# 5/24 Baseline 归档

归档时间: 2026-05-24
触发: 讨论"重新设计 hold time + SL 一体 / $2000 + 1x 整套切换" 前的基线快照.

## 系统状态 (归档点)
- 起始资金: $100, 杠杆 3x, 单笔 $20 保证金 (notional $60)
- 并发上限: 4 笔
- conv >= 6 硬过滤 (Phase 4.H)
- regime_gate = always (down LONG 全拒, Phase 4.J)
- down sub_regime shadow log (Phase 4.K)
- C 组 wick filter
- 客户端 SL 防御 (sl_breach_client)

## 实际数据 (归档点)
- Paper: 981 closed, 净 +$1012.57, 胜率 38.9%, 人均 +$1.03/笔 ← 真实 edge 存在
- Live: 406 closed, 累计 ~-$9 (含滑点 + sl_breach_client 链路损耗)
- 主要损耗源: sl_breach_client (237 笔), 其中 88 笔 paper 在后续走 trail/be (假止损), 漏赚 $450
- Paper hit_sl 565 笔中, 167 笔曾 MFE > 0.5% (29.6% 是可救的)

## 重要数据 bug 修正 (跨 phase 教训 #7)
Paper PnL 字段是 `realized_usdt_pnl` (live 是 `realized_pnl_usdt`, 顺序颠倒).
此前所有 paper vs live 对比 audit 都把 paper 当 $0 → 结论失效:
- Phase 4.I 限价单模拟 (paper 端基线错)
- Phase 4.K 阈值审计 (down+LONG paper PnL 错)
- 5/23 "拓宽 SL 净 -$33.78" 估算 (premature 漏赚错)

修正后真实情况:
- Paper engine 有正 edge (+$1012)
- 链路损耗 ≈ $1000+, 这是 live 不赚钱的真因
- 应优先治链路 (滑点 / 误触 SL), 而非整套换策略

## 归档内容
- `live_trades_history.json` — 实盘历史
- `paper_trades_history.json` — Paper 历史
- `paper_shadow_history.json` — Phase 4.K shadow log
- `pnl.json` — PnL 累计快照
- `regime_history.jsonl` — Regime 时序
- `analyses.jsonl` — 信号分析日志
- `PHASE_NOTES.md` — 落档至 5/24

## 后续切换决策
**未切换**. 5/24 audit 显示:
1. Paper edge 真实存在, 瓶颈在链路损耗, 不在策略
2. 信号语义是 30min 反转 (median 17min hold), 不支持 day-scale 长持
3. 多变量同时切 (capital × 20, leverage 3→1, SL 1%→5%, hold 30min→days) 无法归因
4. 风险绝对值放大 33x ($0.6 → $20/笔), edge 未验证前太激进

推荐渐进路径:
- Step 1: 修字段 bug, 重跑 4.A/B/C 真实效果
- Step 2: paper 端模拟 5% SL + 4h hold, 看 paper PnL
- Step 3: live 小幅试 2% SL (非 5%), $100 不变
- Step 4: 有正向数据再扩容
