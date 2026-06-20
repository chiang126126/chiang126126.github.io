#!/bin/bash
# sync_health_watchdog.sh v2 — 独立 sync 健康监测.
#
# Phase 6.S (2026-06-20) — v2 加强:
#   - 阈值 4h → 1h (= 6 倍数据 sync 周期 + 余地)
#   - 加 .err / .log 扫近期 Aborting / 失败关键字检测
#   - 加 dashboard 文件 mtime 健康检查 (检测"sync 没 cp 但没报错"的隐性死)
#   - 加 untracked 冲突检测 (检测会 block git pull 的 untracked 文件)
#
# 历史 incident:
#   2026-06-16: dashboard repo 在 wrong branch → sync silent skip 16h.
#   2026-06-20: untracked million-path 文件 block git pull 2h39m.
#   v1 都漏了这 2 个 case (commit < 4h + branch == main).
#
# 任一不健康 → 写告警 log + touch .sync_unhealthy.flag + exit 1.

set -u

REPO="$HOME/chiang126126.github.io"
BOT="$HOME/cresus-bot"
ALARM_LOG="$BOT/logs/sync_health_alarms.log"
HEALTH_FLAG="$BOT/.sync_unhealthy.flag"
STALE_SECS=3600   # v2: 1 小时无 commit 即告警 (v1 是 4h, 太宽松错过 2h39m incident)
DASHBOARD_FILE="$REPO/cresus-system/dashboard/live_trades_history.json"
DASHBOARD_STALE_SECS=900  # dashboard 文件 15 分钟没更新即可疑

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
    alarm "dashboard repo 不在 main: branch=$BRANCH. 修: cd $REPO && git stash && git checkout main && git pull"
    exit 1
fi

# 检查 2: main 最后 commit 时效 (v2: 阈值 4h → 1h)
LAST=$(cd "$REPO" && git log -1 --format=%ct main 2>/dev/null || echo 0)
NOW=$(date +%s)
if [ "$LAST" -eq 0 ]; then
    alarm "git log 在 $REPO 失败 (repo 可能损坏)"
    exit 1
fi
AGE=$(( NOW - LAST ))
if [ "$AGE" -gt "$STALE_SECS" ]; then
    alarm "main 最后 commit 在 $((AGE / 60))min 前 (>$((STALE_SECS / 60))min), sync 可能死了"
    exit 1
fi

# 检查 3 (v2 新): dashboard 关键文件 mtime — 防 "sync 跑了但没 cp" 隐性死
if [ -f "$DASHBOARD_FILE" ]; then
    DASHBOARD_MTIME=$(stat -f %m "$DASHBOARD_FILE" 2>/dev/null || stat -c %Y "$DASHBOARD_FILE" 2>/dev/null || echo 0)
    if [ "$DASHBOARD_MTIME" -gt 0 ]; then
        DASHBOARD_AGE=$(( NOW - DASHBOARD_MTIME ))
        if [ "$DASHBOARD_AGE" -gt "$DASHBOARD_STALE_SECS" ]; then
            alarm "dashboard live_trades_history.json mtime $((DASHBOARD_AGE/60))min 前 (>$((DASHBOARD_STALE_SECS/60))min)"
            exit 1
        fi
    fi
fi

# 检查 4 (v2 新): sync.err / velocity_fast_sync.log 近期是否有 Aborting / pull 失败
# 解决 2026-06-20 untracked 冲突 incident — sync 一直跑但每次 Aborting, 数据没流出
SYNC_ERR="$BOT/logs/sync.err"
VFS_LOG="$BOT/logs/velocity_fast_sync.log"
RECENT_SECS=600  # 10 分钟内的错误才算"近期"

check_recent_aborting() {
    local f="$1"
    [ -f "$f" ] || return 0   # 文件不存在不算错
    # 看最后 20 行有没 Aborting / pull --rebase 失败.
    # 2026-06-20 fix: 用 grep | wc -l 替代 grep -c | ... || echo 0 — 后者在
    # grep 无匹配时 exit 1, || echo 0 会附加另一个 0 让 has_abort="0\n0",
    # 后续 [-gt] 比较抛 "integer expression expected".
    local has_abort
    has_abort=$(tail -20 "$f" 2>/dev/null | grep -cE 'Aborting|pull --rebase 失败|untracked working tree files would be overwritten' 2>/dev/null || true)
    has_abort=${has_abort:-0}
    if [ "$has_abort" -gt 0 ] 2>/dev/null; then
        # 看 file mtime 是不是近期 (= 这个错误是新的, 不是历史尾巴)
        local f_mtime
        f_mtime=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0)
        if [ "$f_mtime" -gt 0 ] 2>/dev/null; then
            local f_age=$(( NOW - f_mtime ))
            if [ "$f_age" -lt "$RECENT_SECS" ] 2>/dev/null; then
                return 1   # 近期有 Aborting
            fi
        fi
    fi
    return 0
}

if ! check_recent_aborting "$SYNC_ERR"; then
    alarm "sync.err 近 10 分钟有 Aborting / pull 失败 (大概率 untracked 文件冲突). 修: cd $REPO && git status, mv untracked 文件出仓"
    exit 1
fi

if ! check_recent_aborting "$VFS_LOG"; then
    alarm "velocity_fast_sync.log 近 10 分钟有 pull --rebase 失败 (大概率 untracked 文件冲突)."
    exit 1
fi

# 全部健康 — 清 flag
rm -f "$HEALTH_FLAG"
exit 0
