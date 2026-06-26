# Cresus — 待修 / 待做清单

最后更新: 2026-06-26

## 优先级说明

- **P0**: 影响真金交易 / 资金安全, 当周必修
- **P1**: 改善执行质量 / 减 drag, 本月必修
- **P2**: 增加 observability / 防御纵深, 本季度
- **P3**: 优化体验 / 长期改进

---

## 🔴 P0 — 当前未做但必须

### BLESSUSDT 假账记录修正 (cosmetic, 不影响真钱)
**触发**: 2026-06-25 BLESSUSDT close Binance API 返 cumQuote=0, bot 记录
realized_pnl_usdt=-99.9984 (假亏). 真实 Binance UI 显示 +$7.51 win, 真实余额
$428.13 没受伤.

**已修代码层**: Phase 6.W (commit 871021b765) — 未来 cumQuote=0 自动 fallback.
但**已记录的这一条** BLESSUSDT -$99.99 不会回溯修复.

**影响**: dashboard 累计余额一直比真实低 ~$107 (看着吓人). 不影响真钱 (累计熔断
读 API 真余额), 不影响 bot 决策.

**修正方法 (需 bot 停下避免 race)**:
1. `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.cresus.live-trader.plist`
2. 写脚本: 在 live_trades_history.json + .live_trades_mainnet.json 找 BLESSUSDT
   trade_id=L1782378496_BLESSUS_L, 改 realized_pnl_usdt -99.9984 → +7.46
3. `launchctl bootstrap` 重启
**目标日期**: 下次维护窗口 (不紧急)

---

## 🟡 P1 — 改善执行 (跟 B-staged 进展挂钩)

### Phase 6.U → P2 升级条件
**当前**: B-staged P1, daily DD = $10, max_concurrent = 3.

**升 P2 条件** (全部满足):
- [ ] 连续 24h daily PnL ≥ -$5 (= 一日内有亏但可控)
- [ ] Phase 6.T MA30 拦截 ≥ 5 笔 (= gate 真在工作)
- [ ] Phase 6.Q sl-comp-skip 触发 ≥ 3 笔 (= adverse slip 救场)
- [ ] 0 次 kill switch 重触发
- [ ] BTC 仍在 up/stable regime

**P2 调整**:
- LIVE_PHASE_6U_BSTAGED_P1_DAILY_DD_USDT: $10 → $20
- 其他不变

**P3 完全恢复** (再 24h P2 通过后):
- 改 `LIVE_PHASE_6U_BSTAGED_P1_ENABLED = False`
- daily DD 退回默认 10% × $600 = $60

**目标**: 周内逐步升档.

### Phase 6.N — Maker mode 真启用 (canary_10pct)
**当前**: shadow 数据 39 笔, median maker_vs_market = 4.79 bps (低于 5 bps 阈值, marginal).

**启用条件**:
- [ ] shadow 样本 ≥ 100 笔
- [ ] median maker_vs_market ≥ 5 bps
- [ ] median paper_signal_age < 30s
- [ ] median spread < 10 bps

**实现需要** (估 1-2 天):
- `binance_client.place_post_only_limit_order()`
- `_try_mirror_open` 加 maker fork:
  - 10% (random) 走 maker path
  - 挂 LIMIT post-only at bid (LONG) / ask (SHORT)
  - 等 `LIVE_PHASE_6N_MAKER_TIMEOUT_SEC` (8s)
  - 未 fill → cancel + LIVE_PHASE_6N_MAKER_FALLBACK="skip" 决定
- 监控真实 maker fill rate vs shadow 预测

**目标日期**: 2026-07-05 (= 跑完 P2/P3 验证之后, 真有数据再做)

---

## 🔵 P2 — 架构 / 防御纵深

### cresus-trading 独立仓 (避免被其他 Claude session 污染)
**触发**: 2026-06-20 + 2026-06-23 两次 incident, 都因为 dashboard 仓被其他 Claude session 推 untracked 文件 (million-path / x-growth / mp500-bot) 导致 git pull 失败.

