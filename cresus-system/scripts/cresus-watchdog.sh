#!/usr/bin/env bash
# cresus-watchdog.sh — Phase 4.F (2026-05-21)
#
# 每分钟检查 live_trader / velocity_fast_sync 是否正常工作.
# 触发条件: ~/cresus-bot/{live,paper}_trades_history.json 超过 STALE_MIN 分钟
#           未更新 → 视为异常.
# 处理:
#   - 1 次失败: 仅记录到 watchdog.log
#   - 连续 RECOVER_THRESHOLD 次失败: 自动 launchctl kickstart 服务
#   - 恢复时: 记录 RECOVERED 状态, 清零计数
#
# 部署:
#   1. 把本脚本复制 / 链到 ~/cresus-bot/cresus-watchdog.sh
#   2. 把 com.cresus.watchdog.plist 放到 ~/Library/LaunchAgents/
#   3. launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cresus.watchdog.plist
#
# 设计原则:
#   - 防误触: 连续 ≥2 次失败才 kickstart (单次失败可能是临时延迟)
#   - 防雪崩: kickstart 后清零计数 (避免无限循环)
#   - 日志精简: 只在状态变化时写, 不每次都写 (60 次/小时不是日志)
#   - 退路: 无 launchctl / 无文件等异常不会让脚本崩

set -euo pipefail

# ===== 配置 =====
HOME_DIR="${HOME}"
LIVE_HISTORY="${HOME_DIR}/cresus-bot/live_trades_history.json"
PAPER_HISTORY="${HOME_DIR}/cresus-bot/paper_trades_history.json"
LOG="${HOME_DIR}/cresus-bot/logs/watchdog.log"
STATE_FILE="${HOME_DIR}/cresus-bot/.watchdog-state"
STALE_MIN=5                  # 文件超 N 分钟未更新视为异常
RECOVER_THRESHOLD=2          # 连续 N 次失败才尝试 kickstart
SERVICES=(
    "com.cresus.live-trader"
    "com.cresus.velocity-fast-sync"
)

mkdir -p "$(dirname "$LOG")"

NOW=$(date "+%Y-%m-%d %H:%M:%S")
UID_NUM=$(id -u)

# ===== 工具函数 =====

# 跨平台 stat: macOS 用 -f, Linux 用 -c
file_mtime() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        echo "0"
        return
    fi
    if [[ "$(uname)" == "Darwin" ]]; then
        stat -f %m "$f" 2>/dev/null || echo "0"
    else
        stat -c %Y "$f" 2>/dev/null || echo "0"
    fi
}

# 返回文件最后修改距今分钟数 (整数, 文件不存在返 99999)
file_age_min() {
    local mtime=$(file_mtime "$1")
    if [[ "$mtime" == "0" ]]; then
        echo "99999"
        return
    fi
    local now=$(date +%s)
    echo $(( (now - mtime) / 60 ))
}

read_state() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

write_state() {
    echo "$1" > "$STATE_FILE"
}

log_line() {
    echo "[$NOW] $1" >> "$LOG"
}

# ===== 主逻辑 =====

LIVE_AGE=$(file_age_min "$LIVE_HISTORY")
PAPER_AGE=$(file_age_min "$PAPER_HISTORY")
FAIL_COUNT=$(read_state)

HEALTHY=1
REASON=""

if (( LIVE_AGE > STALE_MIN )); then
    HEALTHY=0
    REASON="live_trades_history ${LIVE_AGE}min stale"
fi
if (( PAPER_AGE > STALE_MIN )); then
    HEALTHY=0
    REASON="${REASON}${REASON:+ + }paper_trades_history ${PAPER_AGE}min stale"
fi

if (( HEALTHY == 1 )); then
    # 健康路径
    if (( FAIL_COUNT > 0 )); then
        log_line "✓ RECOVERED — live=${LIVE_AGE}min paper=${PAPER_AGE}min (after $FAIL_COUNT failures)"
        write_state "0"
    fi
    # 健康时不记日志, 避免噪音
    exit 0
fi

# 不健康路径
FAIL_COUNT=$((FAIL_COUNT + 1))
write_state "$FAIL_COUNT"
log_line "⚠ UNHEALTHY ($FAIL_COUNT/$RECOVER_THRESHOLD) — $REASON"

if (( FAIL_COUNT >= RECOVER_THRESHOLD )); then
    log_line "🔧 ATTEMPTING RECOVERY — kickstart services"
    for svc in "${SERVICES[@]}"; do
        if launchctl kickstart -k "gui/${UID_NUM}/${svc}" 2>>"$LOG"; then
            log_line "  ✓ kickstarted $svc"
        else
            log_line "  ✗ kickstart $svc FAILED (service not in modern domain?)"
        fi
    done
    # 清零, 给服务时间恢复; 若仍失败, 下一轮重新累计
    write_state "0"
fi
