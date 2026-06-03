# 29 — Mainnet Pilot Playbook

> **Live operational manual** for Cresus mainnet pilot mode.
> **Created**: 2026-06-03 (commit `c7ff52e05`)
> **Status**: 🟢 LIVE — running on Binance mainnet sub-account `cresus-pilot` with **$600 USDT** real capital.
> **Version**: Phase 6.A + 6.A-fix + 6.A-margin + 6.A-fix2

---

## 目录

1. [资金分配](#1-资金分配)
2. [杠杆 / 保证金](#2-杠杆--保证金)
3. [入场决策链(15 道 Gate)](#3-入场决策链15-道-gate)
4. [入场执行](#4-入场执行)
5. [持仓管理(Phase A/B/C)](#5-持仓管理phase-abc-状态机)
6. [出场触发](#6-出场触发6-种)
7. [出场执行](#7-出场执行)
8. [风控熔断(5 层)](#8-风控熔断5-层)
9. [当前不做的事](#9-当前不做的事-not-handled)
10. [紧急控制](#10-紧急控制)
11. [一句话总结](#11-一句话总结)
12. [部署 / 回退](#12-部署--回退)
13. [更新日志](#13-更新日志)

---

## 1. 资金分配

| 项 | 数值 | 含义 |
|---|---|---|
| 总资金 | **$600 USDT** | Binance 子账户 `cresus-pilot` |
| 单笔上限(score 5) | **$150** | 普通钻石信号 |
| 单笔上限(score 6) | **$200** | 中等 conviction |
| 单笔上限(score 7) | **$300** | 高 conviction 钻石(理论最大单笔) |
| 单笔上限(score 8-10) | **$150** | 历史数据显示 EV 反转,降回 $150 |
| 最大并发仓位 | **2 笔** | 同时持有 |
| 最大部署资金 | **$450** | 现金保留 25% (~$150 缓冲费用/滑点) |
| 理论 worst-case 暴露 | $300 × 2 = $600 | 但被 deploy cap $450 限制 |

**典型场景**:2 笔 score 5 × $150 = **$300 部署**(50% 资金),还有 $300 现金缓冲。

代码常量(`live_trader.py` mainnet_pilot block):
```python
LIVE_NOTIONAL_BY_SCORE = {5: 150, 6: 200, 7: 300, 8: 150, 9: 150, 10: 150}
LIVE_MAX_CONCURRENT = 2
LIVE_MAX_DEPLOY_USDT = 450.0
LIVE_STARTING_CAPITAL_USDT = 600.0
```

---

## 2. 杠杆 / 保证金

| 项 | 数值 |
|---|---|
| **杠杆** | **1x**(强制,每次开仓 `set_leverage(1)`) |
| **保证金模式** | **ISOLATED**(强制,每次开仓 `set_margin_type(ISOLATED)`,Phase 6.A-margin) |
| **强平风险** | 几乎为零(1x 下币要归零才爆,~15 min 持仓不可能) |
| **最大单仓亏损** | = 该仓 notional × 100%(Isolated 不拖累其它仓) |

**为什么 1x + Isolated**:
- 1x:PnL = 价格变动 × notional,没有杠杆放大,简单可控
- Isolated:每仓独立抵押,避免某一仓爆仓影响其它仓

每次开仓自动序列:
1. `client.set_leverage(symbol, 1)` — Phase 4 已有
2. `client.set_margin_type(symbol, "ISOLATED")` — **Phase 6.A-margin 新加**
3. `client.open_position(...)`

`set_margin_type` 幂等(Binance 返 `-4046 No need to change` → swallow 视为成功)。失败仅 warn,不阻塞开仓。

---

## 3. 入场决策链(15 道 Gate)

Paper 出钻石信号 → `live_trader.py` 每 5s 轮询 → 经过 15 道 gate **全过**才开仓:

| # | Gate | 拒绝条件 | Phase |
|---|---|---|---|
| 1 | 已 mirror 过(去重) | `paper_id ∈ mirrored_paper_ids` | — |
| 2 | 永久 symbol 黑名单 | `symbol ∈ LIVE_SYMBOL_BLACKLIST` | 4.B |
| 3 | session -1121 黑名单 | 本进程内 symbol 不可交易 | 4.W |
| 4 | 白名单(OBS bypass) | `OBS=True` 跳过此项 → 主网放行所有 paper symbol | 4.B |
| 5 | Conviction 阈值 | 当前无最小限,score 5+ 都接受 | 4.H |
| 6 | max_concurrent | 已 2 笔 → 拒 | 3.3.b |
| 7 | 1 symbol per pos | 同 symbol 已开 → 拒 | — |
| 8 | 信号年龄 | paper trade > 10 min → 拒(防陈年信号) | — |
| 9 | 方向有效 | `direction ∈ {LONG, SHORT}` | — |
| 10 | **Phase 4.J regime gate** | **BTC down + LONG → 硬拒**(数据驱动 0/10 胜 p=0.042) | 4.F/J |
| 11 | Phase 5.R sub_regime allow | 当前**空集合**,无影响 | 5.R |
| 12 | Phase 5.S size mult=0 reject | 当前**空 dict**,无影响 | 5.S |
| 13 | **Phase 4.M funding gate** | funding ≥ +0.05% → 拒(paper 历史 -$0.85/笔) | 4.M |
| 14 | **预滑点 gate**(Phase 4.A/4.V) | intensity=1→>100bps / =2→>150bps / =3→>200bps 拒 | 4.A/V |
| 15 | Cash reserve | `deployed + new > $450` → 拒 | 3.3 |

**通过率**(基于历史):paper 中约 27-35% 通过 mirror。剩下被 age / max_concurrent / regime 等过滤。

---

## 4. 入场执行

```
1. set_leverage(symbol, 1)         ← 强制 1x
2. set_margin_type(symbol, ISOLATED) ← 强制隔离 (Phase 6.A-margin)
3. get_book_ticker(symbol)         ← 拉实时盘口
4. 计算 IOC 限价 = ask (LONG) 或 bid (SHORT)
5. 预滑点校验(再次保险)
6. open_position(IOC limit @ 限价) ← Phase 4.U/V
   - LONG: BUY 限价单
   - SHORT: SELL 限价单
   - IOC: 立即成交否则取消
7. 检查 fill:
   - 完整 fill → 记录 live_trade
   - 部分 fill → 应急 close (Phase 5.H, 防孤儿)
   - 全 EXPIRED → 返回 None(等下 tick 重试)
8. Phase 5.G post-fill 结构检查(actual_fill vs paper_entry 偏离过大 → 应急平)
```

---

## 5. 持仓管理(Phase A/B/C 状态机)

| Phase | 触发条件 | 行为 |
|---|---|---|
| **A** | 开仓 | 持有,等 TP1 或 SL |
| **B** | 价格触及 TP1 | 部分平 + trailing SL 上移 |
| **C** | 价格触及 TP2 | 全平 或 继续 trailing 到 trail SL |

具体参数继承自 paper 信号:
- **SL price**: paper 设定 + Phase 4.D 补偿(`live_sl = paper_sl + slippage offset`,让 live 实际 SL 距离 ≈ paper 设计)
- **TP1 / TP2 price**: paper 设定原样
- **Trailing stop**: 价格创新高(LONG)或新低(SHORT)时,SL 跟随移动
- **持仓时间上限**: paper 端 `auto_close_hours` 控制(通常 2-4h)

---

## 6. 出场触发(6 种)

| # | 触发 | 描述 |
|---|---|---|
| 1 | **`sl_breach_client`** | live 端 SL 被触及(Phase 4.E wick filter:连续 4+ tick 越 SL 才触发,防单次 wick 假触发) |
| 2 | **`paper:hit_tp1`** | paper 端 TP1 触发,live 镜像平 50%(部分平) |
| 3 | **`paper:hit_tp2`** | paper 端 TP2 触发,live 全平 |
| 4 | **`paper:hit_trail`** | paper trailing stop 触发,live 镜像平 |
| 5 | **`paper:timeout`** | paper 持仓超时(2-4h)自动平,live 跟着平 |
| 6 | **`already_closed_externally`** | exchange 上已无仓位(手动平 / 异常),清 live state |

特殊出场场景:
- **Phase 5.E 熔断**: 30 min 内 4+ SL → 暂停**开新仓** 30 min(已开仓不动)
- **Phase 5.G 结构检查失败**: 入场后立刻发现 fill 偏离 paper 信号过大 → 应急 close
- **Daily DD 触发**: 日亏 ≥ $60 → 阻塞新开仓(已开仓不动)
- **Kill switch**: 余额 < $420(累计亏 30%)→ 写 emergency-stop flag,**全停**

---

## 7. 出场执行

```
1. close_position(symbol, side, expected_entry_price=avg_fill_price)
   - 拉当前持仓
   - 撤所有挂单(避免 race)
   - reduceOnly market close
   - Phase 5.A-fix: qty > maxQty 时自动 chunking

2. Phase 5.T 双重 PnL 防御:
   - 用 caller 的 expected_entry_price (=本地 avg_fill_price) 计算 PnL
   - 偏离 Binance API 报的 entryPrice > 1% → log warn + 用 expected
     (防 testnet DOGSUSDT $1803 bug:API 返 10x 错值, PnL 失真 600x)
   - 绝对 PnL > 5× notional → realized_pnl_suspect=True + log error

3. 实际 fees 拉取(_actual_commission_usdt)
4. 写入 live_trade 记录
```

---

## 8. 风控熔断(5 层)

| 层 | 触发 | 行动 |
|---|---|---|
| **L1 — funding 不利** | paper signal funding ≥ +0.05% | 不开仓(单笔级别) |
| **L2 — Phase 5.E SL 连损** | 30min 内 ≥ 4 笔 SL | 暂停开新仓 30min |
| **L3 — 日 DD** | 日已实现亏 ≥ **$60**(资金 10%) | 阻塞新开仓,直到次日 UTC 0:00 重置 |
| **L4 — 累计 DD KILL SWITCH** | 子账户余额跌破 **$420**(累计亏 30%) | 自动写 `~/.cresus-emergency-stop`,**全停** |
| **L5 — 手动 emergency** | `touch ~/.cresus-emergency-stop` | **全停**(含已开仓位的新管理周期) |

代码常量:
```python
LIVE_DAILY_DD_LIMIT_USDT = 60.0       # L3
LIVE_TOTAL_DD_LIMIT_PCT = 30.0        # L4 (kill switch at -$180 cumulative)
LIVE_CB_SL_THRESHOLD = 4              # L2 trigger count
LIVE_CB_WINDOW_MIN = 30               # L2 window
LIVE_CB_PAUSE_MIN = 30                # L2 pause duration
```

---

## 9. 当前不做的事(NOT handled)

- ❌ **币种选择**:bot **不主动选币**,完全镜像 paper 选择
- ❌ **方向选择**:bot **不主动选方向**,paper 决定 LONG / SHORT
- ❌ **仓位倾斜**(Phase 5.S):mainnet **关闭**,所有桶 ×1.0(待 mainnet 数据足够再启用)
- ❌ **down + LONG**:Phase 4.J 一律拒(可考虑 Phase 5.R 子状态放开,但当前 allow-list 空)
- ❌ **funding ≥ +0.05%**:Phase 4.M 一律拒
- ❌ **预滑点过大**:Phase 4.V 拒
- ❌ **time-of-day 自适应**:无
- ❌ **波动率自适应 sizing**:无
- ❌ **持有过夜**:paper auto_close 通常 2-4h,几乎不跨 funding 周期

---

## 10. 紧急控制

| 想做什么 | 命令 |
|---|---|
| 立刻暂停**新仓**(已开不动) | `touch ~/.cresus-pause` |
| 立刻**全停**(含已开仓位的新管理) | `touch ~/.cresus-emergency-stop` |
| 恢复正常 | `rm ~/.cresus-pause ~/.cresus-emergency-stop` |
| 看实时 daily PnL | `grep daily_pnl ~/cresus-bot/logs/live_trader.log \| tail -3` |
| 看 mainnet 实际余额 | Binance 子账户 → Futures Wallet |
| 完全关 mainnet pilot | plist 删 `EnvironmentVariables` 块 + reload |

---

## 11. 一句话总结

> **"被动镜像 paper 钻石信号(score≥5),按 score 分 $150/$200/$300 仓位,1x leverage + Isolated margin,经 15 道 gate 过滤后用 IOC 限价开仓,跟随 paper 的 SL/TP/trailing 平仓,5 层熔断保护单日 $60 / 累计 $180 亏损上限。"**

---

## 12. 部署 / 回退

### 部署 mainnet pilot
1. Binance 子账户 + API key(Reading + Futures only,关 Withdrawals)
2. `~/cresus-bot/binance_keys_mainnet.json` 文件(testnet=false)
3. `touch ~/.allow-live` 二级安全闸门
4. plist 加 `EnvironmentVariables` 块:
   ```xml
   <key>EnvironmentVariables</key>
   <dict>
       <key>CRESUS_MODE</key>
       <string>mainnet_pilot</string>
       <key>CRESUS_PILOT_CAPITAL</key>
       <string>600</string>
       <key>BINANCE_KEYS_PATH</key>
       <string>/Users/hong/cresus-bot/binance_keys_mainnet.json</string>
   </dict>
   ```
5. `launchctl unload && load`

### 回退 testnet(任意时刻)
1. plist 删 `EnvironmentVariables` 块
2. `launchctl unload && load`
3. bot 自动恢复 testnet 默认参数($2000 资金 / $400 单笔 / max_concurrent=4 / Phase 5.S audit 配置)

### 完全停止
1. `rm ~/.allow-live`(二级闸门撤销)
2. `launchctl unload ~/Library/LaunchAgents/com.cresus.live-trader.plist`

---

## 13. 更新日志

| 日期 | Commit | 变更 |
|---|---|---|
| 2026-06-03 | (手动) | **Paper engine 部署修复** — copy 最新 volume_velocity_scanner.py 从 repo 到 `~/cresus-bot/scripts/`(此目录不是 git checkout,自 5/14 一直跑老代码)+ reset paper state 到 $600。Paper 现在跟 live 同款 sizing/资金。 |
| 2026-06-03 | `60215ef5e` | Phase 6.A paper sync — paper engine CRESUS_MODE-aware override |
| 2026-06-03 | `cfbf9a8d5` | Playbook 29 初稿 |
| 2026-06-03 | `c7ff52e05` | Phase 6.A-fix2: LIVE_HISTORY 改回单一路径修 dashboard 同步 |
| 2026-06-03 | `dca67a8a1` | Phase 6.A-fix: $LIVE_STARTING_CAPITAL / kill switch / LIVE_NOTIONAL / state 文件独立 |
| 2026-06-03 | `e2db198a4` | Phase 6.A-margin: 自动 ISOLATED margin |
| 2026-06-03 | `68904cb52` | Phase 6.A 后续(R2/R7/Y4/Y8 paranoid review 修复) |
| 2026-06-03 | `327d951da` | Phase 6.A: Mainnet Pilot Mode 基础设施 |

### 历史 Phase 路线图(参考)

- **Phase 4.x**: testnet 时代 — regime gate / funding gate / IOC limit / wick filter
- **Phase 5.A-N**: 各种数据驱动微调(score-based sizing / SHORT/down audit / sub_regime shadow log)
- **Phase 5.R**: sub_regime aware gate(基础设施 ready, default empty)
- **Phase 5.S**: regime-aware size multiplier(testnet audit 后 deployed, mainnet 关闭)
- **Phase 5.T**: 防 Binance API entryPrice 错位(DOGSUSDT $1803 bug 修复)
- **Phase 6.A**: Mainnet Pilot Mode ← **当前阶段**

---

## 14. 相关文档

- `00-architecture.md` — Cresus 整体架构
- `02-setup-binance-api.md` — Binance API key 准备
- `13-dashboard.md` — Dashboard 数据流
- `14-pnl-tracking.md` — PnL 追踪
- `16-risk-control.md` — 风控设计原理
- `25-signal-state-machine.md` — Phase A/B/C 状态机详解
- `28-week2-postmortem-actions.md` — 上周复盘 + 计划

---

## 15. 反馈机制

观察期(头 1 周)发现的任何问题在 commit message 标 `Phase 6.A-fix-XYZ` 并更新本文档 §13 更新日志。

---

## 16. ⚠️ 已知 architecture gap(后续待修)

### 16.1 — Paper engine 部署不在 git checkout

**问题**:`~/cresus-bot/scripts/` **不是 git 仓库**,paper engine `volume_velocity_scanner.py` 在该目录下,**手动 cp 部署**。自 2026-05-14 起,该副本一直冻在那一刻 — 期间我们 push 到 repo 的所有 paper 优化(Phase 5.A 分档仓位 / Phase 5.K 调整 / Phase 5.B BTC trend conviction 等)**实际上没有在 paper 端生效过**。

**影响**:
- 之前 paper engine 表面"按 score 分档",实际 fall through 到 `PAPER_NOTIONAL_PER_TRADE_USDT = 400` 默认
- audit 数据基于的 paper PnL 可能有偏差
- paper-live 对比的"paper 端"实际跑的是 5/14 之前的策略

**触发时机**:Phase 6.A 启用 mainnet pilot 时,paper engine 没用 `CRESUS_MODE` 行为暴露此问题。

**临时修复**(2026-06-03):
```bash
cp ~/chiang126126.github.io/cresus-system/scripts/volume_velocity_scanner.py \
   ~/cresus-bot/scripts/volume_velocity_scanner.py
launchctl unload && launchctl load ~/Library/LaunchAgents/com.cresus.velocity-scanner.plist
```

**长期修复(待做)**:`~/cresus-bot/scripts/` 改成 git checkout(symlink to repo 或定期自动 sync 脚本)。

### 16.2 — Live trader 部署链路 vs Paper engine 部署链路

| | live trader | paper engine |
|---|---|---|
| plist `ProgramArguments` | 直接指向 `~/chiang126126.github.io/cresus-system/scripts/live_trader.py` ✅ | 通过 `~/cresus-bot/scripts/run_velocity_scanner.sh` → `~/cresus-bot/scripts/volume_velocity_scanner.py` ❌ |
| 受 git pull 影响 | 立刻生效 | **不受影响**(需手动 cp 后才生效) |

→ Mainnet pilot 期间所有 paper 相关 code change **必须同时 cp 到 `~/cresus-bot/scripts/`** 才生效。建议每次 git pull 后跑:
```bash
cp ~/chiang126126.github.io/cresus-system/scripts/volume_velocity_scanner.py ~/cresus-bot/scripts/volume_velocity_scanner.py
launchctl unload ~/Library/LaunchAgents/com.cresus.velocity-scanner.plist
launchctl load ~/Library/LaunchAgents/com.cresus.velocity-scanner.plist
```

### 16.3 — Paper engine 内部 state 文件清单(reset 时要全包)

私有(.开头,paper engine 读写):
- `~/cresus-bot/.paper_trades.json` ← source of truth, 主仓
- `~/cresus-bot/.paper_shadow_trades.json` ← shadow 仓
- `~/cresus-bot/.velocity_dedup.json` ← 去重缓存(不需 reset)
- `~/cresus-bot/.velocity_outcomes.json` ← outcomes 缓存(不需 reset)
- `~/cresus-bot/.velocity_tg_cooldown.json` ← TG 冷却
- `~/cresus-bot/.velocity_email_cooldown.json` ← email 冷却

公开(发布给 dashboard):
- `~/cresus-bot/paper_trades_history.json`
- `~/cresus-bot/paper_shadow_history.json`
- `~/cresus-bot/volume_velocity_alerts.json`
- `~/cresus-bot/velocity_winrate.json`
