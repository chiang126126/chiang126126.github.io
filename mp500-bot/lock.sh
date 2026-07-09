#!/usr/bin/env bash
# mkdir 原子锁（macOS 无 flock）。run_local.sh 与 run_guardian.sh 共用，
# 保证小时主循环与分钟级守护层绝不并发读写同一份数据/同一个 git 仓库。
# 用法: source lock.sh && acquire_lock <等待秒数> || exit 0
LOCKDIR="/tmp/mp500-bot.lockdir"

acquire_lock() {
  local wait_s="${1:-60}" i=0
  # 清理陈旧锁（持有者异常退出未清理，超过 10 分钟视为死锁遗留）
  # stat 语法：GNU/Linux 用 -c %Y；BSD/macOS 用 -f %m（注意 Linux 的 -f 是查文件系统，不能先试）
  if [ -d "$LOCKDIR" ]; then
    local mt now
    mt=$(stat -c %Y "$LOCKDIR" 2>/dev/null)
    [ -z "$mt" ] && mt=$(stat -f %m "$LOCKDIR" 2>/dev/null)
    [ -z "$mt" ] && mt=$(date +%s)
    now=$(date +%s)
    if [ $((now - mt)) -gt 600 ]; then rmdir "$LOCKDIR" 2>/dev/null || true; fi
  fi
  until mkdir "$LOCKDIR" 2>/dev/null; do
    i=$((i + 2))
    [ "$i" -ge "$wait_s" ] && return 1
    sleep 2
  done
  trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT
  return 0
}