**方案** (4 阶段, 我们之前定的):
- Phase 1: 建 `chiang126126/cresus-trading` 空仓 + push cresus-system 副本 (低风险)
- Phase 2: 改 sync 脚本 dual-write 老仓 + 新仓 (中风险)
- Phase 3: cutover, sync 只推新仓 (中-高风险)
- Phase 4: 老仓清 cresus-system/ (低风险)

**用户需先做**: 浏览器建空仓 `chiang126126/cresus-trading`.

**目标日期**: 等用户准备好 (建仓 5min 后我接手).

### sl_compensation 真实生产 EV audit
**已部分做**: Phase 6.Q 不对称 gate 部署 (2026-06-22). 消除了 adverse-slip 时 comp 提前砍的 145 笔.

**仍待**: 跑通后 7-14 天数据 audit:
- ab_group A vs B 真实 live PnL 对比
- favorable-slip 时 comp 是否真有效 (按当前设计应有, 数据验证)
- 若 favorable-slip + comp 也微负, 考虑全关 comp

**目标日期**: 2026-07-06 (= 14 天数据)

---

## 🟢 P3 — 长期改进

### 自动提醒系统 (用户曾问 A/B/C 选项, 还没选)
**选项**:
- (A) 仓内 markdown 文档 ← 本文件就是
- (B) `pending_fixes_check.sh` + launchd 每天 09:00 跑, 到日期就 echo 到 `~/cresus-bot/logs/pending_fixes.log`
- (C) Telegram 推送 (需要熟悉用户的 telegram bot infra)

**当前**: (A) 已有, 等用户选 (B) 或 (C) 升级.

### Phase 6.O SL sync audit 数据可视化
**当前**: `last_sl_sync_at` / `sl_sync_count` / `sync_lag` 字段已持久化到 closed trade. dashboard 没显示.

**实现**:
- dashboard 加 "SL sync forensic" 面板
- 显示: 最近 N 笔 SL-BREACH 的 sync_state 分布 (synced vs DRIFT)
- 显示: DRIFT 笔的 paper_sl - live_sl 差值

**目标**: dashboard 重构时一起做.

---

## 📋 已完成 (历史记录)

| Phase | 日期 | 描述 |
|---|---|---|
| 6.M | 2026-06-18 | C 小币 tier 整体 block + 4 symbol blacklist |
| 6.O | 2026-06-18 | POLL_INTERVAL 5→2s + SL sync audit 字段 |
| 6.P | 2026-06-18 | fees retry 0.25s/0.5s backoff |
| 6.Q | 2026-06-22 | SL compensation 不对称 (adverse slip 跳过) |
| 6.R | 2026-06-22 | BNB→USDT fees 换算 |
| 6.S v1 | 2026-06-16 | sync_health_watchdog 初版 |
| 6.S v2 | 2026-06-20 | watchdog 阈值 4h→1h + .err 扫 |
| 6.S v2-fix | 2026-06-20 | grep -c || echo 0 bug 修 |
| 6.T | 2026-06-22 | MA30 趋势 gate (不接飞刀) |
| 6.U | 2026-06-22 | B-staged P1 daily DD $60→$10 |
| 6.V | 2026-06-24 | kill switch buffer +$20 (floor $420→$400) |
| 6.T-strict | 2026-06-24 | 新币 insufficient klines 改 block (不再 fail-safe pass) |
| 6.W | 2026-06-25 | close-side cumQuote=0 sanity check + userTrades fallback |
| SSH 迁移 | 2026-06-26 | GitHub 认证 HTTPS+PAT → SSH ed25519 (无 passphrase). PAT 第 4 次失效后永久解, launchd 不再依赖 keychain prompt |
| 6.S v3 | 2026-06-26 | watchdog 加认证失败关键字 + 检查 5 (unpushed 堆积 >20 告警). 修 v2 盲点: 本地 commit 新鲜但 push 全死时漏报 |

---

## 📋 阅读方法

- 每天早上跑 `cat ~/chiang126126.github.io/cresus-system/docs/PENDING_FIXES.md | head -50` 看头部 P0
- 完成一项 → 移到"已完成"表 + commit
- 新发现 → 加到对应优先级 + 记录触发原因
