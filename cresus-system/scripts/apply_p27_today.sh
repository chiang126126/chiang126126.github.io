#!/bin/bash
# 今日全套: P27-B-Ext + P27-X
#
# 同时部署:
#   1. tp1_breakeven_monitor.py (TP1 触发后自动移 SL 到 breakeven)
#   2. com.cresus.tp1-monitor.plist (每 60s)
#   3. volume_velocity_scanner.py (量价共振扫描器)
#   4. com.cresus.velocity-scanner.plist (每 60s)
#   5. dashboard 同步 volume_velocity_alerts.json

set -e

REPO=~/chiang126126.github.io
BOT=~/cresus-bot
LA=~/Library/LaunchAgents

cd "$REPO" && git pull --quiet || true

# ============================================
# 1. P27-B-Ext: TP1 → breakeven SL monitor
# ============================================
echo "=== P27-B-Ext: TP1 → breakeven 自动监控 ==="
mkdir -p "$BOT/scripts" "$BOT/logs"
cp "$REPO/cresus-system/scripts/tp1_breakeven_monitor.py" "$BOT/scripts/"
echo "✓ tp1_breakeven_monitor.py 装载"

cp "$REPO/cresus-system/scripts/com.cresus.tp1-monitor.plist" "$LA/"
launchctl unload "$LA/com.cresus.tp1-monitor.plist" 2>/dev/null || true
launchctl load -w "$LA/com.cresus.tp1-monitor.plist"
launchctl list | grep cresus.tp1-monitor && echo "✓ launchd 启动"

# ============================================
# 2. P27-X: 量能加速度扫描器
# ============================================
echo ""
echo "=== P27-X: 量能加速度早期检测 ==="
cp "$REPO/cresus-system/scripts/volume_velocity_scanner.py" "$BOT/scripts/"
echo "✓ volume_velocity_scanner.py 装载"

# 立刻跑一次试 (验证网络 + 数据)
echo "→ 试跑一次..."
python3 "$BOT/scripts/volume_velocity_scanner.py" 2>&1 | tail -5

cp "$REPO/cresus-system/scripts/com.cresus.velocity-scanner.plist" "$LA/"
launchctl unload "$LA/com.cresus.velocity-scanner.plist" 2>/dev/null || true
launchctl load -w "$LA/com.cresus.velocity-scanner.plist"
launchctl list | grep cresus.velocity-scanner && echo "✓ launchd 启动"

# ============================================
# 3. dashboard 同步 alerts json
# ============================================
echo ""
echo "=== sync_signals.sh 加 volume_velocity_alerts.json 同步 ==="
SYNC=$BOT/scripts/sync_signals.sh
if grep -q volume_velocity_alerts "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含同步行,跳过"
else
    sed -i '' '/precog_radar.json cresus-system/a\
[ -f ~/cresus-bot/volume_velocity_alerts.json ] \&\& cp ~/cresus-bot/volume_velocity_alerts.json cresus-system/dashboard/volume_velocity_alerts.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 1 行同步"
fi

# 立刻同步一次
bash "$SYNC" 2>&1 | tail -2

echo ""
echo "✅ 今日全部 patch 应用完毕"
echo ""
echo "下一步验证:"
echo "  1. tail -f ~/cresus-bot/logs/velocity_scanner.log    # 看到 ⚡ 标的报警"
echo "  2. tail -f ~/cresus-bot/logs/tp1_monitor.log         # 看到 BE SL 调整"
echo "  3. dashboard 刷新 → ⚡ 量能加速度雷达 panel 出现"
