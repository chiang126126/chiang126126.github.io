#!/bin/bash
# velocity_fast_sync.sh — 高频同步 4 个 velocity 核心 JSON 文件
#
# 互补关系:
#   - sync_signals.sh        10min 周期, 全量同步 (signals/pnl/regime + velocity)
#   - 本脚本 (fast-sync)     2min 周期, 仅 4 个 velocity 核心文件
#   - git_push_retry.sh      60s 周期, 兜底 retry 失败的 push
#
# 安全设计:
#   - 自身并发锁 (防 2min cron 上轮没跑完下轮又起)
#   - 只在文件真有差异时 commit (避免空 commit)
#   - git pull --rebase --autostash 自动处理与 sync_signals.sh 的可能 race
#   - push 失败 3 次重试 (3s/6s 退避), 重试前重新 pull rebase
#   - 失败也只记日志, 不阻塞 launchd

set -u

REPO="$HOME/chiang126126.github.io"
BOT="$HOME/cresus-bot"
LOG="$BOT/logs/velocity_fast_sync.log"
LOCK="$BOT/.velocity_fast_sync.lock"

# 仅同步这 4 个 velocity 核心文件 (其他大文件交 sync_signals.sh 10min 周期)
FILES=(
    "volume_velocity_alerts.json"
    "velocity_winrate.json"
    "paper_trades_history.json"
    "paper_shadow_history.json"
)

# ====== 1. 自身并发锁 (orphan lock > 5min 强制清除) ======
if [ -f "$LOCK" ]; then
    lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -lt 300 ]; then
        # 上一轮还在跑, 跳过本轮
        exit 0
    fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# ====== 2. cd 到 repo ======
cd "$REPO" 2>/dev/null || {
    echo "$(date '+%FT%T') ✗ repo dir 不存在: $REPO" >> "$LOG"
    exit 1
}

# ====== 3. 早期检查: 是否真有文件差异 (避免无意义流程) ======
differ=0
for f in "${FILES[@]}"; do
    if [ -f "$BOT/$f" ]; then
        # cmp -s: 静默比较, 不同返回非 0
        if ! cmp -s "$BOT/$f" "cresus-system/dashboard/$f" 2>/dev/null; then
            differ=$((differ + 1))
        fi
    fi
done

if [ "$differ" -eq 0 ]; then
    # 文件完全一致, 静默退出 (绝大多数 case)
    exit 0
fi

# ====== 4. pull --rebase 与远端同步 (解决与 sync_signals.sh 可能 race) ======
# --autostash: 临时 stash 工作树未提交改动, rebase 后恢复
# --quiet:    无 output 干扰 log
if ! git pull --quiet --rebase --autostash 2>>"$LOG"; then
    echo "$(date '+%FT%T') ✗ pull --rebase 失败, 跳过本轮" >> "$LOG"
    exit 1
fi

# ====== 5. 复制 BOT → dashboard ======
for f in "${FILES[@]}"; do
    if [ -f "$BOT/$f" ]; then
        cp "$BOT/$f" "cresus-system/dashboard/$f"
    fi
done

# ====== 6. 精准 add (只 stage 这 4 个文件, 不污染其他) ======
for f in "${FILES[@]}"; do
    git add "cresus-system/dashboard/$f" 2>/dev/null || true
done

# ====== 7. 检查 staged 是否真有 diff (有时 cp 后内容没真变化) ======
if git diff --staged --quiet; then
    # 文件 mtime 变了但 content 同, git 不视为变化, 退出
    exit 0
fi

# ====== 8. commit ======
if ! git commit -m "data: velocity fast-sync ($(date '+%Y-%m-%d %H:%M:%S'))" --quiet 2>>"$LOG"; then
    echo "$(date '+%FT%T') ✗ commit 失败" >> "$LOG"
    exit 1
fi

# ====== 9. push with 3-retry 退避 ======
# 重试时先 pull (sync_signals.sh 可能刚抢推了)
for attempt in 1 2 3; do
    if git push origin main 2>>"$LOG"; then
        echo "$(date '+%FT%T') ✓ fast-sync 第 $attempt 次 push 成功 ($differ 文件)" >> "$LOG"
        exit 0
    fi
    if [ "$attempt" -lt 3 ]; then
        sleep $((attempt * 3))   # 3s, 6s
        git pull --quiet --rebase --autostash 2>>"$LOG" || true
    fi
done

echo "$(date '+%FT%T') ✗ fast-sync push 3 次均失败" >> "$LOG"
exit 1
