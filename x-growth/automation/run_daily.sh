#!/usr/bin/env bash
# run_daily.sh · 每天定时跑选题（cron / launchd 调用入口）
#
# 使用方式：
#   chmod +x run_daily.sh
#   bash run_daily.sh          # 手动测试
#   cron / launchd 定时自动调
#
# 日志会追加到 output/run.log（按月滚动，超过 500 行自动截断）

set -euo pipefail

# ── 路径 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/output/run.log"

# ── 确保输出目录存在 ──────────────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/output"

# ── 日志滚动（超 500 行保留末 200 行）────────────────────────────────────────
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 500 ]; then
    tail -200 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

{
    echo "========================================"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始每日选题"
    echo "========================================"

    # ── 依赖检查 ─────────────────────────────────────────────────────────────
    if ! command -v claude &>/dev/null; then
        echo "[error] 找不到 claude 命令，请先安装 Claude Code CLI 并登录。"
        echo "        安装：https://claude.ai/code"
        exit 1
    fi

    if ! python3 -c "import feedparser" &>/dev/null; then
        echo "[info] 安装缺失依赖 feedparser…"
        pip3 install --quiet feedparser
    fi

    # ── 运行脚本 ─────────────────────────────────────────────────────────────
    cd "$SCRIPT_DIR"
    python3 daily_pipeline_local.py

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成"

} >> "$LOG_FILE" 2>&1

# 同时在终端打印最后几行（cron 下这行无效，手动跑时有用）
tail -5 "$LOG_FILE" 2>/dev/null || true
