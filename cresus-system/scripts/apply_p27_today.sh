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
# Phase 3: launchd 用 wrapper, 让它能读 ~/.cresus/env.sh 里的 TG token
cp "$REPO/cresus-system/scripts/run_velocity_scanner.sh" "$BOT/scripts/"
chmod +x "$BOT/scripts/run_velocity_scanner.sh"
echo "✓ volume_velocity_scanner.py + run_velocity_scanner.sh 装载"

# 立刻跑一次试 (验证网络 + 数据)
echo "→ 试跑一次..."
bash "$BOT/scripts/run_velocity_scanner.sh" 2>&1 | tail -5

cp "$REPO/cresus-system/scripts/com.cresus.velocity-scanner.plist" "$LA/"
launchctl unload "$LA/com.cresus.velocity-scanner.plist" 2>/dev/null || true
launchctl load -w "$LA/com.cresus.velocity-scanner.plist"
launchctl list | grep cresus.velocity-scanner && echo "✓ launchd 启动"

# Phase 3: 提示 Telegram 配置状态
if [ -f "$HOME/.cresus/env.sh" ] && grep -q "TELEGRAM_BOT_TOKEN" "$HOME/.cresus/env.sh" 2>/dev/null; then
    echo "✓ Telegram push 配置已检测到 (~/.cresus/env.sh)"
else
    echo "ⓘ Telegram push 未配置 — 创建 ~/.cresus/env.sh 写入 TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 启用"
fi

# ============================================
# 2.5 precog_radar.py 更新 (Phase 2 胜率追踪)
# !! 仅 cp .py, 绝不 cp .plist !!
# 原因: ~/Library/LaunchAgents/com.cresus.precog-radar.plist 含真实 DEEPSEEK_API_KEY
# 若用 repo 模板覆盖会把 key 抹成 YOUR_DEEPSEEK_API_KEY_HERE, AI 立即降级
# ============================================
echo ""
echo "=== precog_radar.py 更新 (保留 plist 不动) ==="
if [ -f "$REPO/cresus-system/scripts/precog_radar.py" ]; then
    cp "$REPO/cresus-system/scripts/precog_radar.py" "$BOT/scripts/"
    echo "✓ precog_radar.py 装载"
    # 重启 launchd 让新 .py 被读取 (不改 plist, 保留 API key)
    if [ -f "$LA/com.cresus.precog-radar.plist" ]; then
        launchctl unload "$LA/com.cresus.precog-radar.plist" 2>/dev/null || true
        launchctl load -w "$LA/com.cresus.precog-radar.plist"
        # 校验 plist 里的 key 没被擦
        if grep -q "YOUR_DEEPSEEK_API_KEY_HERE" "$LA/com.cresus.precog-radar.plist"; then
            echo "⚠️  WARNING: plist 里 API key 是占位符,AI 不会真跑"
            echo "    修复: 编辑 $LA/com.cresus.precog-radar.plist 把 YOUR_DEEPSEEK_API_KEY_HERE 换成真 key"
        else
            echo "✓ precog launchd 重启 (API key 保留)"
        fi
    else
        echo "⚠️  $LA/com.cresus.precog-radar.plist 不存在,首次安装请手动配置 API key"
    fi
else
    echo "⊘ 仓库无 precog_radar.py,跳过"
fi

# ============================================
# 3. dashboard 同步 alerts json
# ============================================
echo ""
echo "=== sync_signals.sh 加 volume_velocity_alerts.json 同步 ==="
SYNC=$BOT/scripts/sync_signals.sh
if grep -q volume_velocity_alerts "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含 alerts 同步行,跳过"
else
    sed -i '' '/precog_radar.json cresus-system/a\
[ -f ~/cresus-bot/volume_velocity_alerts.json ] \&\& cp ~/cresus-bot/volume_velocity_alerts.json cresus-system/dashboard/volume_velocity_alerts.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 alerts 同步"
fi

