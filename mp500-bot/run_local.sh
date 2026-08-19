#!/usr/bin/env bash
# 本地运行 MP500 机器人（从你自己的网络连币安 testnet，绕开 GitHub 的地区封锁）。
# 用法：bash run_local.sh   （或加进 crontab 每小时跑）
set -e

# cron 的 PATH 极简（只有 /usr/bin:/bin），会找不到 Homebrew 装的 python3/git，
# 导致"手动跑正常、cron 跑失败"。这里补上常见路径，确保两种方式一致。
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONWARNINGS="ignore::Warning:urllib3"   # 静音 Mac 自带 Python 的 LibreSSL 无害警告

cd "$(dirname "$0")"                       # 进入 mp500-bot 目录
echo "──────── $(date '+%F %T %Z') 开始运行 ────────"
echo "python3: $(command -v python3 || echo '未找到!')  git: $(command -v git || echo '未找到!')"

# 与分钟级守护层(run_guardian.sh)互斥：小时主循环优先级高，最多等5分钟
source ./lock.sh
acquire_lock 300 || { echo "[warn] 等锁超时，跳过本轮"; exit 0; }

# 读 .env（MODE / LLM_API_KEY / BINANCE_TESTNET_* / DATA_REPO）
set -a; [ -f .env ] && . ./.env; set +a

if [ -n "$DATA_REPO" ]; then
  # 把数据写进你 clone 的独立仓库，跑完推回去 → 看板自动显示
  git -C "$DATA_REPO" pull -q --rebase 2>/dev/null || true
  export DATA_DIR="$DATA_REPO/data"
  mkdir -p "$DATA_DIR"
  python3 bot.py
  python3 sim.py || echo "[sim] 模拟舱本轮失败（不影响主循环）"
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
  python3 sim.py || echo "[sim] 模拟舱本轮失败（不影响主循环）"
  echo "✅ 本地完成，数据在 $DATA_DIR（未推送看板）"
fi
