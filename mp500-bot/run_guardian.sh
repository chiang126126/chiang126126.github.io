#!/usr/bin/env bash
# 守护层（每3分钟）：只管已有持仓的 止损/止盈/保本/跟踪/超时，绝不开新仓。
# crontab: */3 * * * * /bin/bash /Users/hong/mp500/mp500-bot/run_guardian.sh >> $HOME/mp500-guardian.log 2>&1
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONWARNINGS="ignore::Warning:urllib3"   # 静音 Mac 自带 Python 的 LibreSSL 无害警告
cd "$(dirname "$0")"

source ./lock.sh
acquire_lock 50 || { echo "[guardian] $(date '+%F %T') 等锁超时（小时主循环在跑），跳过本轮"; exit 0; }

set -a; [ -f .env ] && . ./.env; set +a

if [ -n "$DATA_REPO" ]; then
  git -C "$DATA_REPO" pull -q --rebase 2>/dev/null || true
  export DATA_DIR="$DATA_REPO/data"
fi

set +e
python3 guardian.py
rc=$?
set -e

if [ "$rc" = "10" ]; then
  echo "[guardian] $(date '+%F %T') 状态有变（平仓/移损）"
  if [ -n "$DATA_REPO" ]; then
    git -C "$DATA_REPO" add data/
    git -C "$DATA_REPO" commit -q -m "guardian $(date -u +%FT%TZ)" 2>/dev/null && \
      git -C "$DATA_REPO" push -q 2>/dev/null && echo "[guardian] ✅ 已推送，看板将更新" || true
  fi
elif [ "$rc" != "0" ]; then
  echo "[guardian] $(date '+%F %T') 异常退出码 $rc"
fi
exit 0