# Phase 2: 胜率追踪 winrate.json
if grep -q velocity_winrate "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含 velocity winrate 同步行,跳过"
else
    sed -i '' '/volume_velocity_alerts.json cresus-system/a\
[ -f ~/cresus-bot/velocity_winrate.json ] \&\& cp ~/cresus-bot/velocity_winrate.json cresus-system/dashboard/velocity_winrate.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 velocity winrate 同步"
fi

# Phase 2: precog A/B 评估用 winrate
if grep -q precog_winrate "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含 precog winrate 同步行,跳过"
else
    sed -i '' '/velocity_winrate.json cresus-system/a\
[ -f ~/cresus-bot/precog_winrate.json ] \&\& cp ~/cresus-bot/precog_winrate.json cresus-system/dashboard/precog_winrate.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 precog winrate 同步"
fi

# Phase 4: 模拟仓 history
if grep -q paper_trades_history "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含 paper trades 同步行,跳过"
else
    sed -i '' '/precog_winrate.json cresus-system/a\
[ -f ~/cresus-bot/paper_trades_history.json ] \&\& cp ~/cresus-bot/paper_trades_history.json cresus-system/dashboard/paper_trades_history.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 paper trades 同步"
fi

# Phase 4 Shadow: premium 影子追踪 history
if grep -q paper_shadow_history "$SYNC" 2>/dev/null; then
    echo "✓ sync_signals.sh 已含 paper shadow 同步行,跳过"
else
    sed -i '' '/paper_trades_history.json cresus-system/a\
[ -f ~/cresus-bot/paper_shadow_history.json ] \&\& cp ~/cresus-bot/paper_shadow_history.json cresus-system/dashboard/paper_shadow_history.json
' "$SYNC"
    echo "✓ sync_signals.sh 加 paper shadow 同步"
fi

# 立刻同步一次
bash "$SYNC" 2>&1 | tail -2

echo ""
echo "✅ 今日全部 patch 应用完毕"

# ============================================
# 4. Git push retry watcher (保险丝 — 防 sync curl 56 网络瞬断卡 commit)
# ============================================
echo ""
echo "=== Git push retry watcher (1min 安全网) ==="
cp "$REPO/cresus-system/scripts/git_push_retry.sh" "$BOT/scripts/"
chmod +x "$BOT/scripts/git_push_retry.sh"
echo "✓ git_push_retry.sh 装载"

cp "$REPO/cresus-system/scripts/com.cresus.push-retry.plist" "$LA/"
launchctl unload "$LA/com.cresus.push-retry.plist" 2>/dev/null || true
launchctl load -w "$LA/com.cresus.push-retry.plist"
launchctl list | grep cresus.push-retry && echo "✓ push-retry launchd 启动 (60s 周期)"

# ============================================
# 5. Velocity fast-sync (2min 高频同步钻石模拟仓相关 4 个 JSON)
# ============================================
echo ""
echo "=== Velocity fast-sync (2min 高频) ==="
cp "$REPO/cresus-system/scripts/velocity_fast_sync.sh" "$BOT/scripts/"
chmod +x "$BOT/scripts/velocity_fast_sync.sh"
echo "✓ velocity_fast_sync.sh 装载"

cp "$REPO/cresus-system/scripts/com.cresus.velocity-fast-sync.plist" "$LA/"
launchctl unload "$LA/com.cresus.velocity-fast-sync.plist" 2>/dev/null || true
launchctl load -w "$LA/com.cresus.velocity-fast-sync.plist"
launchctl list | grep cresus.velocity-fast-sync && echo "✓ velocity-fast-sync launchd 启动 (120s 周期)"

echo ""
echo "下一步验证:"
echo "  1. tail -f ~/cresus-bot/logs/velocity_scanner.log     # 看到 ⚡ 标的报警"
echo "  2. tail -f ~/cresus-bot/logs/tp1_monitor.log          # 看到 BE SL 调整"
echo "  3. tail -f ~/cresus-bot/logs/git_push_retry.log       # 网络瞬断时自动重试 push"
echo "  4. dashboard 刷新 → ⚡ 量能加速度雷达 panel 出现"
