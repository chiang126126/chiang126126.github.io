#!/usr/bin/env bash
# 本地运行 MP500 机器人（从你自己的网络连币安 testnet，绕开 GitHub 的地区封锁）。
# 用法：bash run_local.sh   （或加进 crontab 每小时跑）
set -e
cd "$(dirname "$0")"                       # 进入 mp500-bot 目录

# 读 .env（MODE / LLM_API_KEY / BINANCE_TESTNET_* / DATA_REPO）
set -a; [ -f .env ] && . ./.env; set +a

if [ -n "$DATA_REPO" ]; then
  # 把数据写进你 clone 的独立仓库，跑完推回去 → 看板自动显示
  git -C "$DATA_REPO" pull -q --rebase 2>/dev/null || true
  export DATA_DIR="$DATA_REPO/data"
  mkdir -p "$DATA_DIR"
  python3 bot.py
  git -C "$DATA_REPO" add data/
  if git -C "$DATA_REPO" commit -q -m "local bot run $(date -u +%FT%TZ)"; then
    git -C "$DATA_REPO" push -q && echo "✅ 已推送数据，看板将更新"
  else
    echo "（无数据变化）"
  fi
else
  # 没设 DATA_REPO 就只本地跑、本地存（不推看板）
  export DATA_DIR="${DATA_DIR:-./data}"
  mkdir -p "$DATA_DIR"
  python3 bot.py
  echo "✅ 本地完成，数据在 $DATA_DIR（未推送看板）"
fi
