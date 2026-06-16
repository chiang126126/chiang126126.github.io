#!/bin/bash
# sync_health_watchdog.sh — 独立 sync 健康监测.
#
# 2026-06-16 incident 教训: sync_signals.sh / velocity_fast_sync.sh 的 branch
# guard (sprite-catcher auto-inserted) 在 dashboard repo 偏离 main 时 silent
# exit 0, launchctl 看不出有问题, dashboard 静默停滞 16h 才被发现.
#
# 本脚本不修改 sync 脚本本身 (避免跟 sprite-catcher 拉锯), 而是独立观察:
#   1) dashboard repo 是否在 main 分支
#   2) main 最后 commit 是否在合理时间内 (默认 4h, 超时即告警)
#
# 任一不健康 → 写告警 log + touch .sync_unhealthy.flag + exit 1 (launchctl
# 显示 exit code != 0). Telegram operator / dashboard 可订阅 flag 文件做实时
# 推送, 但不依赖之 — 单纯 exit code 1 + log 已足够浮出问题.

set -u

REPO="$HOME/chiang126126.github.io"
BOT="$HOME/cresus-bot"
ALARM_LOG="$BOT/logs/sync_health_alarms.log"
HEALTH_FLAG="$BOT/.sync_unhealthy.flag"
STALE_SECS=14400   # 4 小时无 commit 即告警 (留足 idle 余地, 不误报)

alarm() {
    echo "$(date '+%FT%T') 🚨 $1" >> "$ALARM_LOG"
    touch "$HEALTH_FLAG"
}

# 检查 1: dashboard repo 存在 + 在 main 分支
if [ ! -d "$REPO/.git" ]; then
    alarm "dashboard repo 不存在或损坏: $REPO"
    exit 1
fi

BRANCH=$(cd "$REPO" && git branch --show-current 2>/dev/null || echo "?")
if [ "$BRANCH" != "main" ]; then
    alarm "dashboard repo 不在 main: branch=$BRANCH (sync guard 会静默 skip → 数据停滞). 修: cd $REPO && git stash && git checkout main && git pull"
    exit 1
fi

# 检查 2: main 最后 commit 时效 (broader signal, 防 sync 全死)
LAST=$(cd "$REPO" && git log -1 --format=%ct main 2>/dev/null || echo 0)
NOW=$(date +%s)
if [ "$LAST" -eq 0 ]; then
    alarm "git log 在 $REPO 失败 (repo 可能损坏)"
    exit 1
fi
AGE=$(( NOW - LAST ))
if [ "$AGE" -gt "$STALE_SECS" ]; then
    alarm "main 最后 commit 在 $((AGE / 60))min 前 (>$((STALE_SECS / 60))min), sync 可能全死"
    exit 1
fi

# 全部健康 — 清 flag
rm -f "$HEALTH_FLAG"
exit 0
