# cresus-watchdog 部署指南

Phase 4.F (2026-05-21) 新增的健康监控脚本。

## 它解决什么问题

之前 6 天数据显示:
- **39% 漏单率** (209/541 paper 信号)
- **62% 漏单归因 Mac 停机** (130/209)
- launchd 服务卡在 legacy domain 时, modern API (`launchctl print`, `kickstart`) 摸不到, 数据 24h 不更新都没人知道

## 它怎么工作

每分钟检查:
- `~/cresus-bot/live_trades_history.json` 距今多久
- `~/cresus-bot/paper_trades_history.json` 距今多久

判定:
- 都 ≤ 5min → 健康, 不写日志 (避免每小时 60 行噪音)
- 任一 > 5min → 写日志, 计数 +1
- 连续 ≥ 2 次失败 (≈ 2min 持续异常) → 自动 `launchctl kickstart` 两个服务

恢复时记录 `RECOVERED`, 计数清零.

## 部署 (在你的 Mac 上跑)

```bash
# 1. 链或拷贝脚本 (注意路径)
ln -sf /Users/mangzi/chiang126126.github.io/cresus-system/scripts/cresus-watchdog.sh \
       ~/cresus-bot/cresus-watchdog.sh

# 2. 拷贝 plist
cp /Users/mangzi/chiang126126.github.io/cresus-system/scripts/com.cresus.watchdog.plist \
   ~/Library/LaunchAgents/

# 3. bootstrap 到现代 domain (跟 5/19 修 live-trader 一样的命令)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cresus.watchdog.plist

# 4. 启用持久化
launchctl enable gui/$(id -u)/com.cresus.watchdog

# 5. 验证
launchctl print gui/$(id -u)/com.cresus.watchdog | head -10
# 期望看到 state = waiting/running + run interval = 60 seconds
```

## 监控 watchdog 自己

```bash
# 看 watchdog 最近活动
tail -50 ~/cresus-bot/logs/watchdog.log

# 健康时这个日志是空的 (设计如此, 只在异常 + 恢复时记)
# 异常时会看到:
#   [2026-05-21 16:04:18] ⚠ UNHEALTHY (1/2) — live_trades_history 7min stale
#   [2026-05-21 16:05:18] ⚠ UNHEALTHY (2/2) — ...
#   [2026-05-21 16:05:18] 🔧 ATTEMPTING RECOVERY — kickstart services
#   [2026-05-21 16:06:18] ✓ RECOVERED — live=0min paper=0min (after 1 failures)
```

## 配置可调

在 `cresus-watchdog.sh` 顶部:

```bash
STALE_MIN=5            # 多少 min 未更新视为异常 (默认 5)
RECOVER_THRESHOLD=2    # 连续 N 次失败才 kickstart (默认 2)
SERVICES=(             # 哪些服务被监控
    "com.cresus.live-trader"
    "com.cresus.velocity-fast-sync"
)
```

## 卸载

```bash
launchctl bootout gui/$(id -u)/com.cresus.watchdog
rm ~/Library/LaunchAgents/com.cresus.watchdog.plist
```

## 已测试场景

通过 sandbox 测试 (5/5 pass):
- 文件新鲜 → 不写日志
- 文件 10min 旧 → 第 1 次仅记录, 不 kickstart
- 第 2 次失败 → 触发 kickstart
- 文件恢复 → 记 RECOVERED, 计数清零
