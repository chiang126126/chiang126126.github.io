#!/bin/bash
# git_push_retry.sh — 保险丝: 每分钟检查本地 main 是否领先 origin, 领先就 retry push.
# 解决 sync_signals.sh 的 curl 56 网络瞬断后 commit 卡本地的问题.
#
# 逻辑:
#   1. 只检查/推送, 不 add/commit (避免跟 sync_signals.sh 冲突)
#   2. 如果本地领先 → 3 次重试 (5s/10s/15s 退避)
#   3. 成功/失败都写 log
#
# 部署: 由 com.cresus.push-retry.plist 每 60s 调用

set -u

REPO=~/chiang126126.github.io
LOG=~/cresus-bot/logs/git_push_retry.log

cd "$REPO" 2>/dev/null || { echo "$(date '+%FT%T') ✗ repo dir 不存在: $REPO" >> "$LOG"; exit 1; }

# 静默 fetch origin/main 最新指针 (不动本地工作树)
git fetch origin main --quiet 2>/dev/null || {
    # 网络断也别误报, 下次再试
    exit 0
}

ahead=$(git rev-list --count origin/main..HEAD 2>/dev/null)
if [ -z "$ahead" ] || [ "$ahead" = "0" ]; then
    exit 0  # 无未推 commit, 沉默退出 (绝大多数 case)
fi

# 有未推 commit → 3 次重试退避
for attempt in 1 2 3; do
    if git push origin main 2>>"$LOG"; then
        echo "$(date '+%FT%T') ✓ push 成功 第 $attempt 次尝试 ($ahead 条 commit)" >> "$LOG"
        exit 0
    fi
    [ "$attempt" -lt 3 ] && sleep $((attempt * 5))
done

echo "$(date '+%FT%T') ✗ push 3 次均失败 ($ahead 条 commit 仍卡本地)" >> "$LOG"
exit 1
