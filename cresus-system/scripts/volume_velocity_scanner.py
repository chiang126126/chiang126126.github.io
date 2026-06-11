"""volume_velocity_scanner.py — P27-X: 量能加速度早期检测器

核心目标: 在 SUI 类标的"刚启动"瞬间捕捉,比 OI 累积/MA 偏离等滞后指标早 5-15 分钟.

触发条件 (双因子共振):
  1. 1m 量能 > 30m 均量 × 5 (量能爆发)
  2. 1m 价格 同向变化 ≥ 0.5% (价格已跟随)

输出:
  ~/cresus-bot/volume_velocity_alerts.json     最近 60min 内的爆发标的
  Discord 通知 (如配置 DISCORD_VELOCITY_WEBHOOK)

设计:
  - 扫描覆盖: Binance 永续 Top 200 by 24h volume
  - 数据源: Binance 1m kline (公开免费, 无 API key)
  - 频率: 每 60s 一轮 (太密会撞 rate limit, 太松会错过启动)
  - 去重: 同标的 30 min 内不重复报警

为什么是这两个条件?
  - 单看量能爆发会被假突破误导 (大单挂单/抽单)
  - 单看价格变化会被噪音误导 (1m 内 0.5% 是常态)
  - 量价双共振 = 真实资金入场 (难伪造, 因为要砸真钱)

依赖: 纯 stdlib (urllib + json)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean
from typing import List, Optional

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_OUTPUT  = Path.home() / "cresus-bot" / "volume_velocity_alerts.json"
DEDUP_STATE     = Path.home() / "cresus-bot" / ".velocity_dedup.json"
LOG_FILE        = Path.home() / "cresus-bot" / "logs" / "volume_velocity_scanner.log"

# ---- Phase 2: 历史胜率追踪 ----
OUTCOMES_STATE   = Path.home() / "cresus-bot" / ".velocity_outcomes.json"   # 本地状态,不 push
WINRATE_OUTPUT   = Path.home() / "cresus-bot" / "velocity_winrate.json"     # push 给看板
OUTCOME_STAGES_MIN = [30, 60, 240]    # 30m / 1h / 4h
OUTCOMES_RETENTION_DAYS = 60          # 保留 60 天历史
OUTCOME_WIN_THRESHOLD_PCT = 0.5       # outcome_pct ≥ 0.5% 视为"赢" (net of fees)
WINRATE_MIN_SAMPLES = 5               # 样本数 ≥ 此值才显示胜率

# ---- Phase 3: Telegram push (绕过看板延迟链, 直接推手机) ----
TG_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TG_COOLDOWN_STATE = Path.home() / "cresus-bot" / ".velocity_tg_cooldown.json"
TG_COOLDOWN_MIN  = 30                 # 同 symbol 30min 只推 1 次
TG_MIN_INTENSITY = 2                  # intensity ≥ 2 才推 (过滤弱信号噪声)

# ---- Phase 3b: Email VIP 通道 (仅钻石信号, 避免被 TG 噪声淹没) ----
EMAIL_SMTP_HOST  = os.environ.get("EMAIL_SMTP_HOST", "").strip()    # 如 smtp.gmail.com
EMAIL_SMTP_PORT  = int(os.environ.get("EMAIL_SMTP_PORT", "587") or "587")
EMAIL_USERNAME   = os.environ.get("EMAIL_USERNAME", "").strip()
EMAIL_PASSWORD   = os.environ.get("EMAIL_PASSWORD", "").strip()    # Gmail app password (非常规密码)
EMAIL_FROM       = os.environ.get("EMAIL_FROM", "").strip() or EMAIL_USERNAME
EMAIL_TO         = os.environ.get("EMAIL_TO", "").strip() or EMAIL_USERNAME
EMAIL_COOLDOWN_STATE = Path.home() / "cresus-bot" / ".velocity_email_cooldown.json"
EMAIL_COOLDOWN_MIN   = 60             # 同 symbol+direction 1 小时只发 1 封 (比 TG 严, 避免邮件刷屏)
EMAIL_MIN_SCORE      = 6              # 仅 score ≥6 才发邮件 (基于 N=42 数据: score 6+ 100% 胜率 avg+9.6%; score 5 ≈ 0%)

# ---- Phase 4: 自动模拟仓 (仅钻石信号开仓, 跟踪真实收益曲线) ----
PAPER_STATE       = Path.home() / "cresus-bot" / ".paper_trades.json"       # 本地全量 state
PAPER_HISTORY     = Path.home() / "cresus-bot" / "paper_trades_history.json" # 推给看板
PAPER_AUTO_CLOSE_HOURS = 4            # 4 小时未触发 SL/TP 自动平仓 (跟 OUTCOME_STAGES 4h 对齐)
PAPER_SYMBOL_COOLDOWN_MIN = 30        # Phase 1.1: 同 symbol 任意 exit 后冷却 (任意 close_reason, 防信号抖动 + 长尾反复亏损)
PAPER_MAX_ATR_PCT = 3.0               # Phase 5.A (5/27): 2.0→3.0 — 数据 (1410笔):
                                       # ATR ≥2% n=26 avg +$7.14 (最高 EV 区!), 旧规则错杀.
                                       # 保留 ATR ≥3% 极端波动保护 (n=极少, 不可控).
PAPER_CONSEC_SL_TRIGGER = 2           # Phase 1.2: 同 symbol 连续 SL 次数阈值 (审计: QUSDT 5/5 SL, ATA 4/4 SL)
PAPER_CONSEC_SL_WINDOW_HOURS = 4      # Phase 1.2: 连续 SL 检测窗口 (4h 内)
PAPER_CONSEC_SL_COOLDOWN_HOURS = 4    # Phase 1.2: 触发后冷却时长

# ==========================================
# Phase 6.B (2026-06-03) — 实战亏损反馈过滤 + 浮盈保护
# ==========================================
# 触发原因: 用户 6/3 实盘看到 DRAMUSDT/DYDX/BASEDUSDT/MONUSDT 等多笔 paper Phase A 止损,
# 共同模式:
#   - R 极小 (DRAMUSDT R=0.23%, 1 tick 就触发)
#   - 历史 30m 胜率低 (BASEDUSDT 12% N=32, DYDX 27% N=15)
#   - DYDX 类: 高水位接近 TP1 但回拉到 SL = "盈利后转亏"
#
# 当前代码只用历史胜率 *加分* (+2 或 +3), 不用作 *过滤*. 致命漏洞.
# 当前 SL 公式 = 1.0×ATR×vol_mult, 没有绝对下限, 极小 ATR 信号 R 不足以承受 wick.
# 当前 Phase A 阶段 SL 永不动直到 TP1, 浮盈接近 TP1 后回拉直接吃 SL.
#
# Phase 6.B 3 个修复 (Tier 1):
#   A. 历史 30m 胜率 filter: < 25% AND N >= 20 → reject (BASED 类显著负 EV 信号)
#   B. SL 绝对下限: SL distance < 0.3% → reject (DRAMUSDT 类微 R 信号)
#   C. Breakeven shift: Phase A 浮盈达 1.0R 时 SL 移到 entry (DYDX 类盈利保护)
PAPER_MIN_HIST_WINRATE = 0.25         # 6.B-A: 历史 30m 胜率下限 (低于此 + N 足够 → reject)
PAPER_MIN_HIST_SAMPLE = 20            # 6.B-A: 历史样本下限 (N < 此值不应用 winrate filter, 防小样本误杀)
PAPER_MIN_SL_DISTANCE_PCT = 0.3       # 6.B-B: SL 距入场最小 % (低于此 = R 太窄, reject)
PAPER_BREAKEVEN_PROFIT_R = 1.0        # 6.B-C: Phase A 浮盈达 N×R 时 SL 移到 entry

# ==========================================
# Phase 6.C (2026-06-04) — 0.8R 中间保护 + Funding 方向感知评分
# ==========================================
# 用户审计 Phase 6.B 后提议: 在 1.0R BE 之前加 0.8R 中间保护 (更早锁住浮盈);
# Funding 评分从 "abs(funding) >= 0.3 → +2 无方向区分" 改成方向感知 (追拥
# 挤方向 -2, fade 拥挤方向 +2, 因为 funding 反映情绪拥挤).
#
# 0.8R 中间保护实现: 浮盈达 0.8R → SL 移到 entry - 0.2R (LONG) / entry + 0.2R
# (SHORT). 即最差仍亏 0.2R, 比 BE shift (1.0R 触发) 更早, 但保留一些波动空间.
# 用 _profit_milestone 字段记录已触发的最高里程碑 (0.8 / 1.0), 保证只前进
# 不后退 (避免回拉到 0.8R 时再次"激活"已经过的 milestone).
PAPER_PROFIT_PROTECT_R = 0.8          # 6.C-A: 浮盈 0.8R → SL 移到 entry ± 0.2R
PAPER_PROTECT_BUFFER_R = 0.2          # 6.C-A: 0.8R milestone 触发后 SL 离 entry 的 buffer (0.2R = 仍允许小亏)
PAPER_FUNDING_DIRECTION_BIAS = True   # 6.C-B: True = Funding 评分方向感知, False = 旧 abs 行为
PAPER_MILESTONE_EPSILON = 1e-9        # 6.C-A: 浮点容差 (避免 100-99.2=0.7999... 触发不到 0.8R)
PAPER_RECENT_LIMIT = 0                # 已废弃 (改为全量发布以支持任意日期复盘, N=1000 时 ~150KB 也可接受)
PAPER_MIN_TIER     = "diamond"        # 只对钻石信号自动开仓 (高质量 only)
# 模拟仓金额: 总账户 $2000, 每笔分配 $400 (20%), 最多并发 5 笔
# 关仓后 realized P&L 回到账户余额; 已分配资金 = Σ open 仓的 notional_usdt
# 可用资金 = 余额 - 已分配; 若 < notional 则新钻石信号跳过 (资金不足)
PAPER_STARTING_CAPITAL_USDT   = 2000.0  # 起始账户余额 (整个仓总额)
PAPER_NOTIONAL_PER_TRADE_USDT = 400.0   # 每笔交易分配 ($2000 × 20%, 最多并发 5)

# Phase 5.A (5/27) + 5.K (6/1): Conviction score 分档仓位 (与 live 同步):
#   score 5 (92%): paper EV +$0.92, 实盘减摩擦 $2-3 后净 EV ≈ 0 → $200 (5.K 减半)
#   score 6-7 (7%): paper EV +$4.50/笔 (5×), 摩擦后净 $2-6 → $800 (5.A-restore 翻倍)
#   score 8+ (0.5%): n=7 累计 -$118 反向证据 → $200 (Phase 5.A 原方案)
# 字段缺 → 退路到 PAPER_NOTIONAL_PER_TRADE_USDT 基准.
#
# Phase 5.A-restore + 5.K (6/1): 解除 5/28 hotfix.
#   MAX_QTY chunking + 截断 已生效 (binance_client.py Phase 5.A-fix), 不再有
#   -4005 死循环风险. 数据驱动 (5/31): live 净亏来自 score 5 摩擦, 而非 score 6-7.
PAPER_NOTIONAL_BY_SCORE = {
    5:   200.0,   # Phase 5.K: 低 EV 减半
    6:   400.0,   # Phase 5.K-adjust (6/1): 撤回 5.A-restore 的 800
                  # 5/31+6/1 实盘 6 笔全亏 avg -$5.83 矛盾历史 +$4.34
    7:   800.0,   # Phase 5.A-restore: 高 EV 翻倍
    8:   200.0,
    9:   200.0,
    10:  200.0,
}


# ============================================================================
# Phase 6.A (2026-06-03) — Paper-Live 对齐 (mainnet pilot 同步)
# ============================================================================
# 当 CRESUS_MODE=mainnet_pilot 启用时, paper engine 同步 live 的资金 / 仓位
# 配置, 这样 paper PnL 和 mainnet PnL 是 apples-to-apples 可比.
#
# 启用方式 (跟 live_trader.py 同款 env var):
#   CRESUS_MODE=mainnet_pilot
#   CRESUS_PILOT_CAPITAL=600
#
# 不启用 (default testnet) → 保留原 $2000 / {200/400/800/200} 配置.
#
# 注意: paper engine 不调真钱, 仅同步 sizing + 起始资金, 不需要 ~/.allow-live.
_PAPER_CRESUS_MODE = os.environ.get('CRESUS_MODE', 'testnet').strip().lower()
try:
    _PAPER_PILOT_CAPITAL = float(os.environ.get('CRESUS_PILOT_CAPITAL', '500') or 500)
except (TypeError, ValueError):
    _PAPER_PILOT_CAPITAL = 500.0

if _PAPER_CRESUS_MODE == 'mainnet_pilot':
    # 跟 live_trader.py mainnet_pilot tier 一致 (Phase 6.G G1: 同步减 33% 防御性减仓).
    if _PAPER_PILOT_CAPITAL <= 250:
        PAPER_NOTIONAL_BY_SCORE = {5: 50, 6: 65, 7: 100, 8: 50, 9: 50, 10: 50}
        PAPER_NOTIONAL_PER_TRADE_USDT = 50.0
    elif _PAPER_PILOT_CAPITAL <= 600:
        # Phase 6.G G1: 原 {150, 200, 300, ...} → ≈ × 0.67 → {100, 130, 200, ...}
        PAPER_NOTIONAL_BY_SCORE = {5: 100, 6: 130, 7: 200, 8: 100, 9: 100, 10: 100}
        PAPER_NOTIONAL_PER_TRADE_USDT = 100.0
    elif _PAPER_PILOT_CAPITAL <= 1200:
        # 中间档 (live_trader 同款)
        PAPER_NOTIONAL_BY_SCORE = {5: 100, 6: 130, 7: 200, 8: 100, 9: 100, 10: 100}
        PAPER_NOTIONAL_PER_TRADE_USDT = 100.0
    else:
        PAPER_NOTIONAL_BY_SCORE = {5: 200, 6: 265, 7: 400, 8: 200, 9: 200, 10: 200}
        PAPER_NOTIONAL_PER_TRADE_USDT = 200.0

    # paper 起始资金跟 live pilot 一致 → PnL 可直接对比
    PAPER_STARTING_CAPITAL_USDT = _PAPER_PILOT_CAPITAL


def _notional_for_score(score) -> float:
    """按 conviction score 返回分配 notional. 字段异常退路到基准."""
    try:
        s = int(score)
    except (TypeError, ValueError):
        return PAPER_NOTIONAL_PER_TRADE_USDT
    return PAPER_NOTIONAL_BY_SCORE.get(s, PAPER_NOTIONAL_PER_TRADE_USDT)


def _initial_r_distance(t: dict, entry: float) -> float:
    """Phase 6.C: 计算 trade 的原始 1R 距离 (entry-to-initial-SL).

    Robust to SL shifts (Phase 6.B-C breakeven 后 t["sl"] = entry, 距离 = 0
    无法用作 R 计算). 优先级:
        1. t["initial_r"]    新 trade 开仓时即记录 (Phase 6.C 起)
        2. abs(entry - tp1) / 1.5  通过 TP1 反推 (TP1 永远 = 1.5R, 不变)
        3. abs(entry - sl)   最后退路 (仅 SL 未移过时正确)

    Returns: 1R 距离 (绝对值, 同价格单位). 异常返 0.0 (caller 应 guard).
    """
    if t.get("initial_r"):
        try:
            v = float(t["initial_r"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    tp1 = t.get("tp1")
    if tp1 is not None and entry > 0:
        try:
            tp1_f = float(tp1)
            r = abs(entry - tp1_f) / 1.5
            if r > 0:
                return r
        except (TypeError, ValueError):
            pass
    sl = t.get("sl")
    if sl is not None:
        try:
            return abs(entry - float(sl))
        except (TypeError, ValueError):
            pass
    return 0.0


def _use_tp1_partial_close(paper_id: str, mode: Optional[str] = None) -> bool:
    """Phase 5.C TP1 部分平仓 A/B 分组判定.

    mode='off':    永不分组 (维持满仓走 trailing)
    mode='always': 永远部分平仓
    mode='ab':     MD5 hash 50/50 (B 组启用部分平仓)
    Returns True 表示该 trade 触 TP1 时应锁 50% 利润.
    用 MD5 而非 hash() — 后者跨进程值不稳 (PYTHONHASHSEED).
    """
    if mode is None:
        mode = PAPER_TP1_PARTIAL_CLOSE_MODE
    if mode == "off":
        return False
    if mode == "always":
        return True
    if mode == "ab":
        if not paper_id:
            return False
        h = int(hashlib.md5(paper_id.encode("utf-8")).hexdigest(), 16)
        return (h % 2) == 1   # 一半 B 组启用
    return False  # 未知 mode 安全退路


def _apply_tp1_partial_close(t: dict, cur: float, entry: float, is_long: bool) -> None:
    """Phase 5.C: 触 TP1 时若该 trade 属 B 组, 锁 50% 利润 + 剩 50% 继续走 trailing.

    操作 (mutate t in place):
      - 计算锁定 USDT 利润 (50% 仓位 × (gross_pct - 全 RT fee)).
      - notional_usdt 减半 → 后续 Phase B/C 的 unrealized 和 close PnL 用剩 50%.
      - tp1_locked_pnl_usdt 字段保存锁定金额, 终账时加回.
      - tp1_partial_closed 标记便于审计.

    A 组 (不分组) 此函数直接 return, 不改 trade.
    """
    paper_id = t.get("id", "")
    if not _use_tp1_partial_close(paper_id):
        t["tp1_partial_closed"] = False
        return
    notional_full = float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
    half_notional = notional_full / 2.0
    # gross %: LONG = (cur - entry) / entry; SHORT 取反向
    raw_pct = (cur - entry) / entry * 100
    gross_pct = raw_pct if is_long else -raw_pct
    # 锁定半仓的 net PnL = half × (gross - RT fee 0.08%)
    net_pct = gross_pct - PAPER_FEE_PCT_ROUND_TRIP
    locked = round(half_notional * net_pct / 100.0, 2)
    t["tp1_partial_closed"] = True
    t["tp1_locked_pnl_usdt"] = locked
    t["notional_usdt"] = round(half_notional, 2)   # 剩 50% 仓位继续
# 手续费 — Binance USDT-M 永续 taker 0.04%, system 触发的 open/close 都是 market = taker
# round-trip = 0.04% × 2 = 0.08% (保守估计, 实战 maker 可能更便宜)
# 老 trade (无 fee_pct 字段) 会在 _enrich_trade_for_publish + 统计时追溯扣手续费
PAPER_FEE_PCT_ROUND_TRIP      = 0.08

# Phase 5.C (5/27) TP1 部分平仓 A/B 测试.
# 假设: 触 TP1 锁 50% 利润 + 50% 走 trailing 是否好过全单走 trailing.
# 数据驱动 (1410 笔) 预估全单走 trailing EV +$8.85/笔, 部分平仓 EV +$7.26/笔 (-$1.59)
# 但用户要求实测对比 — 由 MD5 hash 50/50 分组, 一周后审计两组真实表现.
#   "off":   永远不分组 (维持当前满仓走 trailing 行为)
#   "always": 永远部分平仓 (用于完全切换)
#   "ab":    MD5 hash 50/50 分组 (推荐, A=全单走 trailing, B=TP1 部分平仓)
PAPER_TP1_PARTIAL_CLOSE_MODE = "ab"

# ---- Phase 4 Shadow: premium tier 影子追踪 (不开真仓, 但模拟跟踪) ----
# 目的: 在不冒资金风险的前提下, 用 1-2 周时间收集 premium 信号的真实 outcome,
# 数据成熟后再决定是否启用 premium 自动开仓
PAPER_SHADOW_STATE   = Path.home() / "cresus-bot" / ".paper_shadow_trades.json"
PAPER_SHADOW_HISTORY = Path.home() / "cresus-bot" / "paper_shadow_history.json"
PAPER_SHADOW_TIERS   = ["premium"]      # 哪些 tier 进 shadow tracking
PAPER_SHADOW_NOTIONAL_HYPOTHETICAL = 200.0  # 假设的 notional (仅用于 P&L 计算, 不占用真实资金池)
PAPER_SHADOW_TG_NOTIFY = False          # shadow 不发 Telegram (避免噪声)
PAPER_SHADOW_VERDICT_MIN_N = 20         # 至少 N 笔已平才下结论

# ---- Regime 快照 (开仓时记录, 用于后续 regime × 胜率切片复盘, 不参与开仓决策) ----
# 数据来源: regime_radar 写入 ~/cresus-bot/regime.json (与行情天气表同源)
# 当前阶段: 仅记录, 不过滤; 等每种 regime 各积累 N≥20 钻石/shadow trade 后才考虑加 gate
REGIME_FILE = Path.home() / "cresus-bot" / "regime.json"

BINANCE_FAPI = "https://fapi.binance.com"
UA = "Mozilla/5.0 (Macintosh) cresus-velocity-scanner"
HTTP_TIMEOUT = 12

SCAN_TOP_N             = 200      # 24h 成交额 Top N 标的
KLINE_LIMIT            = 241      # 1m 数据条数 (4h+1 = 241 根, 让 _safe_pct(klines,240) 能访问 klines[-241])

# ---- 路径 A: 启动检测 (1m spike vs 30m 基线) ----
VOLUME_BURST_RATIO     = 5.0      # 1m 量 > 30m 均 × 此倍数
PRICE_MOVE_THRESHOLD   = 0.005    # 1m 价格变化 ≥ 0.5%

# ---- 路径 B: 持续动能 (1m vs 24h 全天分钟均 + 10m 累计趋势) ----
SUSTAINED_VOL_24H_RATIO     = 5.0    # 1m 量 ≥ X × (24h_quoteVol / 1440)
SUSTAINED_PRICE_10M_THRESHOLD = 0.015  # 10m 累计变化 ≥ 1.5%

# ---- ATR-based 入场建议 ----
ATR_PERIOD = 14
ATR_SL_MULT = 1.0   # 止损距 = 1.0 × ATR
ATR_TP1_MULT = 1.5  # TP1 距 = 1.5 × ATR (1.5R)
ATR_TP2_MULT = 3.0  # TP2 距 = 3.0 × ATR (3R)

DEDUP_WINDOW_MIN       = 30       # 同标的+类型去重窗口 (分钟)
KEEP_ALERT_WINDOW_MIN  = 60       # 输出 JSON 保留最近 X 分钟内的报警

# 排除杠杆代币、稳定币
EXCLUDE_PATTERNS = ("DOWN", "UP", "BEAR", "BULL", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "_PERP", "_PRE")


# ============================================================================
# Data class
# ============================================================================

@dataclass
class VelocityAlert:
    symbol: str
    base: str
    direction: str          # "LONG" if 价格上涨, "SHORT" if 下跌
    alert_type: str         # "burst" (启动 1m) | "sustained" (持续 10m)
    price: float
    price_change_pct: float # burst=1m 变化, sustained=10m 累计
    metric_window_min: int  # 1 或 10
    volume_1m_usdt: float
    volume_baseline_usdt: float
    volume_ratio: float
    detected_at: str
    intensity: int

    # ---- Phase 1 富信息 ----
    # 多窗口涨幅 (BILL 慢牛的解药)
    change_5m_pct:  Optional[float] = None
    change_15m_pct: Optional[float] = None
    change_1h_pct:  Optional[float] = None
    change_4h_pct:  Optional[float] = None

    # 主动资金方向 (kline 字段 10 / 字段 7, 0-1, >0.55 偏多, <0.45 偏空)
    taker_buy_ratio_1m: Optional[float] = None   # 最近 1m
    taker_buy_ratio_5m: Optional[float] = None   # 最近 5m 加权

    # OI Δ (新仓 vs 平仓识别): >0+价↑ = 真新仓; <0+价↑ = 空头回补
    oi_delta_5m_pct: Optional[float] = None

    # Phase 4.Z (5/27): 大户 / 散户多空比 — 当前阶段仅采集, 不进 scoring.
    # top_position > 1: Top 20% 大户按仓位金额净多 ; < 1: 净空.
    # top_account 与 global_account 同向偏离 = 大户散户共识 (高概率延续).
    # top vs global 反向 = 大户散户分歧 (常预示反转).
    top_trader_position_ratio: Optional[float] = None
    top_trader_account_ratio: Optional[float] = None
    global_account_ratio: Optional[float] = None

    # Funding rate (情绪拥挤指标): >0.05% = 多拥挤; <-0.05% = 空拥挤
    funding_rate_pct: Optional[float] = None

    # ATR-based 入场建议
    atr_pct: Optional[float] = None              # ATR / current price * 100
    range_4h_pct: Optional[float] = None         # 4h 真实波动幅度 (用于 vol regime 检测)
    vol_mult_used: float = 1.0                   # SL/TP 应用的 vol 倍数 (1.0 / 1.5 / 2.0)
    suggested_sl:  Optional[float] = None
    suggested_tp1: Optional[float] = None        # 1.5R
    suggested_tp2: Optional[float] = None        # 3R

    # Phase 3: 置信评分 (基于 GTC 反向工程: 极端 funding + OI 方向 + 历史胜率 + 多窗口)
    conviction_score: int = 0       # 0-10
    conviction_tier:  str = "regular"  # "diamond" (≥5) | "premium" (≥3) | "regular"


# ============================================================================
# HTTP + utilities
# ============================================================================

def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, file=sys.stderr)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[object]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError) as e:
        return None


def _is_excluded(symbol: str) -> bool:
    if not symbol or not symbol.endswith("USDT") or not symbol.isascii():
        return True
    base = symbol[:-4]
    if not base or len(base) > 12:
        return True
    return any(p in symbol for p in EXCLUDE_PATTERNS)


# ============================================================================
# Universe + data fetch
# ============================================================================

def fetch_universe() -> List[tuple]:
    """获取 Binance 永续 Top N USDT 标的, 返回 [(symbol, 24h_quote_vol), ...]."""
    data = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr")
    if not isinstance(data, list):
        _log("fetch_universe: bad response")
        return []
    pairs = []
    for t in data:
        sym = t.get("symbol", "")
        if _is_excluded(sym):
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
        except ValueError:
            continue
        if qv < 1_000_000:
            continue
        pairs.append((sym, qv))
    pairs.sort(key=lambda x: -x[1])
    return pairs[:SCAN_TOP_N]


def fetch_1m_klines(symbol: str) -> List[list]:
    """KLINE_LIMIT 根 1m K 线 (240 根 = 4h)."""
    url = f"{BINANCE_FAPI}/fapi/v1/klines?symbol={symbol}&interval=1m&limit={KLINE_LIMIT}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    return data


def fetch_all_funding_rates() -> dict:
    """一次 bulk 拉所有 USDT 永续 funding rate, 返回 {symbol: funding_rate_pct}."""
    data = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex")
    if not isinstance(data, list):
        return {}
    out = {}
    for t in data:
        sym = t.get("symbol", "")
        try:
            out[sym] = float(t.get("lastFundingRate", 0)) * 100  # 转 %
        except (ValueError, TypeError):
            pass
    return out


# Phase 5.B (5/27) — BTC regime (1h MA25 baseline), 复用 live_trader 算法.
# 用于 _compute_conviction trend-aligned bonus (+1 BTC up+LONG / BTC down+SHORT).
# Threshold ±0.5% 来自 live_trader LIVE_BTC_REGIME_THRESHOLD_PCT.
BTC_REGIME_THRESHOLD_PCT = 0.5


def fetch_btc_regime() -> Optional[str]:
    """获取 BTC 1h MA25 regime ('up' / 'down' / 'chop').

    一次 API 调用 per scan (整个 scan 共享同一 snapshot), 不是 per symbol.
    Returns: 'up' / 'down' / 'chop' / None (失败时).
    """
    url = f"{BINANCE_FAPI}/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=25"
    data = _http_get_json(url)
    if not isinstance(data, list) or len(data) < 25:
        return None
    try:
        closes = [float(k[4]) for k in data]
    except (ValueError, IndexError, TypeError):
        return None
    current = closes[-1]
    if current <= 0:
        return None
    ma25 = sum(closes) / len(closes)
    if ma25 <= 0:
        return None
    pct_vs_ma = (current - ma25) / ma25 * 100.0
    if pct_vs_ma >= BTC_REGIME_THRESHOLD_PCT:
        return "up"
    if pct_vs_ma <= -BTC_REGIME_THRESHOLD_PCT:
        return "down"
    return "chop"


def fetch_oi_delta_5m(symbol: str) -> Optional[float]:
    """OI 5min 前 vs 当前的变化 %.  Binance: /futures/data/openInterestHist."""
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2"
    data = _http_get_json(url)
    if not isinstance(data, list) or len(data) < 2:
        return None
    try:
        prev = float(data[-2].get("sumOpenInterest", 0))
        curr = float(data[-1].get("sumOpenInterest", 0))
        if prev <= 0:
            return None
        return round((curr - prev) / prev * 100, 3)
    except (ValueError, TypeError, KeyError):
        return None


# Phase 4.Z (5/27): 大户 / 散户多空比数据采集.
# 三个接口的本质区别:
#   topLongShortPositionRatio:  Top 20% 大户按"仓位 USDT 加权"的多空比 — 真金白银的方向
#   topLongShortAccountRatio:   Top 20% 大户按"账户数"的多空比 — 大户共识度
#   globalLongShortAccountRatio: 全市场散户按"账户数"的多空比 — retail 情绪
# 三者组合可识别"大户与散户分歧" (top_position 看空 但 retail 看多 = 大概率回调).
# 当前阶段: 仅采集到 paper_trade 元字段, 不进 conviction scoring.
# 1 周后 (~100 笔样本) 数据驱动判断是否纳入 scoring.

def fetch_top_position_ratio(symbol: str, period: str = "5m") -> Optional[float]:
    """Top 20% trader 多空比 (仓位 USDT 加权).

    Binance: /futures/data/topLongShortPositionRatio
    返回 longShortRatio (浮点): > 1.0 大户净多, < 1.0 大户净空.
    取最近 1 个 period (5m) snapshot.
    """
    url = (f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio"
           f"?symbol={symbol}&period={period}&limit=1")
    data = _http_get_json(url)
    if not isinstance(data, list) or not data:
        return None
    try:
        ratio = float(data[-1].get("longShortRatio", 0))
        return round(ratio, 4) if ratio > 0 else None
    except (ValueError, TypeError, KeyError):
        return None


def fetch_top_account_ratio(symbol: str, period: str = "5m") -> Optional[float]:
    """Top 20% trader 多空账户比 (按账户数).

    Binance: /futures/data/topLongShortAccountRatio
    返回 longShortRatio: 反映大户中"做多账户 / 做空账户"数量比.
    """
    url = (f"{BINANCE_FAPI}/futures/data/topLongShortAccountRatio"
           f"?symbol={symbol}&period={period}&limit=1")
    data = _http_get_json(url)
    if not isinstance(data, list) or not data:
        return None
    try:
        ratio = float(data[-1].get("longShortRatio", 0))
        return round(ratio, 4) if ratio > 0 else None
    except (ValueError, TypeError, KeyError):
        return None


def fetch_global_account_ratio(symbol: str, period: str = "5m") -> Optional[float]:
    """全市场散户多空账户比 (与 top 对比识别大户/散户分歧).

    Binance: /futures/data/globalLongShortAccountRatio
    返回 longShortRatio: 全市场 (含所有账户) 的多空账户数比.
    """
    url = (f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio"
           f"?symbol={symbol}&period={period}&limit=1")
    data = _http_get_json(url)
    if not isinstance(data, list) or not data:
        return None
    try:
        ratio = float(data[-1].get("longShortRatio", 0))
        return round(ratio, 4) if ratio > 0 else None
    except (ValueError, TypeError, KeyError):
        return None


def _safe_pct(klines: List[list], lookback: int) -> Optional[float]:
    """klines[-(lookback+1)] open → klines[-1] close 的变化 %.
    使用 [-1] 因为我们后面只对完成的 candle 走 [-2] 切片, 调用方应传完整切片."""
    if len(klines) < lookback + 1:
        return None
    try:
        start = float(klines[-(lookback + 1)][1])
        end = float(klines[-1][4])
        if start <= 0:
            return None
        return round((end - start) / start * 100, 3)
    except (ValueError, IndexError, TypeError):
        return None


def _taker_buy_ratio(kline_row: list) -> Optional[float]:
    """单根 kline 的主动买盘占比.  字段 7=quoteVol, 字段 10=taker_buy_quote_vol."""
    try:
        qv = float(kline_row[7])
        tbq = float(kline_row[10])
        if qv <= 0:
            return None
        return round(tbq / qv, 3)
    except (ValueError, IndexError, TypeError):
        return None


def _taker_buy_ratio_n(klines: List[list], n: int) -> Optional[float]:
    """最近 n 根完成 kline 的加权 taker buy 占比 (加权按 quoteVol)."""
    if len(klines) < n + 1:
        return None
    try:
        tot_qv = 0.0
        tot_tbq = 0.0
        for k in klines[-(n + 1):-1]:
            tot_qv += float(k[7])
            tot_tbq += float(k[10])
        if tot_qv <= 0:
            return None
        return round(tot_tbq / tot_qv, 3)
    except (ValueError, IndexError, TypeError):
        return None


def _compute_atr(klines: List[list], period: int = ATR_PERIOD) -> Optional[float]:
    """ATR(period) on 1m candles. 返回绝对值 (与 close price 同单位)."""
    if len(klines) < period + 2:
        return None
    try:
        trs = []
        for i in range(len(klines) - period - 1, len(klines) - 1):
            high  = float(klines[i][2])
            low   = float(klines[i][3])
            prev_close = float(klines[i - 1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else None
    except (ValueError, IndexError, TypeError):
        return None


def _compute_4h_range_pct(klines: List[list], current_price: float) -> Optional[float]:
    """4h 真实波动幅度 = (最高 - 最低) / 现价 × 100.
    比 1m ATR(14) 更能反映 memecoin/高波动币的宏观真实波动,
    用于 vol regime 检测 → 调整 SL/TP 宽度防止被局部洗盘.
    """
    if len(klines) < 240 or current_price <= 0:
        return None
    try:
        highs = [float(k[2]) for k in klines[-240:]]
        lows  = [float(k[3]) for k in klines[-240:]]
        return round((max(highs) - min(lows)) / current_price * 100, 2)
    except (ValueError, IndexError, TypeError):
        return None


# ============================================================================
# Detection logic
# ============================================================================

def analyze_symbol(symbol: str,
                   quote_vol_24h: Optional[float] = None,
                   funding_rate_pct: Optional[float] = None,
                   skip_oi: bool = False) -> Optional[VelocityAlert]:
    """对单个 symbol 做双路检测.
    路径 A "burst": 1m_vol/30m_avg ≥ 5x AND 1m 变化 ≥ 0.5%  → 启动瞬间
    路径 B "sustained": 1m_vol/24h_avg_per_min ≥ 5x AND 10m 累计 ≥ 1.5%  → 持续动能
    优先级: burst > sustained (启动信号更早,更值钱).
    """
    klines = fetch_1m_klines(symbol)
    if len(klines) < 25:
        return None

    # K 线字段: [openTime, open, high, low, close, volume, closeTime, quoteVol, ...]
    try:
        # 用倒数第二根 (最近完成的 1m)
        last_completed = klines[-2]
        recent_window = klines[-31:-2] if len(klines) >= 31 else klines[:-2]
        if len(recent_window) < 20:
            return None

        last_open  = float(last_completed[1])
        last_close = float(last_completed[4])
        last_vol   = float(last_completed[7])    # quoteVolume USDT

        if last_open <= 0:
            return None

        # ===== 路径 A: 启动 (1m vs 30m 基线) =====
        avg_30m = mean([float(k[7]) for k in recent_window])
        if avg_30m <= 0:
            return None
        vol_ratio_30m = last_vol / avg_30m
        price_change_1m = (last_close - last_open) / last_open

        burst = (vol_ratio_30m >= VOLUME_BURST_RATIO
                 and abs(price_change_1m) >= PRICE_MOVE_THRESHOLD)

        # ===== 路径 B: 持续动能 (1m vs 24h_avg/min + 10m 累计) =====
        sustained = False
        vol_ratio_24h = 0.0
        price_change_10m = 0.0
        avg_1m_24h = 0.0
        if quote_vol_24h and quote_vol_24h > 0:
            avg_1m_24h = quote_vol_24h / 1440.0   # 24h * 60min/h
            if avg_1m_24h > 0:
                vol_ratio_24h = last_vol / avg_1m_24h
            # 10m 累计: 取倒数 12 根中的前 10 根作为 10m 窗口起点
            ten_min = klines[-12:-2] if len(klines) >= 12 else []
            if len(ten_min) >= 8:
                start_price = float(ten_min[0][1])
                if start_price > 0:
                    price_change_10m = (last_close - start_price) / start_price
                    sustained = (vol_ratio_24h >= SUSTAINED_VOL_24H_RATIO
                                 and abs(price_change_10m) >= SUSTAINED_PRICE_10M_THRESHOLD)

        if not burst and not sustained:
            return None

        # 优先 burst (早期信号更值钱)
        if burst:
            alert_type = "burst"
            primary_ratio = vol_ratio_30m
            primary_change = price_change_1m
            baseline_vol = avg_30m
            window_min = 1
        else:
            alert_type = "sustained"
            primary_ratio = vol_ratio_24h
            primary_change = price_change_10m
            baseline_vol = avg_1m_24h
            window_min = 10

        # 强度: 综合 ratio 和 |change|
        intensity = 1
        if alert_type == "burst":
            if primary_ratio >= 10 and abs(primary_change) >= 0.01:
                intensity = 3
            elif primary_ratio >= 7 and abs(primary_change) >= 0.008:
                intensity = 2
        else:  # sustained
            if primary_ratio >= 10 and abs(primary_change) >= 0.03:
                intensity = 3
            elif primary_ratio >= 7 and abs(primary_change) >= 0.02:
                intensity = 2

        direction = "LONG" if primary_change > 0 else "SHORT"
        base = symbol[:-4].upper()

        # ===== Phase 1 富信息 =====
        # 多窗口涨幅: 用完整 klines (含最新 candle 也无妨, 价格点位)
        change_5m  = _safe_pct(klines, 5)
        change_15m = _safe_pct(klines, 15)
        change_1h  = _safe_pct(klines, 60)
        change_4h  = _safe_pct(klines, 240) if len(klines) >= 241 else None

        # 主动买盘占比 (kline 自带, 0 额外 API)
        taker_1m = _taker_buy_ratio(last_completed)
        taker_5m = _taker_buy_ratio_n(klines, 5)

        # ATR(14) 1m
        atr_abs = _compute_atr(klines, ATR_PERIOD)
        atr_pct = round(atr_abs / last_close * 100, 3) if (atr_abs and last_close > 0) else None

        # 4h 宏观波动幅度 (vol regime 检测) — 比 1m ATR 更能反映 memecoin 真实波动
        range_4h = _compute_4h_range_pct(klines, last_close)

        # vol regime → SL/TP 倍数. USELESS-style 高波动币用更宽 SL/TP, 防止被局部洗盘.
        # 比例保持 1:1.5:3 不变, 整体缩放.
        if range_4h is not None and range_4h >= 10.0:
            vol_mult = 2.0    # 极端波动 (24h 范围 >=10%): SL/TP 翻倍
        elif range_4h is not None and range_4h >= 5.0:
            vol_mult = 1.5    # 高波动: SL/TP 1.5x
        else:
            vol_mult = 1.0    # 普通波动: 标准 ATR

        # 入场建议: 以 last_close 为参考入场点
        sl = tp1 = tp2 = None
        if atr_abs and atr_abs > 0:
            if direction == "LONG":
                sl  = round(last_close - ATR_SL_MULT  * atr_abs * vol_mult, 8)
                tp1 = round(last_close + ATR_TP1_MULT * atr_abs * vol_mult, 8)
                tp2 = round(last_close + ATR_TP2_MULT * atr_abs * vol_mult, 8)
            else:
                sl  = round(last_close + ATR_SL_MULT  * atr_abs * vol_mult, 8)
                tp1 = round(last_close - ATR_TP1_MULT * atr_abs * vol_mult, 8)
                tp2 = round(last_close - ATR_TP2_MULT * atr_abs * vol_mult, 8)

        # OI 5m Δ (额外 1 次 API,可禁用)
        oi_delta = None
        # Phase 4.Z 大户/散户多空比 (3 次 API, 与 OI 同 candidate-level 节流)
        top_pos_ratio = None
        top_acc_ratio = None
        global_acc_ratio = None
        if not skip_oi:
            oi_delta = fetch_oi_delta_5m(symbol)
            top_pos_ratio = fetch_top_position_ratio(symbol)
            top_acc_ratio = fetch_top_account_ratio(symbol)
            global_acc_ratio = fetch_global_account_ratio(symbol)

        return VelocityAlert(
            symbol=symbol,
            base=base,
            direction=direction,
            alert_type=alert_type,
            price=last_close,
            price_change_pct=round(primary_change * 100, 3),
            metric_window_min=window_min,
            volume_1m_usdt=round(last_vol, 0),
            volume_baseline_usdt=round(baseline_vol, 0),
            volume_ratio=round(primary_ratio, 2),
            detected_at=datetime.now(timezone.utc).isoformat(),
            intensity=intensity,
            change_5m_pct=change_5m,
            change_15m_pct=change_15m,
            change_1h_pct=change_1h,
            change_4h_pct=change_4h,
            taker_buy_ratio_1m=taker_1m,
            taker_buy_ratio_5m=taker_5m,
            oi_delta_5m_pct=oi_delta,
            top_trader_position_ratio=top_pos_ratio,
            top_trader_account_ratio=top_acc_ratio,
            global_account_ratio=global_acc_ratio,
            funding_rate_pct=round(funding_rate_pct, 4) if funding_rate_pct is not None else None,
            atr_pct=atr_pct,
            range_4h_pct=range_4h,
            vol_mult_used=vol_mult,
            suggested_sl=sl,
            suggested_tp1=tp1,
            suggested_tp2=tp2,
        )
    except (ValueError, TypeError, IndexError):
        return None


# ============================================================================
# Phase 2: Outcome tracking + win-rate
# ============================================================================

def _load_outcomes() -> dict:
    if not OUTCOMES_STATE.exists():
        return {"alerts": {}}
    try:
        data = json.loads(OUTCOMES_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "alerts" not in data:
            return {"alerts": {}}
        return data
    except Exception:
        return {"alerts": {}}


def _save_outcomes(state: dict) -> None:
    try:
        OUTCOMES_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUTCOMES_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUTCOMES_STATE)
    except Exception as e:
        _log(f"save_outcomes failed: {e}")


def _alert_id(a: VelocityAlert) -> str:
    # detected_at 本身含 +00:00, 用作幂等键
    return f"{a.symbol}|{a.alert_type}|{a.direction}|{a.detected_at}"


def _log_new_alerts_to_outcomes(state: dict, new_alerts: List[VelocityAlert]) -> None:
    """新 alert 落库为待结算 entry."""
    for a in new_alerts:
        aid = _alert_id(a)
        if aid in state["alerts"]:
            continue
        state["alerts"][aid] = {
            "symbol": a.symbol,
            "alert_type": a.alert_type,
            "direction": a.direction,
            "intensity": a.intensity,
            "entry_price": a.price,
            "detected_at": a.detected_at,
            # 上下文 (未来回归用)
            "vol_ratio": a.volume_ratio,
            "primary_change_pct": a.price_change_pct,
            "outcomes": {f"{m}m": None for m in OUTCOME_STAGES_MIN},
        }


def _fetch_all_prices_now() -> dict:
    """1 次 bulk 拉所有 USDT-M 永续现价. 返回 {symbol: float}."""
    data = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/price")
    if not isinstance(data, list):
        return {}
    out = {}
    for t in data:
        try:
            out[t["symbol"]] = float(t["price"])
        except (KeyError, ValueError, TypeError):
            pass
    return out


def _resolve_matured_outcomes(state: dict) -> int:
    """扫所有 alert,把已到期但未结算的 stage 用当前价填上.
    返回 resolved 条数 (用于日志)."""
    now = datetime.now(timezone.utc)
    pending: List[tuple] = []   # (aid, stage_key, stage_min)
    for aid, rec in state["alerts"].items():
        try:
            det = datetime.fromisoformat(rec["detected_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        age_min = (now - det).total_seconds() / 60.0
        for m in OUTCOME_STAGES_MIN:
            key = f"{m}m"
            if rec["outcomes"].get(key) is None and age_min >= m:
                pending.append((aid, key, m))

    if not pending:
        return 0

    prices = _fetch_all_prices_now()
    if not prices:
        return 0

    resolved = 0
    for aid, key, _m in pending:
        rec = state["alerts"].get(aid)
        if not rec:
            continue
        sym = rec["symbol"]
        cur = prices.get(sym)
        if cur is None:
            continue
        entry = float(rec["entry_price"])
        if entry <= 0:
            continue
        raw_pct = (cur - entry) / entry * 100.0
        # 方向化: LONG 取正向, SHORT 取反向 (正=对方向走对了)
        signed_pct = raw_pct if rec["direction"] == "LONG" else -raw_pct
        rec["outcomes"][key] = {
            "price": cur,
            "outcome_pct": round(signed_pct, 3),
            "resolved_at": now.isoformat(),
        }
        resolved += 1
    return resolved


def _prune_old_outcomes(state: dict) -> int:
    """删 >OUTCOMES_RETENTION_DAYS 天前的 alert. 返回删除数."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=OUTCOMES_RETENTION_DAYS)
    to_del = []
    for aid, rec in state["alerts"].items():
        try:
            det = datetime.fromisoformat(rec["detected_at"].replace("Z", "+00:00"))
            if det < cutoff:
                to_del.append(aid)
        except Exception:
            to_del.append(aid)
    for aid in to_del:
        del state["alerts"][aid]
    return len(to_del)


def _build_winrate_summary(state: dict) -> dict:
    """按 (symbol|alert_type|direction) 聚合, 每个 stage 计算胜率."""
    buckets: dict = {}
    for rec in state["alerts"].values():
        key = f"{rec['symbol']}|{rec['alert_type']}|{rec['direction']}"
        b = buckets.setdefault(key, {
            "symbol": rec["symbol"],
            "alert_type": rec["alert_type"],
            "direction": rec["direction"],
            "stages": {f"{m}m": {"n": 0, "wins": 0, "sum_pct": 0.0} for m in OUTCOME_STAGES_MIN},
        })
        for m in OUTCOME_STAGES_MIN:
            stk = f"{m}m"
            oc = rec["outcomes"].get(stk)
            if not oc:
                continue
            pct = oc.get("outcome_pct", 0.0)
            b["stages"][stk]["n"] += 1
            b["stages"][stk]["sum_pct"] += pct
            if pct >= OUTCOME_WIN_THRESHOLD_PCT:
                b["stages"][stk]["wins"] += 1

    # 转输出格式
    out_by_key = {}
    for key, b in buckets.items():
        stages_out = {}
        any_data = False
        for stk, s in b["stages"].items():
            if s["n"] >= WINRATE_MIN_SAMPLES:
                stages_out[stk] = {
                    "n": s["n"],
                    "win_rate": round(s["wins"] / s["n"], 3),
                    "avg_outcome_pct": round(s["sum_pct"] / s["n"], 2),
                }
                any_data = True
        if any_data:
            out_by_key[key] = {
                "symbol": b["symbol"],
                "alert_type": b["alert_type"],
                "direction": b["direction"],
                "stages": stages_out,
            }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "win_threshold_pct": OUTCOME_WIN_THRESHOLD_PCT,
        "min_samples": WINRATE_MIN_SAMPLES,
        "by_key": out_by_key,
    }


def _save_winrate(summary: dict) -> None:
    try:
        WINRATE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = WINRATE_OUTPUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(WINRATE_OUTPUT)
    except Exception as e:
        _log(f"save_winrate failed: {e}")


# ============================================================================
# Dedup + persistence
# ============================================================================

def _load_dedup() -> dict:
    if not DEDUP_STATE.exists():
        return {}
    try:
        return json.loads(DEDUP_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dedup(state: dict) -> None:
    try:
        DEDUP_STATE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_STATE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _is_recently_alerted(symbol: str, dedup: dict) -> bool:
    last_ts = dedup.get(symbol)
    if not last_ts:
        return False
    try:
        last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except Exception:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(minutes=DEDUP_WINDOW_MIN)


def _load_alerts() -> List[dict]:
    if not DEFAULT_OUTPUT.exists():
        return []
    try:
        data = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        return data.get("alerts", [])
    except Exception:
        return []


def _save_alerts(all_alerts: List[dict]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scan_universe": SCAN_TOP_N,
        "thresholds": {
            "volume_burst_ratio":          VOLUME_BURST_RATIO,
            "price_move_pct":              PRICE_MOVE_THRESHOLD * 100,
            "sustained_vol_24h_ratio":     SUSTAINED_VOL_24H_RATIO,
            "sustained_price_10m_pct":     SUSTAINED_PRICE_10M_THRESHOLD * 100,
            "dedup_window_min":            DEDUP_WINDOW_MIN,
        },
        "alerts_count": len(all_alerts),
        "alerts": all_alerts,
    }
    try:
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = DEFAULT_OUTPUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DEFAULT_OUTPUT)
    except Exception as e:
        _log(f"save_alerts failed: {e}")


def _trim_old_alerts(alerts: List[dict]) -> List[dict]:
    """只保留最近 KEEP_ALERT_WINDOW_MIN 分钟的报警."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=KEEP_ALERT_WINDOW_MIN)
    out = []
    for a in alerts:
        try:
            ts = datetime.fromisoformat(a.get("detected_at", "").replace("Z", "+00:00"))
            if ts >= cutoff:
                out.append(a)
        except Exception:
            continue
    return out


# ============================================================================
# Phase 3a: 置信评分 (Conviction) — 反向工程 GTC 的"DNA"
# ============================================================================
# GTC 案例 (entry 0.1698 → 现 0.19345, +13.9%, 远超 TP2 +4.65%):
#   - sustained 类型 (+1)
#   - funding -2.0% 极端 (+3, 最强信号: 空头被压制 → 轧空)
#   - OI +1.40% 方向匹配 (+2)
#   - 历史 30m 胜率 86% N=7 (+3, 经验主义)
#   - 5m/15m 同向 (+1)
#   总分 10/10 → 💎 diamond
# 对比同时间 CUSDT/USUSDT: OI 方向不匹配 + funding 平 = 1/10 噪声

CONVICTION_DIAMOND_THRESHOLD = 5   # ≥5 = 💎 钻石
CONVICTION_PREMIUM_THRESHOLD = 3   # ≥3 = ⭐ 中置信

def _compute_conviction(a: VelocityAlert, winrate_summary: Optional[dict],
                          regime: Optional[str] = None,
                          btc_regime: Optional[str] = None) -> tuple:
    """计算 (score 0-10, tier str).
    修订 v2 (基于 USELESS/GTC 失败案例反向工程):
    - 加宏观逆势硬否决 (1h/4h 严重反向直接拒绝, 不靠加分扛)
    - 历史胜率: N≥10 严格 / N≥5 高胜率折扣 (避免小样本误导)
    - Funding 权重 +3 → +2 (单一指标不应主导评分)
    - 多窗口对齐: 1h+4h 都同向 +2 (强对齐), 单边同向 +1

    Phase 5.A (5/27) 增: macro regime 联动减分.
    - ALT_SEASON_RUNNING + LONG: -1 分 (paper 数据: 准负 EV)

    Phase 5.B (5/27) 增: BTC regime trend-aligned bonus.
    - BTC up + LONG: +1 (顺势)
    - BTC down + SHORT: +1 (顺势)
    - BTC down + LONG: 已在 live_trader 硬拒, scanner 不再加分

    Phase 5.N (6/1) 增: live 实盘亏损陷阱 -1 分.
    数据驱动 (live 830 笔):
    - RANGE_BORING + SHORT: live avg -$1.45, 震荡 SHORT 缺方向性
    - ALT_SEASON_RUNNING + SHORT: live avg -$1.81, 反向追单陷阱
    保留: RISK_OFF + SHORT (live avg +$0.64, 唯一稳定盈利组合)
    """
    is_long = (a.direction == "LONG")

    # ===== 硬否决: 宏观逆势 → 直接 0 分, 不进入加分 =====
    # 防止"下跌趋势中追多"或"上涨趋势中追空"这类 GTC 式陷阱
    if a.change_4h_pct is not None:
        h4 = a.change_4h_pct
        if (is_long and h4 <= -3.0) or (not is_long and h4 >= 3.0):
            return 0, "regular"   # 4h 反向 ≥3%: 强趋势中逆向, 拒
    if a.change_1h_pct is not None:
        h1 = a.change_1h_pct
        if (is_long and h1 <= -1.5) or (not is_long and h1 >= 1.5):
            return 0, "regular"   # 1h 反向 ≥1.5%: 短线趋势逆向, 拒

    # ===== 硬否决: 顺势追末段 → 直接 0 分 =====
    # 防止"24h 跌 30%+ 才喊 SHORT" 或 "涨 30%+ 才喊 LONG" 这类追尾陷阱
    # 主跌/主涨已完成, RR 极差 (TP 距 24h 极值不足, SL 易被反向打)
    # 实战案例: ATAUSDT 4h -36%, SYSUSDT 4h -33%, MLNUSDT 4h -25%+
    # 这类 SHORT 几乎不可能盈利 — 距 24h 低 <5% 但 TP1 要求 -6%+
    LATE_ENTRY_4H_LIMIT = 20.0
    if a.change_4h_pct is not None:
        h4 = a.change_4h_pct
        if (is_long and h4 >= LATE_ENTRY_4H_LIMIT) or \
           (not is_long and h4 <= -LATE_ENTRY_4H_LIMIT):
            return 0, "regular"   # 4h |变化| ≥20%: 主移动已完成, 拒末段追单

    score = 0

    # 1. Funding 评分 — Phase 6.C-B (2026-06-04): 方向感知, 修正之前 abs 一刀切.
    # 老逻辑: abs(funding) >= 0.3 → +2 (任何方向都加, 但跟方向逻辑不对).
    # 新逻辑: funding 反映多头/空头拥挤度. 拥挤方向追单 = 反向风险大, 应减分;
    #         相反方向 = fade 拥挤 = 潜在 alpha, 应加分.
    #   funding > 0 (多头拥挤):
    #     LONG  → 追拥挤多头, 减分
    #     SHORT → fade 多头, 加分
    #   funding < 0 (空头拥挤):
    #     LONG  → fade 空头, 加分
    #     SHORT → 追拥挤空头, 减分
    # 阈值分层 (跟旧 abs 阈值兼容):
    #   |funding| >= 0.3%  → ±2 (极端拥挤)
    #   |funding| >= 0.05% → ±1 (中等拥挤)
    if a.funding_rate_pct is not None and PAPER_FUNDING_DIRECTION_BIAS:
        f = a.funding_rate_pct
        crowded_long = (f > 0)        # f > 0: 多头拥挤 (多头付 funding)
        crowded_short = (f < 0)
        chasing_crowd = (is_long and crowded_long) or ((not is_long) and crowded_short)
        fading_crowd = (is_long and crowded_short) or ((not is_long) and crowded_long)
        if abs(f) >= 0.3:
            if fading_crowd:
                score += 2
            elif chasing_crowd:
                score -= 2
        elif abs(f) >= 0.05:
            if fading_crowd:
                score += 1
            elif chasing_crowd:
                score -= 1
    elif a.funding_rate_pct is not None and not PAPER_FUNDING_DIRECTION_BIAS:
        # 老 abs 行为 (兼容退路, 万一新逻辑要回滚)
        if abs(a.funding_rate_pct) >= 0.3:
            score += 2

    # 2. 历史 30m 胜率: N≥10 高门槛 (+3) / N≥5 + 高胜率折扣 (+2)
    if winrate_summary and winrate_summary.get("by_key"):
        wkey = f"{a.symbol}|{a.alert_type}|{a.direction}"
        w = winrate_summary["by_key"].get(wkey)
        if w and w.get("stages"):
            s30 = w["stages"].get("30m")
            if s30:
                n = s30.get("n", 0)
                rate = s30.get("win_rate", 0)
                if n >= 10 and rate >= 0.65:
                    score += 3   # 统计有意义的样本量
                elif n >= 5 and rate >= 0.70:
                    score += 2   # 小样本但表现优异, 折扣到 +2

    # 3. OI 方向匹配 (+2): LONG+OI涨 = 真新多, SHORT+OI跌 = 真新空
    if a.oi_delta_5m_pct is not None:
        if (is_long and a.oi_delta_5m_pct > 0) or (not is_long and a.oi_delta_5m_pct < 0):
            score += 2

    # 4. sustained 类型 (+1): 10m 累计比 1m burst 多一层时间验证
    if a.alert_type == "sustained":
        score += 1

    # 5. 多窗口对齐: 1h+4h 都同向 (+2, 强对齐) / 单边同向 (+1)
    if a.change_1h_pct is not None and a.change_4h_pct is not None:
        h1_aligned = (a.change_1h_pct > 0) == is_long
        h4_aligned = (a.change_4h_pct > 0) == is_long
        if h1_aligned and h4_aligned:
            score += 2   # 强对齐: 短线 + 中线趋势都站我方
        elif h1_aligned or h4_aligned:
            score += 1

    # 6. Phase 5.A regime 联动: ALT_SEASON_RUNNING + LONG -1 分
    # 数据驱动 (1410 笔): ALT_SEASON_RUNNING+LONG n=156 avg +$0.06 win 33%, 准负 EV.
    # 软减分而非硬拒, 让其他维度强信号仍可救到 diamond.
    if regime == "ALT_SEASON_RUNNING" and is_long:
        score -= 1

    # 6.N. Phase 5.N (6/1) regime + direction live 实盘亏损陷阱 -1 分.
    # 数据驱动 (5/26+ live 830 笔):
    #   RANGE_BORING + SHORT: 128 笔 live avg -$1.45 (vs paper avg +$1.06,
    #     摩擦完全吃掉 EV. 震荡市场 SHORT 缺方向性, wick 反复打 trail).
    #   ALT_SEASON_RUNNING + SHORT: 27 笔 live avg -$1.81 (反向追单陷阱,
    #     上涨行情逆向 SL 触发率 64%).
    # -1 conviction 过滤掉这两个组合中的 score 5 边缘信号 (~85%), 保留高分 6-7.
    # 预期: 避亏 ~$14/天 (基于 14 天历史回测).
    # 不动: ALT_SEASON + LONG (已 Phase 5.A 处理) / RISK_OFF + SHORT (唯一 live 盈利)
    if regime == "RANGE_BORING" and not is_long:
        score -= 1
    elif regime == "ALT_SEASON_RUNNING" and not is_long:
        score -= 1

    # 7. Phase 5.B BTC regime trend-aligned bonus
    # 假设: 顺 BTC 短线趋势 (1h MA25) 的方向应该比逆势更可靠.
    # 数据: 历史 paper 没有 BTC up/down 字段, 无直接验证, 采用理论合理的小幅加分.
    # BTC up + LONG / BTC down + SHORT → +1 ; 其他不动.
    # down + LONG 已在 live_trader is_eligible 硬拒 (0/10 胜率 p=0.042), 不再加分.
    if btc_regime == "up" and is_long:
        score += 1
    elif btc_regime == "down" and not is_long:
        score += 1

    # 防御: score 永不为负 (tier 计算要求非负数)
    score = max(score, 0)

    # tier 分级 (阈值不变)
    if score >= CONVICTION_DIAMOND_THRESHOLD:
        tier = "diamond"
    elif score >= CONVICTION_PREMIUM_THRESHOLD:
        tier = "premium"
    else:
        tier = "regular"

    return score, tier


# ============================================================================
# Phase 3: Telegram push (高质量信号绕过看板延迟, 直接推手机)
# ============================================================================

def _load_tg_cooldown() -> dict:
    if not TG_COOLDOWN_STATE.exists():
        return {}
    try:
        return json.loads(TG_COOLDOWN_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tg_cooldown(state: dict) -> None:
    try:
        TG_COOLDOWN_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TG_COOLDOWN_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(TG_COOLDOWN_STATE)
    except Exception as e:
        _log(f"tg_cooldown save failed: {e}")


def _send_telegram(text: str) -> bool:
    """发送一条 Telegram 消息. 失败静默, 不影响主流程."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
        # 不用 markdown_v2 (转义太麻烦), 用 HTML 模式
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            if r.status == 200:
                return True
            _log(f"tg send non-200: {r.status}")
            return False
    except Exception as e:
        _log(f"tg send failed: {e}")
        return False


def _format_alert_for_tg(a: VelocityAlert, winrate_summary: Optional[dict] = None) -> str:
    """构造 Telegram 消息 (HTML 模式). 数据丰富但简洁,适配手机阅读."""
    type_icon = "🔥" if a.alert_type == "sustained" else "⚡"
    type_label = "持续" if a.alert_type == "sustained" else "启动"
    dir_icon = "📈" if a.direction == "LONG" else "📉"
    win_str = f"{a.metric_window_min}m"
    pct_str = f"{a.price_change_pct:+.2f}%"

    # Phase 3a: 置信徽章 (放在最前, 一眼看出来)
    tier_prefix = ""
    if a.conviction_tier == "diamond":
        tier_prefix = "💎💎💎 <b>钻石信号</b> 💎💎💎\n"
    elif a.conviction_tier == "premium":
        tier_prefix = "⭐ <b>中置信</b>\n"

    lines = [
        tier_prefix + f"<b>{type_icon} {a.symbol}</b> {type_label} <b>{a.direction}</b> {dir_icon}",
        f"{pct_str} / {win_str} · vol <b>{a.volume_ratio:.1f}x</b> · 强度 {a.intensity} · 置信 <b>{a.conviction_score}/10</b>",
    ]

    # 富信息段 (chips)
    chips = []
    if a.funding_rate_pct is not None:
        flag = "🔥" if abs(a.funding_rate_pct) > 0.05 else ""
        chips.append(f"Fnd {a.funding_rate_pct:+.3f}%{flag}")
    if a.oi_delta_5m_pct is not None:
        oi_meaning = ""
        if a.direction == "LONG":
            oi_meaning = " 真新多" if a.oi_delta_5m_pct > 0 else " 空头回补"
        else:
            oi_meaning = " 真新空" if a.oi_delta_5m_pct < 0 else " 诱空"
        chips.append(f"OI {a.oi_delta_5m_pct:+.2f}%{oi_meaning}")
    if a.taker_buy_ratio_1m is not None:
        chips.append(f"主{int(a.taker_buy_ratio_1m * 100)}")
    if chips:
        lines.append("  " + " · ".join(chips))

    # 多窗口
    multi = []
    if a.change_5m_pct is not None: multi.append(f"5m {a.change_5m_pct:+.2f}%")
    if a.change_1h_pct is not None: multi.append(f"1h {a.change_1h_pct:+.2f}%")
    if a.change_4h_pct is not None: multi.append(f"4h {a.change_4h_pct:+.2f}%")
    if multi:
        lines.append("  " + " · ".join(multi))

    # 入场建议
    if a.suggested_sl is not None:
        def _fmt(p):
            n = float(p)
            if n < 1:   return f"{n:.6g}"
            if n < 100: return f"{n:.4f}"
            return f"{n:.2f}"
        def _dist(target):
            return (target - a.price) / a.price * 100
        lines.append("")
        lines.append(f"💼 入场 <code>{_fmt(a.price)}</code>")
        lines.append(f"  SL  <code>{_fmt(a.suggested_sl)}</code> ({_dist(a.suggested_sl):+.2f}%, 1R)")
        lines.append(f"  TP1 <code>{_fmt(a.suggested_tp1)}</code> ({_dist(a.suggested_tp1):+.2f}%, 1.5R)")
        lines.append(f"  TP2 <code>{_fmt(a.suggested_tp2)}</code> ({_dist(a.suggested_tp2):+.2f}%, 3R)")
        if a.atr_pct is not None:
            lines.append(f"  ATR(14) {a.atr_pct:.2f}%")

    # 历史胜率 (如果该 setup 已有 N≥5 经验数据)
    if winrate_summary and winrate_summary.get("by_key"):
        wkey = f"{a.symbol}|{a.alert_type}|{a.direction}"
        w = winrate_summary["by_key"].get(wkey)
        if w and w.get("stages"):
            s30 = w["stages"].get("30m")
            if s30:
                rate100 = round(s30["win_rate"] * 100)
                mu = s30["avg_outcome_pct"]
                lines.append(f"🏆 历史 30m 胜率 <b>{rate100}%</b> (N={s30['n']}, μ{mu:+.2f}%)")

    return "\n".join(lines)


# ============================================================================
# Phase 3b: Email VIP 通道 (仅钻石信号, 避免被 TG 噪声淹没)
# ============================================================================

def _load_email_cooldown() -> dict:
    if not EMAIL_COOLDOWN_STATE.exists():
        return {}
    try:
        return json.loads(EMAIL_COOLDOWN_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_email_cooldown(state: dict) -> None:
    try:
        EMAIL_COOLDOWN_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = EMAIL_COOLDOWN_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(EMAIL_COOLDOWN_STATE)
    except Exception as e:
        _log(f"email_cooldown save failed: {e}")


def _send_email(subject: str, body: str) -> bool:
    """发送邮件. 失败静默 (不影响主流程)."""
    if not (EMAIL_SMTP_HOST and EMAIL_USERNAME and EMAIL_PASSWORD and EMAIL_TO):
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            smtp.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        return True
    except Exception as e:
        _log(f"email send failed: {e}")
        return False


def _format_alert_for_email(a: VelocityAlert, winrate_summary: Optional[dict] = None) -> tuple:
    """构造邮件 (subject, body). 比 TG 更详细, 适合慢慢看."""
    type_label = "持续" if a.alert_type == "sustained" else "启动"
    dir_icon = "📈" if a.direction == "LONG" else "📉"
    type_icon = "🔥" if a.alert_type == "sustained" else "⚡"

    # Subject: 让 Gmail 过滤规则容易匹配 [CRESUS 💎] 前缀
    subject = (
        f"[CRESUS 💎] {a.symbol} {a.direction} "
        f"{a.price_change_pct:+.2f}%/{a.metric_window_min}m "
        f"· 置信 {a.conviction_score}/10"
    )

    L = []
    L.append("💎💎💎  钻石信号  💎💎💎")
    L.append("")
    L.append(f"{type_icon} {a.symbol}  {type_label}{a.direction} {dir_icon}")
    L.append(f"{a.price_change_pct:+.2f}% / {a.metric_window_min}m  ·  vol {a.volume_ratio:.1f}x  ·  置信 {a.conviction_score}/10")
    L.append("")
    L.append("─────── 富信息 ───────")
    if a.funding_rate_pct is not None:
        flag = " 🔥" if abs(a.funding_rate_pct) > 0.05 else ""
        L.append(f"Funding 资金费率: {a.funding_rate_pct:+.4f}%{flag}")
    if a.oi_delta_5m_pct is not None:
        meaning = ""
        if a.direction == "LONG":
            meaning = "（真新多）" if a.oi_delta_5m_pct > 0 else "（空头回补，弱信号）"
        else:
            meaning = "（真新空）" if a.oi_delta_5m_pct < 0 else "（诱空，弱信号）"
        L.append(f"OI 5m 变化: {a.oi_delta_5m_pct:+.2f}% {meaning}")
    if a.taker_buy_ratio_1m is not None:
        L.append(f"1m 主动买盘: {int(a.taker_buy_ratio_1m * 100)}%")
    if a.taker_buy_ratio_5m is not None:
        L.append(f"5m 主动买盘: {int(a.taker_buy_ratio_5m * 100)}%")
    L.append("")

    L.append("─────── 多窗口涨幅 ───────")
    if a.change_5m_pct  is not None: L.append(f"5m:  {a.change_5m_pct:+.2f}%")
    if a.change_15m_pct is not None: L.append(f"15m: {a.change_15m_pct:+.2f}%")
    if a.change_1h_pct  is not None: L.append(f"1h:  {a.change_1h_pct:+.2f}%")
    if a.change_4h_pct  is not None: L.append(f"4h:  {a.change_4h_pct:+.2f}%")
    L.append("")

    if a.suggested_sl is not None:
        def _fmt(p):
            n = float(p)
            if n < 1:   return f"{n:.6g}"
            if n < 100: return f"{n:.4f}"
            return f"{n:.2f}"
        def _dist(target):
            return (target - a.price) / a.price * 100
        L.append("─────── 入场建议 (3-阶段动态 SL/TP) ───────")
        L.append(f"入场: {_fmt(a.price)}")
        L.append(f"SL  (Phase A 止损):    {_fmt(a.suggested_sl)}   ({_dist(a.suggested_sl):+.2f}%, 1R)")
        L.append(f"TP1 (触发 → SL 移 BE): {_fmt(a.suggested_tp1)}   ({_dist(a.suggested_tp1):+.2f}%, 1.5R)")
        L.append(f"TP2 (触发 → 启动 trailing): {_fmt(a.suggested_tp2)}   ({_dist(a.suggested_tp2):+.2f}%, 3R)")
        if a.atr_pct is not None:
            L.append(f"ATR(14): {a.atr_pct:.2f}%")
        if a.range_4h_pct is not None:
            L.append(f"4h 真实波动范围: {a.range_4h_pct:.1f}%")
        if a.vol_mult_used and a.vol_mult_used > 1.0:
            L.append(f"vol_mult: {a.vol_mult_used:.1f}× (高波动 regime, SL/TP 已放宽)")
        L.append("")

    if winrate_summary and winrate_summary.get("by_key"):
        wkey = f"{a.symbol}|{a.alert_type}|{a.direction}"
        w = winrate_summary["by_key"].get(wkey)
        if w and w.get("stages"):
            s30 = w["stages"].get("30m")
            if s30:
                L.append("─────── 历史胜率 ───────")
                L.append(f"30m 胜率: {int(s30['win_rate']*100)}% (N={s30['n']}, μ {s30['avg_outcome_pct']:+.2f}%)")
                s60 = w["stages"].get("60m")
                if s60:
                    L.append(f"1h  胜率: {int(s60['win_rate']*100)}% (N={s60['n']}, μ {s60['avg_outcome_pct']:+.2f}%)")
                s240 = w["stages"].get("240m")
                if s240:
                    L.append(f"4h  胜率: {int(s240['win_rate']*100)}% (N={s240['n']}, μ {s240['avg_outcome_pct']:+.2f}%)")
                L.append("")

    L.append("─────── 元信息 ───────")
    L.append(f"触发时间 (UTC): {a.detected_at[:19].replace('T', ' ')}")
    L.append(f"Binance 链接: https://www.binance.com/en/futures/{a.symbol}")
    L.append("")
    L.append("--")
    L.append("Cresus 量能加速雷达 · 钻石信号专属邮件通道")
    L.append("仅 conviction_tier=diamond (score ≥5) 触发, TP1/TP2/止损自动管理")
    return subject, "\n".join(L)


def _push_diamond_email(a: VelocityAlert, winrate_summary: Optional[dict],
                        email_cooldown: dict, now: datetime) -> bool:
    """钻石信号邮件推送, 含 cooldown 检查.
    仅 conviction_score ≥ EMAIL_MIN_SCORE 才发邮件 — 实战数据 (N=42) 证明:
      score 5 钻石: avg ≈ 0% (大多 SL/BE, 偶发小赢)
      score 6+ 钻石: avg +9.6%, 100% 胜率 (N=3 仅供参考但方向明确)
    邮件留给真正高质量信号, Telegram 仍发所有钻石.
    """
    if not (EMAIL_SMTP_HOST and EMAIL_USERNAME and EMAIL_PASSWORD and EMAIL_TO):
        return False
    if a.conviction_tier != "diamond":
        return False
    if (a.conviction_score or 0) < EMAIL_MIN_SCORE:
        return False   # 低分钻石不发邮件 (走 TG 即可)
    # cooldown 按 (symbol, direction) 1 小时去重
    key = f"{a.symbol}|{a.direction}"
    if key in email_cooldown:
        try:
            ts = datetime.fromisoformat(email_cooldown[key].replace("Z", "+00:00"))
            if (now - ts).total_seconds() < EMAIL_COOLDOWN_MIN * 60:
                return False  # cooldown active
        except Exception:
            pass
    subject, body = _format_alert_for_email(a, winrate_summary)
    if _send_email(subject, body):
        email_cooldown[key] = now.isoformat()
        return True
    return False


def _push_to_telegram(new_alerts: List[VelocityAlert],
                      winrate_summary: Optional[dict]) -> int:
    """筛选高质量 alerts 并推送. 返回推送数 (含跳过).
    门槛: intensity ≥ TG_MIN_INTENSITY AND 30min 冷却内未推过同 symbol.
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return 0
    if not new_alerts:
        return 0
    cooldown = _load_tg_cooldown()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=TG_COOLDOWN_MIN)
    # 清理过期 cooldown 条目
    for sym in list(cooldown.keys()):
        try:
            ts = datetime.fromisoformat(cooldown[sym].replace("Z", "+00:00"))
            if ts < cutoff:
                del cooldown[sym]
        except Exception:
            del cooldown[sym]

    # Phase 3b: email cooldown (跟 TG 独立, 1h)
    email_cooldown = _load_email_cooldown()
    # 清理过期 email cooldown
    e_cutoff = now - timedelta(minutes=EMAIL_COOLDOWN_MIN)
    for k in list(email_cooldown.keys()):
        try:
            ts = datetime.fromisoformat(email_cooldown[k].replace("Z", "+00:00"))
            if ts < e_cutoff:
                del email_cooldown[k]
        except Exception:
            del email_cooldown[k]

    pushed = 0
    diamond_n = 0
    email_sent = 0
    for a in new_alerts:
        is_diamond = (a.conviction_tier == "diamond")
        # 钻石信号: 跳过 intensity 门槛 + 跳过 TG cooldown (绝不漏)
        # 普通信号: 严格 intensity ≥ TG_MIN_INTENSITY + 30min 冷却
        if not is_diamond:
            if a.intensity < TG_MIN_INTENSITY:
                continue
            if a.symbol in cooldown:
                continue
        msg = _format_alert_for_tg(a, winrate_summary)
        if _send_telegram(msg):
            cooldown[a.symbol] = now.isoformat()
            pushed += 1
            if is_diamond:
                diamond_n += 1
                # Phase 3b: 钻石专属邮件 (1h cooldown, 独立于 TG)
                try:
                    if _push_diamond_email(a, winrate_summary, email_cooldown, now):
                        email_sent += 1
                        _log(f"📧 钻石邮件已发 {a.symbol} {a.direction}")
                except Exception as e:
                    _log(f"diamond email failed: {e}")

    if pushed > 0:
        _save_tg_cooldown(cooldown)
        if email_sent > 0:
            _save_email_cooldown(email_cooldown)
        if diamond_n > 0:
            _log(f"📲 Telegram 推送 {pushed} 条 (其中 💎 钻石 {diamond_n} 条, 📧 邮件 {email_sent} 封)")
        else:
            _log(f"📲 Telegram 推送 {pushed} 条 (intensity ≥ {TG_MIN_INTENSITY})")
    return pushed


# ============================================================================
# Phase 4: 自动模拟仓 (仅钻石信号开仓, 跟踪真实收益曲线)
# 只读 alerts + 公共 ticker 价, 不真实下单. 每 60s 检查 SL/TP 命中.
# ============================================================================

def _load_current_regime() -> dict:
    """读 regime.json 快照, 取开仓时刻的 macro state. 失败返回空 dict (字段为 None).
    仅用于打 tag 复盘, 绝不参与开仓决策, 失败必须不影响交易逻辑."""
    try:
        data = json.loads(REGIME_FILE.read_text(encoding="utf-8"))
        return {
            "regime":            data.get("regime") or None,
            "regime_zh":         data.get("regime_zh") or None,
            "regime_confidence": data.get("confidence"),
        }
    except Exception:
        return {"regime": None, "regime_zh": None, "regime_confidence": None}


def _load_paper_state() -> dict:
    if not PAPER_STATE.exists():
        return {"open_trades": [], "closed_trades": []}
    try:
        data = json.loads(PAPER_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"open_trades": [], "closed_trades": []}
        data.setdefault("open_trades", [])
        data.setdefault("closed_trades", [])
        return data
    except Exception:
        return {"open_trades": [], "closed_trades": []}


def _save_paper_state(state: dict) -> None:
    try:
        PAPER_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PAPER_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PAPER_STATE)
    except Exception as e:
        _log(f"paper_state save failed: {e}")


def _get_last_close_for_symbol(state: dict, symbol: str) -> Optional[datetime]:
    """返回该 symbol 最近一次 closed_at (UTC datetime), 没有则 None.
    Phase 1.1 同 symbol 冷却用. O(N) 扫描 closed_trades, N=1000 时仍亚毫秒."""
    latest = None
    for t in state.get("closed_trades", []):
        if t.get("symbol") != symbol:
            continue
        ca = t.get("closed_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            if latest is None or dt > latest:
                latest = dt
        except Exception:
            continue
    return latest


def _count_recent_consecutive_sl(
    state: dict, symbol: str, now: datetime, window_hours: float,
) -> tuple:
    """统计该 symbol 在过去 window_hours 内的连续 SL 次数 (从最近向前).
    一旦遇到非 SL 出场 (win / be / trail / timeout) 计数中断.

    Phase 1.2 用. Returns (count, last_sl_at | None).
    """
    cutoff = now - timedelta(hours=window_hours)
    # 该 symbol 所有平仓单, 按 closed_at 倒序
    matches = []
    for t in state.get("closed_trades", []):
        if t.get("symbol") != symbol:
            continue
        ca = t.get("closed_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            matches.append((dt, t))
        except Exception:
            continue
    matches.sort(key=lambda x: x[0], reverse=True)

    count = 0
    last_sl_at = None
    for dt, t in matches:
        if dt < cutoff:
            break  # 出窗口, 不再回看
        if t.get("close_reason") == "hit_sl":
            count += 1
            if last_sl_at is None:
                last_sl_at = dt
        else:
            break  # 非 SL 出场 → 连续中断
    return count, last_sl_at


def _compute_free_capital(state: dict) -> float:
    """账户可用资金 = 起始 + Σ realized P&L - Σ open notional.
    open trade 时使用此函数预算是否够本金开新仓.
    """
    starting = PAPER_STARTING_CAPITAL_USDT
    closed_pnl = sum(_trade_usdt_pnl(t) for t in state.get("closed_trades", []))
    allocated = sum(float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
                    for t in state.get("open_trades", []))
    return starting + closed_pnl - allocated


def _open_paper_trade(a: VelocityAlert, state: dict, now: datetime,
                      free_capital: float,
                      winrate_summary: Optional[dict] = None) -> Optional[dict]:
    """钻石信号 → 开模拟仓. 返回 trade dict (None 表示跳过).
    资金检查: 若 free_capital < PAPER_NOTIONAL_PER_TRADE_USDT 则拒开.
    初始 Phase A: SL = entry ± 1×ATR, TP1 = entry ± 1.5×ATR, TP2 = entry ± 3×ATR

    Phase 6.B (2026-06-03) 新增 filters (实战亏损反馈):
        A. 历史 30m 胜率 < PAPER_MIN_HIST_WINRATE AND N >= PAPER_MIN_HIST_SAMPLE → reject
        B. SL distance < PAPER_MIN_SL_DISTANCE_PCT → reject

    winrate_summary: 由 caller (_run_paper_trading) 传入, 用于 Tier 1A 过滤.
        None 时跳过 winrate filter (向后兼容老 callers / 测试).
    """
    if a.conviction_tier != PAPER_MIN_TIER:
        return None
    # Phase 6.G G2 (2026-06-11): Macro event blackout — 跟 live_trader.py 一致行为.
    # 数据驱动: CPI 06-10 单日 paper -$3.13 (小) / live -$40.02 (大), gap $37.
    # 即使 paper 没受重创, 但既然 live blackout 期间不开, paper 也应同步保证
    # apples-to-apples 对账 — 不然以后 paper 在 CPI 期间继续刷数据, gap 会假性扩大.
    # Fail-safe: 任何异常都让 paper 继续开 (跟 live 同款防御逻辑).
    try:
        from macro_calendar import get_blackout_decision
        decision = get_blackout_decision(now=now)
        if decision.get("blocked"):
            _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
                 f"phase_6g_g2 macro blackout (tier={decision.get('tier')}) — "
                 f"{decision.get('reason') or 'high-impact event window'}")
            return None
    except Exception as e:
        # 极保守 — log 但不挡 (跟 live 同款 fail-safe)
        pass
    # Phase 1.3: 极高 ATR (>=2%) 信号 reject — 降低 variance, 接受失去稀有 outlier.
    # 审计 N=142: ATR 2.0-2.5% (n=6) 全亏 avg -2.89%, ATR>=2% 整体 cell 净亏.
    # 100U 实盘期核心目标是 variance 降低, 不是追 outlier.
    if a.atr_pct is not None and a.atr_pct >= PAPER_MAX_ATR_PCT:
        _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
             f"ATR {a.atr_pct:.2f}% >= {PAPER_MAX_ATR_PCT}% (high volatility filter)")
        return None
    if a.suggested_sl is None or a.suggested_tp1 is None or a.suggested_tp2 is None:
        return None

    # Phase 6.B-A: 历史 30m 胜率 filter — 显著负 EV 的 setup 直接 reject
    # 实战触发: BASEDUSDT 历史 12% N=32 → -1.44% 止损 (-$2.15), 类似 setup 应屏蔽
    if winrate_summary and winrate_summary.get("by_key"):
        wkey = f"{a.symbol}|{a.alert_type}|{a.direction}"
        w = winrate_summary["by_key"].get(wkey)
        if w and w.get("stages"):
            s30 = w["stages"].get("30m")
            if s30:
                n = s30.get("n", 0)
                rate = s30.get("win_rate", 0)
                if n >= PAPER_MIN_HIST_SAMPLE and rate < PAPER_MIN_HIST_WINRATE:
                    mu = s30.get("avg_outcome_pct", 0)
                    _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
                         f"Phase 6.B-A 历史 30m 胜率 {rate*100:.0f}% (N={n}, μ{mu:+.2f}%) "
                         f"< {PAPER_MIN_HIST_WINRATE*100:.0f}% — 显著负 EV setup")
                    return None

    # Phase 6.B-B: SL 绝对距离 filter — R 太窄易被 wick 扫
    # 实战触发: DRAMUSDT ATR 0.23%, R 仅 0.23% → 1 个 tick 就 SL, 持仓 0min
    if a.price > 0:
        sl_dist_pct = abs(float(a.suggested_sl) - float(a.price)) / float(a.price) * 100
        if sl_dist_pct < PAPER_MIN_SL_DISTANCE_PCT:
            _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
                 f"Phase 6.B-B SL distance {sl_dist_pct:.2f}% < {PAPER_MIN_SL_DISTANCE_PCT}% "
                 f"— R 太窄易被 wick 扫")
            return None

    # Phase 5.A (5/27): 按 conviction score 决定 notional, 取代固定 $400.
    # score 5: $400 (基准), 6-7: $800 (高 EV 加仓), 8+: $200 (反向证据减仓).
    trade_notional = _notional_for_score(a.conviction_score)
    # 资金检查: 不够开本金就跳过 (最多 5 笔并发或老仓未平时常见)
    if free_capital < trade_notional:
        _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
             f"free_capital ${free_capital:.0f} < score-based notional ${trade_notional:.0f}")
        return None
    # 防御: SL/TP 顺序异常 → 拒开 (避免逻辑 bug 把"止损"开在盈利方向)
    if a.direction == "LONG":
        if not (a.suggested_sl < a.price < a.suggested_tp1 < a.suggested_tp2):
            _log(f"⚠️ {a.symbol} LONG SL/TP 顺序异常, 拒开: "
                 f"SL={a.suggested_sl} entry={a.price} TP1={a.suggested_tp1} TP2={a.suggested_tp2}")
            return None
    elif a.direction == "SHORT":
        if not (a.suggested_sl > a.price > a.suggested_tp1 > a.suggested_tp2):
            _log(f"⚠️ {a.symbol} SHORT SL/TP 顺序异常, 拒开: "
                 f"SL={a.suggested_sl} entry={a.price} TP1={a.suggested_tp1} TP2={a.suggested_tp2}")
            return None
    else:
        return None  # 未知方向
    for t in state["open_trades"]:
        if t.get("symbol") == a.symbol and t.get("direction") == a.direction:
            return None
    # Phase 1.1: 同 symbol 任意 exit 后冷却 (无视方向 / close_reason).
    # 审计 N=118 显示: SIREN/QUSDT/ATA 等抖动重入贡献 ~-10% 噪音损失.
    # 30min 阈值同时保留 SIREN #3 (226min 间隔) 这类真正的反弹机会.
    last_close = _get_last_close_for_symbol(state, a.symbol)
    if last_close is not None:
        elapsed_min = (now - last_close).total_seconds() / 60.0
        if elapsed_min < PAPER_SYMBOL_COOLDOWN_MIN:
            _log(f"[paper] SKIP open {a.symbol} {a.direction}: "
                 f"symbol cooldown {elapsed_min:.0f}min < {PAPER_SYMBOL_COOLDOWN_MIN}min "
                 f"(last_close={last_close.isoformat()})")
            return None
    # Phase 1.2: 同 symbol 在过去 4h 内已经连续 SL >= 2 次 → 冷却 4h 不再开新.
    # 审计: QUSDT 5/5 SL (-11.57%), ATA 4/4 SL (-13.86%), TRUTH 3/3 SL (-9.56%).
    # 这些长尾损失靠 30min cooldown 救不了 (gap 太大), 必须靠 SL streak detection.
    consec_sl, last_sl_at = _count_recent_consecutive_sl(
        state, a.symbol, now, PAPER_CONSEC_SL_WINDOW_HOURS,
    )
    if consec_sl >= PAPER_CONSEC_SL_TRIGGER:
        # 检查最近一次 SL 是否还在冷却期内
        if last_sl_at is not None:
            elapsed_h = (now - last_sl_at).total_seconds() / 3600.0
            if elapsed_h < PAPER_CONSEC_SL_COOLDOWN_HOURS:
                _log(
                    f"[paper] SKIP open {a.symbol} {a.direction}: "
                    f"{consec_sl} consecutive SL in last {PAPER_CONSEC_SL_WINDOW_HOURS}h, "
                    f"cooldown {elapsed_h:.1f}h < {PAPER_CONSEC_SL_COOLDOWN_HOURS}h"
                )
                return None
    regime_snap = _load_current_regime()
    trade = {
        "id": f"{a.symbol}|{a.direction}|{a.detected_at}",
        "symbol": a.symbol,
        "direction": a.direction,
        "alert_type": a.alert_type,
        "intensity": a.intensity,
        "conviction_score": a.conviction_score,
        "atr_pct": a.atr_pct,
        "entry_price": a.price,
        "sl":  a.suggested_sl,
        "tp1": a.suggested_tp1,
        "tp2": a.suggested_tp2,
        # Phase 6.C: 原始 1R 距离 (entry-to-original-SL). SL 后续可能被 6.B-C/6.C
        # breakeven 移动, 但 initial_r 永久保留, 供 profit_r 计算用. abs() 防方向.
        "initial_r": abs(float(a.price) - float(a.suggested_sl)),
        "entered_at": now.isoformat(),
        "current_price": a.price,
        "unrealized_pnl_pct": 0.0,
        # Phase 4 资金跟踪 (Phase 5.A 改为 score-based)
        "notional_usdt": trade_notional,
        "unrealized_usdt_pnl": 0.0,
        # 上下文 (复盘用)
        "funding_rate_pct": a.funding_rate_pct,
        "oi_delta_5m_pct": a.oi_delta_5m_pct,
        "taker_buy_ratio_1m": a.taker_buy_ratio_1m,
        "change_1h_pct": a.change_1h_pct,
        # Phase 4.Z 大户/散户多空比 (数据采集期, 不进 scoring)
        "top_trader_position_ratio": a.top_trader_position_ratio,
        "top_trader_account_ratio":  a.top_trader_account_ratio,
        "global_account_ratio":      a.global_account_ratio,
        # Regime 快照 (开仓时, 不影响决策, 仅 regime×胜率 切片复盘用)
        "regime_at_open":            regime_snap["regime"],
        "regime_zh_at_open":         regime_snap["regime_zh"],
        "regime_confidence_at_open": regime_snap["regime_confidence"],
        # ===== Phase 4 动态 SL/TP 状态机 =====
        "phase": "A",                            # A=初始 / B=TP1后BE / C=TP2后trailing
        "tp1_hit_at": None,                      # ISO ts when TP1 触发
        "tp2_hit_at": None,                      # ISO ts when TP2 触发
        "high_water_mark": a.price,              # 持仓期间最高价(LONG)/最低价(SHORT)
        "trailing_sl": None,                     # Phase C 跟踪止损 (只升不降棘轮)
        # ===== Per-phase MFE (Maximum Favorable Excursion) 复盘用 =====
        # 记录每个 phase 期间到达的最佳价 + 方向化 % from entry
        # 比如 "Phase B 最高摸到 +9% 但 BE 关 0R" → 显示 +9% regret
        "phase_a_mfe_price": a.price,
        "phase_a_mfe_pct":   0.0,
        "phase_b_mfe_price": None,
        "phase_b_mfe_pct":   None,
        "phase_c_mfe_price": None,
        "phase_c_mfe_pct":   None,
    }
    state["open_trades"].append(trade)
    return trade


def _update_paper_trades(state: dict, prices: dict, now: datetime) -> tuple:
    """3 阶段动态 SL/TP 状态机.
    Phase A: 初始 SL/TP, 命中 TP1 → 进 B (移 SL 到 BE)
    Phase B: SL=BE, 命中 TP2 → 进 C (启动 trailing); 触 BE SL → 关 0R
    Phase C: trailing SL = HWM ∓ 2×ATR (棘轮只升); 触 trailing → 关跟踪止盈
    返回 (newly_closed_count, list_of_closed_trades, list_of_phase_transitions).
    phase_transitions 用于 Telegram 中途通知.
    """
    closed_now = []
    phase_transitions: List[dict] = []
    still_open = []
    for t in state["open_trades"]:
        cur = prices.get(t["symbol"])
        if cur is None:
            still_open.append(t)
            continue
        t["current_price"] = cur
        entry = float(t["entry_price"])
        if entry <= 0:
            still_open.append(t)
            continue
        is_long = (t["direction"] == "LONG")

        # 维护 high water mark (LONG 最高价, SHORT 最低价 — 跟踪 favorable 方向)
        hwm = float(t.get("high_water_mark", entry))
        if is_long:
            if cur > hwm: hwm = cur
        else:
            if cur < hwm: hwm = cur
        t["high_water_mark"] = hwm

        # ===== Per-phase MFE: 更新当前 phase 的最佳价 =====
        # 比如 trade 在 Phase B 时, 这里只更新 phase_b_mfe_*
        phase = t.get("phase", "A")
        mfe_pkey = f"phase_{phase.lower()}_mfe_price"
        mfe_qkey = f"phase_{phase.lower()}_mfe_pct"
        cur_mfe = t.get(mfe_pkey)
        # 老 trade 没这字段时初始化为 entry
        if cur_mfe is None:
            cur_mfe = entry
        update_mfe = False
        if is_long and cur > cur_mfe:
            update_mfe = True
        elif (not is_long) and cur < cur_mfe:
            update_mfe = True
        if update_mfe:
            t[mfe_pkey] = cur
            raw = (cur - entry) / entry * 100
            t[mfe_qkey] = round(raw if is_long else -raw, 3)

        # 方向化 unrealized PnL (% + USDT)
        raw_pct = (cur - entry) / entry * 100
        pnl_pct = raw_pct if is_long else -raw_pct
        t["unrealized_pnl_pct"] = round(pnl_pct, 3)
        notional = float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
        t["unrealized_usdt_pnl"] = round(notional * pnl_pct / 100.0, 2)

        # 持仓时长
        try:
            entered = datetime.fromisoformat(t["entered_at"].replace("Z", "+00:00"))
            hold_min = (now - entered).total_seconds() / 60.0
        except Exception:
            hold_min = 0.0

        close_reason = None
        close_price = None
        phase = t.get("phase", "A")
        atr_pct = t.get("atr_pct") or 0.5

        # ===== 状态机 =====
        if phase == "A":
            # Phase 6.B-C / 6.C-A: 浮盈分级保护 (Phase A 内, 不转 Phase B).
            # 用 _profit_milestone 字段记录已触发的最高里程碑, 保证只前进不后退:
            #   0.8R 触发 → SL 移到 entry ± 0.2R (Phase 6.C-A 中间保护, 仍允许小亏)
            #   1.0R 触发 → SL 移到 entry (Phase 6.B-C BE, 最差 0R)
            # initial_r 在开仓时存档 (Phase 6.C), SL 移动后仍可正确算 profit_r.
            try:
                init_r = _initial_r_distance(t, entry)
                if init_r > 0:
                    cur_profit = (cur - entry) if is_long else (entry - cur)
                    profit_r = cur_profit / init_r
                    current_milestone = float(t.get("_profit_milestone") or 0.0)
                    # Phase 6.C 部署迁移 (paranoid H1): 老 trade (Phase 6.B-C 时代开仓)
                    # 没有 _profit_milestone 字段但已经触发 _breakeven_shifted=True →
                    # 视为已到 1.0R milestone, 防止 0.8R 分支把已经收紧到 entry 的 SL
                    # 重新 loosen 到 entry ± 0.2R (实战安全回退).
                    if current_milestone == 0.0 and t.get("_breakeven_shifted"):
                        current_milestone = PAPER_BREAKEVEN_PROFIT_R
                    # 检查 1.0R BE shift (最高级, 优先). 用 epsilon 容差防 FP 失之毫厘.
                    if profit_r + PAPER_MILESTONE_EPSILON >= PAPER_BREAKEVEN_PROFIT_R and current_milestone < PAPER_BREAKEVEN_PROFIT_R:
                        old_sl = t["sl"]
                        t["sl"] = entry
                        t["_profit_milestone"] = PAPER_BREAKEVEN_PROFIT_R
                        t["_breakeven_shifted"] = True   # backward compat (Phase 6.B 字段)
                        t["_breakeven_shifted_at"] = now.isoformat()
                        _log(
                            f"[paper] {t['symbol']} {t['direction']} 浮盈达 "
                            f"{profit_r:.2f}R → SL {old_sl} → entry {entry} "
                            f"(Phase 6.B-C breakeven lock, milestone=1.0R)"
                        )
                    # 否则检查 0.8R 中间保护 (仅在还没到 0.8 时触发). epsilon 容差防 FP.
                    elif profit_r + PAPER_MILESTONE_EPSILON >= PAPER_PROFIT_PROTECT_R and current_milestone < PAPER_PROFIT_PROTECT_R:
                        # SL 移到 entry - buffer_r (LONG) / entry + buffer_r (SHORT)
                        buffer_dist = PAPER_PROTECT_BUFFER_R * init_r
                        new_sl = (entry - buffer_dist) if is_long else (entry + buffer_dist)
                        old_sl = t["sl"]
                        t["sl"] = new_sl
                        t["_profit_milestone"] = PAPER_PROFIT_PROTECT_R
                        t["_profit_protect_at"] = now.isoformat()
                        _log(
                            f"[paper] {t['symbol']} {t['direction']} 浮盈达 "
                            f"{profit_r:.2f}R → SL {old_sl} → {new_sl} "
                            f"(Phase 6.C-A 0.8R 保护, 最差亏 0.2R)"
                        )
            except (TypeError, ValueError, ZeroDivisionError):
                pass

            # 初始阶段: 检查 SL → TP1 (TP2 还远, 不直接跳到 C)
            if is_long:
                if cur <= t["sl"]:
                    close_reason = "hit_sl"; close_price = t["sl"]
                elif cur >= t["tp1"]:
                    # TP1 触发 → 进 Phase B, 不关仓!! SL 移到 entry (BE)
                    t["phase"] = "B"
                    t["tp1_hit_at"] = now.isoformat()
                    t["sl"] = entry  # BE
                    # 初始化 Phase B MFE 起点 = 当前价 (TP1 trigger price)
                    t["phase_b_mfe_price"] = cur
                    t["phase_b_mfe_pct"]   = round((cur - entry) / entry * 100, 3)
                    # Phase 5.C: B 组在 TP1 触发时锁 50% 利润, 剩 50% 继续 trailing
                    _apply_tp1_partial_close(t, cur, entry, is_long=True)
                    phase_transitions.append({
                        "type": "tp1", "trade": t.copy(), "old_sl_pct": -atr_pct,
                    })
            else:  # SHORT
                if cur >= t["sl"]:
                    close_reason = "hit_sl"; close_price = t["sl"]
                elif cur <= t["tp1"]:
                    t["phase"] = "B"
                    t["tp1_hit_at"] = now.isoformat()
                    t["sl"] = entry
                    # 初始化 Phase B MFE (SHORT 取反向 %)
                    t["phase_b_mfe_price"] = cur
                    t["phase_b_mfe_pct"]   = round((entry - cur) / entry * 100, 3)
                    # Phase 5.C: SHORT 同 LONG, B 组在 TP1 锁 50% 利润
                    _apply_tp1_partial_close(t, cur, entry, is_long=False)
                    phase_transitions.append({
                        "type": "tp1", "trade": t.copy(), "old_sl_pct": -atr_pct,
                    })

        elif phase == "B":
            # TP1 后 trailing (新设计): SL = max/min(entry, HWM ∓ 1.5×ATR×vol_mult)
            # 旧设计 SL 固定 BE → 价回吐到 entry 才关 0R, 中段利润全错失
            # 新设计: HWM 升 → SL 棘轮跟着升, floor=entry 保证最差仍 BE
            vol_mult = float(t.get("vol_mult_used", 1.0) or 1.0)
            trail_pct = 1.5 * atr_pct * vol_mult   # 1.5×ATR (跟 Phase C 同款)
            if is_long:
                # 计算新 trailing SL (永不低于 entry)
                new_sl = max(entry, hwm * (1 - trail_pct / 100.0))
                # 棘轮: LONG SL 只升不降
                if new_sl > float(t["sl"]):
                    t["sl"] = new_sl
                if cur <= float(t["sl"]):
                    # 区分关仓原因: SL≈entry → BE; SL > entry → trailing 锁利
                    if float(t["sl"]) <= entry * 1.0001:
                        close_reason = "hit_be_sl"
                    else:
                        close_reason = "hit_b_trail"
                    close_price = float(t["sl"])
                elif cur >= t["tp2"]:
                    # TP2 触发 → 进 Phase C, 不关仓!! 启动 Phase C trailing
                    t["phase"] = "C"
                    t["tp2_hit_at"] = now.isoformat()
                    base_floor = float(t["tp1"])
                    hwm_trail  = hwm * (1 - 2 * atr_pct / 100.0)
                    t["trailing_sl"] = max(base_floor, hwm_trail)
                    t["phase_c_mfe_price"] = cur
                    t["phase_c_mfe_pct"]   = round((cur - entry) / entry * 100, 3)
                    phase_transitions.append({"type": "tp2", "trade": t.copy()})
            else:  # SHORT
                new_sl = min(entry, hwm * (1 + trail_pct / 100.0))
                # 棘轮: SHORT SL 只降不升
                if new_sl < float(t["sl"]):
                    t["sl"] = new_sl
                if cur >= float(t["sl"]):
                    if float(t["sl"]) >= entry * 0.9999:
                        close_reason = "hit_be_sl"
                    else:
                        close_reason = "hit_b_trail"
                    close_price = float(t["sl"])
                elif cur <= t["tp2"]:
                    t["phase"] = "C"
                    t["tp2_hit_at"] = now.isoformat()
                    base_ceil = float(t["tp1"])
                    hwm_trail = hwm * (1 + 2 * atr_pct / 100.0)
                    t["trailing_sl"] = min(base_ceil, hwm_trail)
                    t["phase_c_mfe_price"] = cur
                    t["phase_c_mfe_pct"]   = round((entry - cur) / entry * 100, 3)
                    phase_transitions.append({"type": "tp2", "trade": t.copy()})

        elif phase == "C":
            # TP2 后跟踪止盈: trailing SL 棘轮 + TP1 安全地板
            if is_long:
                base_floor = float(t["tp1"])
                hwm_trail  = hwm * (1 - 2 * atr_pct / 100.0)
                new_trail  = max(base_floor, hwm_trail)
                # 棘轮: 只朝有利方向更新
                if t.get("trailing_sl") is None or new_trail > t["trailing_sl"]:
                    t["trailing_sl"] = new_trail
                if cur <= t["trailing_sl"]:
                    close_reason = "hit_trail"; close_price = t["trailing_sl"]
            else:  # SHORT
                base_ceil = float(t["tp1"])
                hwm_trail = hwm * (1 + 2 * atr_pct / 100.0)
                new_trail = min(base_ceil, hwm_trail)
                if t.get("trailing_sl") is None or new_trail < t["trailing_sl"]:
                    t["trailing_sl"] = new_trail
                if cur >= t["trailing_sl"]:
                    close_reason = "hit_trail"; close_price = t["trailing_sl"]

        # 任何 phase: 4h timeout 兜底
        if not close_reason and hold_min >= PAPER_AUTO_CLOSE_HOURS * 60:
            close_reason = "timeout"; close_price = cur

        if close_reason:
            # 毛盈亏 (price diff only, 跟 Binance 显示的 unrealized 一致)
            realized_raw = (close_price - entry) / entry * 100
            gross_pct = realized_raw if is_long else -realized_raw
            # 手续费 (taker open + taker close round-trip)
            fee_pct = PAPER_FEE_PCT_ROUND_TRIP
            # 净盈亏 (扣手续费后的真实落袋)
            net_pct = gross_pct - fee_pct
            notional = float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
            t["closed_at"] = now.isoformat()
            t["close_price"] = close_price
            t["close_reason"] = close_reason
            t["gross_pnl_pct"]   = round(gross_pct, 3)
            t["fee_pct"]         = fee_pct
            t["fee_usdt"]        = round(notional * fee_pct / 100.0, 2)
            t["realized_pnl_pct"] = round(net_pct, 3)   # 改义: 现在存的是 NET
            # Phase 5.C: 若 B 组在 TP1 已锁 50% 利润, 加回到最终 realized.
            # notional 已减半, 这里 net_pct × 半仓 = 剩 50% 部分的 PnL.
            tp1_locked = float(t.get("tp1_locked_pnl_usdt") or 0)
            t["realized_usdt_pnl"]= round(notional * net_pct / 100.0 + tp1_locked, 2)
            t["hold_time_min"] = round(hold_min, 1)
            state["closed_trades"].append(t)
            closed_now.append(t)
        else:
            still_open.append(t)

    state["open_trades"] = still_open
    return len(closed_now), closed_now, phase_transitions


def _trade_net_pct(t: dict) -> float:
    """返回 NET pct (扣手续费后).
    新 trade (有 fee_pct 字段): realized_pnl_pct 已是 net, 直接返回.
    老 trade (无 fee_pct 字段): realized_pnl_pct 是 gross, 追溯扣 PAPER_FEE_PCT_ROUND_TRIP.
    """
    pct = float(t.get("realized_pnl_pct", 0.0))
    if "fee_pct" not in t:
        pct -= PAPER_FEE_PCT_ROUND_TRIP   # 老 trade 追溯扣费
    return round(pct, 3)


def _trade_usdt_pnl(t: dict) -> float:
    """返回 NET USDT 盈亏 (扣手续费后).
    新 trade: realized_usdt_pnl 已是 net, 直接返回.
    老 trade: 用 _trade_net_pct 重算 (gross 扣费 → net) × notional.
    """
    if "fee_pct" in t and "realized_usdt_pnl" in t and t["realized_usdt_pnl"] is not None:
        return float(t["realized_usdt_pnl"])
    notional = float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
    net_pct = _trade_net_pct(t)
    return round(notional * net_pct / 100.0, 2)


def _trade_fee_usdt(t: dict) -> float:
    """返回该 trade 支付的手续费 USDT (新 trade 取存储, 老 trade 按 PAPER_FEE_PCT 估算)."""
    if "fee_usdt" in t and t["fee_usdt"] is not None:
        return float(t["fee_usdt"])
    notional = float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
    return round(notional * PAPER_FEE_PCT_ROUND_TRIP / 100.0, 2)


def _compute_paper_stats(state: dict) -> dict:
    """胜率口径: BE 平仓 (≤0.1% 净) 算 scratch (不计胜/负). 这是行业惯例,
    避免 BE 被误算成 loss 拉低胜率.
    资金口径: 起始 PAPER_STARTING_CAPITAL_USDT, 每笔固定 notional.
    """
    open_n = len(state.get("open_trades", []))
    closed = state.get("closed_trades", [])
    closed_n = len(closed)
    starting_capital = PAPER_STARTING_CAPITAL_USDT
    # 已分配资金 (任何 phase 都计入)
    allocated = round(sum(float(t.get("notional_usdt", PAPER_NOTIONAL_PER_TRADE_USDT))
                          for t in state.get("open_trades", [])), 2)
    max_concurrent_slots = int(starting_capital // PAPER_NOTIONAL_PER_TRADE_USDT)
    if closed_n == 0:
        return {
            "total_trades": open_n, "open": open_n, "closed": 0,
            "wins": 0, "losses": 0, "scratches": 0,
            "win_rate": None,
            "total_pnl_pct": 0.0, "avg_pnl_pct": None,
            "best_trade": None, "worst_trade": None,
            "by_outcome": {},
            # 资金视图
            "starting_capital_usdt": starting_capital,
            "notional_per_trade_usdt": PAPER_NOTIONAL_PER_TRADE_USDT,
            "current_balance_usdt": starting_capital,
            "allocated_usdt": allocated,
            "free_capital_usdt": round(starting_capital - allocated, 2),
            "max_concurrent_slots": max_concurrent_slots,
            "slots_used": open_n,
            "total_usdt_pnl": 0.0,
            "avg_usdt_pnl": None,
            "best_trade_usdt": None,
            "worst_trade_usdt": None,
            "roi_pct": 0.0,
            "total_fees_usdt": 0.0,
            "fee_pct_per_trade": PAPER_FEE_PCT_ROUND_TRIP,
        }
    BE_EPSILON = 0.1
    # 使用 NET pct (扣手续费) 做所有判断 — 老 trade 通过 _trade_net_pct 追溯扣费
    net_pcts = [_trade_net_pct(t) for t in closed]
    wins      = sum(1 for v in net_pcts if v >  BE_EPSILON)
    losses    = sum(1 for v in net_pcts if v < -BE_EPSILON)
    scratches = closed_n - wins - losses
    usdt_pnls = [_trade_usdt_pnl(t) for t in closed]
    fee_usdts = [_trade_fee_usdt(t) for t in closed]
    by_outcome: dict = {}
    for t in closed:
        reason = t.get("close_reason", "?")
        by_outcome.setdefault(reason, 0)
        by_outcome[reason] += 1
    decisive = wins + losses
    win_rate = round(wins / decisive, 3) if decisive > 0 else None
    total_usdt = round(sum(usdt_pnls), 2)
    total_fees_usdt = round(sum(fee_usdts), 2)
    current_balance = round(starting_capital + total_usdt, 2)
    free_capital = round(current_balance - allocated, 2)
    return {
        "total_trades": open_n + closed_n,
        "open": open_n,
        "closed": closed_n,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "win_rate": win_rate,
        "total_pnl_pct": round(sum(net_pcts), 2),
        "avg_pnl_pct": round(sum(net_pcts) / closed_n, 3),
        "best_trade": round(max(net_pcts), 2),
        "worst_trade": round(min(net_pcts), 2),
        "by_outcome": by_outcome,
        # 资金视图
        "starting_capital_usdt": starting_capital,
        "notional_per_trade_usdt": PAPER_NOTIONAL_PER_TRADE_USDT,
        "current_balance_usdt": current_balance,
        "allocated_usdt": allocated,
        "free_capital_usdt": free_capital,
        "max_concurrent_slots": max_concurrent_slots,
        "slots_used": open_n,
        "total_usdt_pnl": total_usdt,        # NET (扣手续费后)
        "avg_usdt_pnl": round(total_usdt / closed_n, 2),
        "best_trade_usdt": round(max(usdt_pnls), 2),
        "worst_trade_usdt": round(min(usdt_pnls), 2),
        "roi_pct": round(total_usdt / starting_capital * 100, 2),
        # 手续费视图
        "total_fees_usdt": total_fees_usdt,
        "fee_pct_per_trade": PAPER_FEE_PCT_ROUND_TRIP,
    }


def _enrich_trade_for_publish(t: dict) -> dict:
    """对外发布前给 trade 补字段 (老数据可能缺). 不改原 state.
    重要: 老 closed trade 没 fee_pct → realized_pnl_pct 是 gross
    → 这里追溯扣手续费, 让看板/邮件等始终显示 NET (诚实数字).
    """
    out = dict(t)
    if "notional_usdt" not in out:
        out["notional_usdt"] = PAPER_NOTIONAL_PER_TRADE_USDT

    # ===== 老 closed trade: 追溯扣手续费, gross → net =====
    # 判定: 已 closed (有 close_reason) 且 无 fee_pct 字段 = legacy
    is_closed_legacy = (
        "close_reason" in out
        and "fee_pct" not in out
        and "realized_pnl_pct" in out
    )
    if is_closed_legacy:
        gross_pct = float(out["realized_pnl_pct"])
        out["gross_pnl_pct"] = round(gross_pct, 3)
        out["fee_pct"]       = PAPER_FEE_PCT_ROUND_TRIP
        net_pct = gross_pct - PAPER_FEE_PCT_ROUND_TRIP
        out["realized_pnl_pct"]  = round(net_pct, 3)   # 改回 net 供发布
        notional = float(out["notional_usdt"])
        out["realized_usdt_pnl"] = round(notional * net_pct / 100.0, 2)
        out["fee_usdt"]          = round(notional * PAPER_FEE_PCT_ROUND_TRIP / 100.0, 2)

    # 新数据 / open trade 兜底补 USDT 字段
    if "realized_pnl_pct" in out and "realized_usdt_pnl" not in out:
        out["realized_usdt_pnl"] = round(
            float(out["notional_usdt"]) * float(out["realized_pnl_pct"]) / 100.0, 2
        )
    if "unrealized_pnl_pct" in out and "unrealized_usdt_pnl" not in out:
        out["unrealized_usdt_pnl"] = round(
            float(out["notional_usdt"]) * float(out["unrealized_pnl_pct"]) / 100.0, 2
        )
    return out


def _save_paper_history(state: dict, stats: dict) -> None:
    """对外发布的 view: stats + open + 全部 closed.
    全量发布以支持任意日期复盘 (N=200 时 ~30KB, N=1000 时 ~150KB 也可接受)."""
    closed = state.get("closed_trades", [])
    closed_sorted = sorted(closed, key=lambda t: t.get("closed_at", ""), reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "open_trades": [_enrich_trade_for_publish(t) for t in state.get("open_trades", [])],
        "recent_closed": [_enrich_trade_for_publish(t) for t in closed_sorted],
        "auto_close_hours": PAPER_AUTO_CLOSE_HOURS,
        "min_tier": PAPER_MIN_TIER,
    }
    try:
        PAPER_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = PAPER_HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PAPER_HISTORY)
    except Exception as e:
        _log(f"paper_history save failed: {e}")


# ============================================================================
# Phase 4 Shadow: premium 信号影子追踪 (不开真仓, 但模拟跟踪供数据评估)
# ============================================================================

def _load_shadow_state() -> dict:
    if not PAPER_SHADOW_STATE.exists():
        return {"open_trades": [], "closed_trades": []}
    try:
        data = json.loads(PAPER_SHADOW_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"open_trades": [], "closed_trades": []}
        data.setdefault("open_trades", [])
        data.setdefault("closed_trades", [])
        return data
    except Exception:
        return {"open_trades": [], "closed_trades": []}


def _save_shadow_state(state: dict) -> None:
    try:
        PAPER_SHADOW_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PAPER_SHADOW_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PAPER_SHADOW_STATE)
    except Exception as e:
        _log(f"shadow_state save failed: {e}")


def _open_shadow_trade(a: VelocityAlert, state: dict, now: datetime) -> Optional[dict]:
    """premium 信号 → 开 shadow 仓 (不占用真实资金, 仅记录).
    保持跟真 paper 相同的 SL/TP 状态机, 便于后续直接对比'如果跟了会赚多少'.
    """
    if a.conviction_tier not in PAPER_SHADOW_TIERS:
        return None
    if a.suggested_sl is None or a.suggested_tp1 is None or a.suggested_tp2 is None:
        return None
    # 同方向校验 (跟真 paper 一样)
    if a.direction == "LONG":
        if not (a.suggested_sl < a.price < a.suggested_tp1 < a.suggested_tp2):
            return None
    elif a.direction == "SHORT":
        if not (a.suggested_sl > a.price > a.suggested_tp1 > a.suggested_tp2):
            return None
    else:
        return None
    # 同 symbol+direction 已开 → 跳过 (避免短时重复)
    for t in state["open_trades"]:
        if t.get("symbol") == a.symbol and t.get("direction") == a.direction:
            return None
    regime_snap = _load_current_regime()
    trade = {
        "id": f"shadow|{a.symbol}|{a.direction}|{a.detected_at}",
        "shadow": True,                # 标记 shadow, 不混入真 paper 统计
        "symbol": a.symbol,
        "direction": a.direction,
        "alert_type": a.alert_type,
        "intensity": a.intensity,
        "conviction_score": a.conviction_score,
        "conviction_tier": a.conviction_tier,  # premium / etc
        "atr_pct": a.atr_pct,
        "range_4h_pct": a.range_4h_pct,
        "vol_mult_used": a.vol_mult_used,
        "entry_price": a.price,
        "sl":  a.suggested_sl,
        "tp1": a.suggested_tp1,
        "tp2": a.suggested_tp2,
        # Phase 6.C: 原始 1R 距离 (entry-to-original-SL). SL 后续可能被 6.B-C/6.C
        # breakeven 移动, 但 initial_r 永久保留, 供 profit_r 计算用. abs() 防方向.
        "initial_r": abs(float(a.price) - float(a.suggested_sl)),
        "entered_at": now.isoformat(),
        "current_price": a.price,
        "unrealized_pnl_pct": 0.0,
        "notional_usdt": PAPER_SHADOW_NOTIONAL_HYPOTHETICAL,  # 仅供 P&L 计算, 不真扣
        "unrealized_usdt_pnl": 0.0,
        # 上下文 (供后续分析 'OI 匹配 vs 不匹配' 等子类型差异)
        "funding_rate_pct": a.funding_rate_pct,
        "oi_delta_5m_pct": a.oi_delta_5m_pct,
        "oi_matches_direction": (
            (a.direction == "LONG" and (a.oi_delta_5m_pct or 0) > 0) or
            (a.direction == "SHORT" and (a.oi_delta_5m_pct or 0) < 0)
        ) if a.oi_delta_5m_pct is not None else None,
        "taker_buy_ratio_1m": a.taker_buy_ratio_1m,
        "change_1h_pct": a.change_1h_pct,
        "change_4h_pct": a.change_4h_pct,
        # Phase 4.Z 大户/散户多空比 (shadow 也采集, 数据维度一致便于比对)
        "top_trader_position_ratio": a.top_trader_position_ratio,
        "top_trader_account_ratio":  a.top_trader_account_ratio,
        "global_account_ratio":      a.global_account_ratio,
        # Regime 快照 (开仓时, 不影响决策, 仅 regime×胜率 切片复盘用)
        "regime_at_open":            regime_snap["regime"],
        "regime_zh_at_open":         regime_snap["regime_zh"],
        "regime_confidence_at_open": regime_snap["regime_confidence"],
        # Phase 状态机字段 (跟真 paper 同 schema, 让 _update_paper_trades 直接复用)
        "phase": "A",
        "tp1_hit_at": None,
        "tp2_hit_at": None,
        "high_water_mark": a.price,
        "trailing_sl": None,
    }
    state["open_trades"].append(trade)
    return trade


def _compute_shadow_stats(state: dict) -> dict:
    """shadow 专用统计: hypothetical ROI + 自动 verdict + OI 子类型对比."""
    open_n = len(state.get("open_trades", []))
    closed = state.get("closed_trades", [])
    closed_n = len(closed)
    notional = PAPER_SHADOW_NOTIONAL_HYPOTHETICAL

    if closed_n == 0:
        return {
            "total_trades": open_n + closed_n,
            "open": open_n, "closed": 0,
            "wins": 0, "losses": 0, "scratches": 0,
            "win_rate": None,
            "avg_pnl_pct": None, "total_usdt_pnl": 0.0,
            "verdict": "📊 数据不足 — 等待 premium 信号触发",
            "verdict_class": "neutral",
            "hypothetical_notional_usdt": notional,
            "by_outcome": {},
            "oi_subset": {
                "oi_match":    {"n": 0, "wins": 0, "win_rate": None, "avg_pct": None},
                "oi_mismatch": {"n": 0, "wins": 0, "win_rate": None, "avg_pct": None},
                "no_oi_data":  {"n": 0, "wins": 0, "win_rate": None, "avg_pct": None},
            },
        }

    BE_EPSILON = 0.1
    # Shadow 也用 NET pct (跟真 paper 一致, 公平评估 EV)
    net_pcts  = [_trade_net_pct(t) for t in closed]
    wins      = sum(1 for v in net_pcts if v >  BE_EPSILON)
    losses    = sum(1 for v in net_pcts if v < -BE_EPSILON)
    scratches = closed_n - wins - losses
    pnls = net_pcts
    usdt_pnls = [round(notional * v / 100.0, 2) for v in net_pcts]
    decisive = wins + losses
    win_rate = round(wins / decisive, 3) if decisive > 0 else None
    avg_pnl = round(sum(pnls) / closed_n, 3)
    total_usdt = round(sum(usdt_pnls), 2)

    # by_outcome 分布
    by_outcome: dict = {}
    for t in closed:
        reason = t.get("close_reason", "?")
        by_outcome[reason] = by_outcome.get(reason, 0) + 1

    # OI 子类型分析: 哪些 premium 是 OI 匹配 vs 不匹配
    oi_subset = {
        "oi_match":    {"n": 0, "wins": 0, "sum_pct": 0.0},
        "oi_mismatch": {"n": 0, "wins": 0, "sum_pct": 0.0},
        "no_oi_data":  {"n": 0, "wins": 0, "sum_pct": 0.0},
    }
    for t in closed:
        oim = t.get("oi_matches_direction")
        bucket = "oi_match" if oim is True else ("oi_mismatch" if oim is False else "no_oi_data")
        net = _trade_net_pct(t)   # NET pct (扣手续费后)
        oi_subset[bucket]["n"] += 1
        oi_subset[bucket]["sum_pct"] += net
        if net > BE_EPSILON:
            oi_subset[bucket]["wins"] += 1
    for k in oi_subset:
        n = oi_subset[k]["n"]
        if n > 0:
            oi_subset[k]["win_rate"] = round(oi_subset[k]["wins"] / n, 3)
            oi_subset[k]["avg_pct"]  = round(oi_subset[k]["sum_pct"] / n, 3)
        else:
            oi_subset[k]["win_rate"] = None
            oi_subset[k]["avg_pct"]  = None
        del oi_subset[k]["sum_pct"]   # 内部用,不发布

    # ===== 自动 verdict (基于数据下结论, 不靠主观) =====
    if closed_n < PAPER_SHADOW_VERDICT_MIN_N:
        verdict = f"📊 数据不足 (N={closed_n} < {PAPER_SHADOW_VERDICT_MIN_N}) — 继续观察"
        verdict_cls = "neutral"
    elif avg_pnl >= 0.3 and (win_rate or 0) >= 0.5:
        verdict = f"✅ 显示正向 edge (μ {avg_pnl:+.2f}% / 胜率 {int((win_rate or 0)*100)}%) — 可以考虑启用 premium 自动开仓"
        verdict_cls = "positive"
    elif avg_pnl <= -0.3 or (win_rate or 0) < 0.35:
        verdict = f"❌ 负向 EV (μ {avg_pnl:+.2f}% / 胜率 {int((win_rate or 0)*100)}%) — 保持 shadow 不开真仓"
        verdict_cls = "negative"
    else:
        verdict = f"⚖️ 中性 (μ {avg_pnl:+.2f}% / 胜率 {int((win_rate or 0)*100)}%) — 继续观察"
        verdict_cls = "neutral"

    return {
        "total_trades": open_n + closed_n,
        "open": open_n,
        "closed": closed_n,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl,
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "total_usdt_pnl": total_usdt,
        "verdict": verdict,
        "verdict_class": verdict_cls,
        "hypothetical_notional_usdt": notional,
        "by_outcome": by_outcome,
        "oi_subset": oi_subset,
    }


def _save_shadow_history(state: dict, stats: dict) -> None:
    """发布到 paper_shadow_history.json 供看板 fetch."""
    closed = state.get("closed_trades", [])
    closed_sorted = sorted(closed, key=lambda t: t.get("closed_at", ""), reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "open_trades": [_enrich_trade_for_publish(t) for t in state.get("open_trades", [])],
        "recent_closed": [_enrich_trade_for_publish(t) for t in closed_sorted],
        "shadow_mode": True,
        "min_n_for_verdict": PAPER_SHADOW_VERDICT_MIN_N,
    }
    try:
        PAPER_SHADOW_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = PAPER_SHADOW_HISTORY.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PAPER_SHADOW_HISTORY)
    except Exception as e:
        _log(f"shadow_history save failed: {e}")


def _prune_old_shadow(state: dict, retention_days: int = OUTCOMES_RETENTION_DAYS) -> int:
    """超过 retention_days 的 closed shadow 自动清掉 (跟 outcomes 同保留策略)"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    keep = []
    deleted = 0
    for t in state.get("closed_trades", []):
        try:
            ca = datetime.fromisoformat(t["closed_at"].replace("Z", "+00:00"))
            if ca >= cutoff:
                keep.append(t)
            else:
                deleted += 1
        except Exception:
            keep.append(t)
    state["closed_trades"] = keep
    return deleted


def _run_paper_trading(new_alerts: List[VelocityAlert], now: datetime,
                       winrate_summary: Optional[dict] = None) -> None:
    """钻石信号自动开仓 + 3 阶段动态 SL/TP 状态机 + 关仓 + 发布 history.
    + Phase 4 Shadow: premium 信号同时记录到影子追踪 (不开真仓).
    全部失败保护 — 主流程不受影响.
    """
    # 共享 prices fetch — 真 paper + shadow 都需要现价
    prices_cache: Optional[dict] = None
    def _get_prices():
        nonlocal prices_cache
        if prices_cache is None:
            prices_cache = _fetch_all_prices_now() or {}
        return prices_cache

    try:
        state = _load_paper_state()
        # 1. 新钻石开仓 — 跟踪 free 资金, 不够时跳过
        free_capital = _compute_free_capital(state)
        opened_n = 0
        skipped_capital = 0
        for a in new_alerts:
            if a.conviction_tier != PAPER_MIN_TIER:
                continue  # 非钻石不进入 paper (premium 走 shadow)
            if free_capital < PAPER_NOTIONAL_PER_TRADE_USDT:
                skipped_capital += 1
                _log(f"💸 {a.symbol} 钻石信号但资金不足 (free=${free_capital:.2f} < notional=${PAPER_NOTIONAL_PER_TRADE_USDT}), 跳过开仓")
                continue
            if _open_paper_trade(a, state, now, free_capital,
                                  winrate_summary=winrate_summary) is not None:
                opened_n += 1
                free_capital -= PAPER_NOTIONAL_PER_TRADE_USDT
                _log(f"💎 模拟开仓 {a.symbol} {a.direction} @ {a.price} "
                     f"notional=${PAPER_NOTIONAL_PER_TRADE_USDT} SL={a.suggested_sl} "
                     f"TP1={a.suggested_tp1} TP2={a.suggested_tp2} · 剩余 free=${free_capital:.2f}")
        # 2. 更新 open trades (含 phase 转移)
        closed_n = 0
        closed_list: List[dict] = []
        transitions: List[dict] = []
        if state["open_trades"]:
            prices = _get_prices()
            if prices:
                closed_n, closed_list, transitions = _update_paper_trades(state, prices, now)
        # 3. 写盘 state + 发布 history
        _save_paper_state(state)
        stats = _compute_paper_stats(state)
        _save_paper_history(state, stats)
        # 4. Phase 转移通知 (Telegram) — TP1 移 BE / TP2 启动 trailing
        for tr in transitions:
            try:
                t = tr["trade"]
                if tr["type"] == "tp1":
                    msg = (
                        f"🛡️ <b>TP1 触发 — SL 已移到 breakeven</b>\n"
                        f"{t['symbol']} {t['direction']} · 入 {t['entry_price']} · 现 {t['current_price']}\n"
                        f"已锁定: 零风险, 等 TP2 / trailing"
                    )
                else:  # tp2
                    msg = (
                        f"🎯 <b>TP2 触发 — 启动跟踪止盈 (trailing 2×ATR)</b>\n"
                        f"{t['symbol']} {t['direction']} · 入 {t['entry_price']} · 现 {t['current_price']}\n"
                        f"已落袋 +{t['unrealized_pnl_pct']}% 浮盈, 让利润奔跑"
                    )
                _send_telegram(msg)
            except Exception as e:
                _log(f"phase transition TG notify failed: {e}")
        # 5. 关仓通知
        for t in closed_list:
            try:
                emoji = "💚" if t["realized_pnl_pct"] > 0 else ("💔" if t["realized_pnl_pct"] < 0 else "⚖️")
                reason_label = {
                    "hit_sl":     "止损 (Phase A)",
                    "hit_be_sl":  "BE 平仓 (TP1 后回吐)",
                    "hit_b_trail":"Phase B 跟踪平仓 (锁部分利润)",
                    "hit_trail":  "跟踪止盈 (TP2 后)",
                    "hit_tp1":    "TP1 止盈 (旧逻辑)",
                    "hit_tp2":    "TP2 止盈 (旧逻辑)",
                    "timeout":    "超时 (4h)",
                }.get(t["close_reason"], t["close_reason"])
                usdt_pnl = _trade_usdt_pnl(t)
                msg = (
                    f"{emoji} <b>模拟仓平仓 — {t['symbol']} {t['direction']}</b>\n"
                    f"原因: {reason_label} · <b>{t['realized_pnl_pct']:+.2f}% (${usdt_pnl:+.2f})</b> · 持仓 {t['hold_time_min']:.0f}min\n"
                    f"入场 {t['entry_price']} → 平仓 {t['close_price']}\n"
                    f"高水位: {t.get('high_water_mark','—')} · 置信 {t.get('conviction_score','—')}/10"
                )
                _send_telegram(msg)
            except Exception as e:
                _log(f"paper close TG notify failed: {e}")
        if opened_n or closed_n or transitions:
            _log(f"📊 模拟仓: 新开 {opened_n}, 关 {closed_n}, 阶段转移 {len(transitions)}, "
                 f"open={stats['open']} closed={stats['closed']} "
                 f"win_rate={stats.get('win_rate','—')} total={stats['total_pnl_pct']:+.2f}%")
    except Exception as e:
        _log(f"paper trading failed: {e}")

    # ===== Phase 4 Shadow: premium 信号影子追踪 (不开真仓) =====
    # 独立 try-except: shadow 出错绝不影响真 paper
    try:
        shadow_state = _load_shadow_state()
        # 1. premium 信号开 shadow 仓 (不查资金)
        shadow_opened = 0
        for a in new_alerts:
            if _open_shadow_trade(a, shadow_state, now) is not None:
                shadow_opened += 1
        # 2. 更新 shadow open trades — 复用 _update_paper_trades 同款 3-phase 状态机
        shadow_closed_n = 0
        shadow_transitions: List[dict] = []
        if shadow_state["open_trades"]:
            prices = _get_prices()
            if prices:
                shadow_closed_n, _, shadow_transitions = _update_paper_trades(
                    shadow_state, prices, now
                )
        # 3. 清理过期 + 写盘
        _prune_old_shadow(shadow_state)
        _save_shadow_state(shadow_state)
        shadow_stats = _compute_shadow_stats(shadow_state)
        _save_shadow_history(shadow_state, shadow_stats)
        # 4. Shadow TG 通知默认 OFF (避免噪声)
        # if PAPER_SHADOW_TG_NOTIFY: ... 留作以后扩展
        if shadow_opened or shadow_closed_n or shadow_transitions:
            _log(f"📊 Shadow (premium): 新开 {shadow_opened}, 关 {shadow_closed_n}, "
                 f"open={shadow_stats['open']} closed={shadow_stats['closed']} "
                 f"verdict={shadow_stats.get('verdict','—')}")
    except Exception as e:
        _log(f"shadow tracking failed: {e}")


# ============================================================================
# Main scan
# ============================================================================

def run_scan() -> List[VelocityAlert]:
    universe = fetch_universe()
    if not universe:
        _log("empty universe, abort")
        return []

    # 一次 bulk 拉所有 funding rate (premiumIndex 返回全部 symbol)
    funding_map = fetch_all_funding_rates()

    dedup = _load_dedup()
    new_alerts: List[VelocityAlert] = []

    # 串行扫描 (避免触发 Binance rate limit)
    # 200 标的 × klines + (条件性) OI ≈ 25-35 秒, 60s 周期内
    for i, (sym, qv) in enumerate(universe):
        alert = analyze_symbol(sym,
                               quote_vol_24h=qv,
                               funding_rate_pct=funding_map.get(sym))
        if not alert:
            if (i + 1) % 50 == 0:
                time.sleep(0.5)
            continue
        # 同标的+类型分别 dedup (burst 报过不影响 sustained 后续报)
        key = f"{sym}|{alert.alert_type}"
        if _is_recently_alerted(key, dedup):
            if (i + 1) % 50 == 0:
                time.sleep(0.5)
            continue
        new_alerts.append(alert)
        dedup[key] = alert.detected_at
        icon = "⚡" if alert.alert_type == "burst" else "🔥"
        _log(f"{icon} {sym} {alert.direction} {alert.alert_type} {alert.price_change_pct:+.2f}%"
             f"({alert.metric_window_min}m) × {alert.volume_ratio:.1f}x [intensity={alert.intensity}]")

        # 防 rate limit: 每 50 个稍停
        if (i + 1) % 50 == 0:
            time.sleep(0.5)

    # 清理过期 dedup 记录
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DEDUP_WINDOW_MIN)
    dedup = {s: ts for s, ts in dedup.items()
             if (datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff
                 if isinstance(ts, str) else False)}
    _save_dedup(dedup)

    if new_alerts:
        long_n  = sum(1 for a in new_alerts if a.direction == "LONG")
        short_n = sum(1 for a in new_alerts if a.direction == "SHORT")
        if long_n >= 30:
            _log(f"🌊 全市场上涨事件: {long_n} 个 LONG (市场级,非个股 alpha)")
        elif short_n >= 30:
            _log(f"🌊 全市场下跌事件: {short_n} 个 SHORT (市场级,非个股 alpha)")
        else:
            _log(f"✅ {len(new_alerts)} alerts (LONG={long_n} SHORT={short_n})")

    # ===== Phase 2: outcome tracking (需要先跑, 因为 conviction 评分依赖 winrate) =====
    winrate_summary = None
    try:
        outcomes_state = _load_outcomes()
        _log_new_alerts_to_outcomes(outcomes_state, new_alerts)
        resolved_n = _resolve_matured_outcomes(outcomes_state)
        pruned_n = _prune_old_outcomes(outcomes_state)
        _save_outcomes(outcomes_state)
        winrate_summary = _build_winrate_summary(outcomes_state)
        _save_winrate(winrate_summary)
        if resolved_n or pruned_n:
            _log(f"📊 outcomes: 新增 {len(new_alerts)}, 结算 {resolved_n}, 清理 {pruned_n}, "
                 f"汇总 bucket={len(winrate_summary.get('by_key', {}))}")
    except Exception as e:
        _log(f"outcome tracking failed: {e}")

    # ===== Phase 3a: 置信评分 (基于 GTC 反向工程, 必须在写盘前完成) =====
    # Phase 5.A: 加载 macro regime snapshot (用于 ALT_SEASON_RUNNING+LONG 减分).
    # Phase 5.B: 加载 BTC regime snapshot (用于 trend-aligned bonus).
    try:
        regime_snap_for_scoring = _load_current_regime()
        current_regime = regime_snap_for_scoring.get("regime") if regime_snap_for_scoring else None
    except Exception:
        current_regime = None
    try:
        current_btc_regime = fetch_btc_regime()  # 一次 API per scan, 不是 per symbol
    except Exception:
        current_btc_regime = None
    try:
        for a in new_alerts:
            score, tier = _compute_conviction(
                a, winrate_summary,
                regime=current_regime,
                btc_regime=current_btc_regime,
            )
            a.conviction_score = score
            a.conviction_tier = tier
        diamonds = [a for a in new_alerts if a.conviction_tier == "diamond"]
        if diamonds:
            _log(f"💎 高置信信号 {len(diamonds)} 条: " +
                 ", ".join(f"{a.symbol}(score={a.conviction_score})" for a in diamonds))
    except Exception as e:
        _log(f"conviction scoring failed: {e}")

    # ===== 合并已有 alerts + 新增 (conviction 已经在 dataclass 中, 一次写盘搞定) =====
    existing = _load_alerts()
    combined = _trim_old_alerts(existing) + [asdict(a) for a in new_alerts]
    combined.sort(key=lambda a: a.get("detected_at", ""), reverse=True)
    _save_alerts(combined)

    # ===== Phase 3: Telegram push (单独 try-except, 失败不影响主流程) =====
    try:
        _push_to_telegram(new_alerts, winrate_summary)
    except Exception as e:
        _log(f"telegram push failed: {e}")

    # ===== Phase 4: 自动模拟仓 (仅钻石信号, 跟踪真实盈亏曲线) =====
    # Phase 6.B (2026-06-03): 传 winrate_summary 让 _open_paper_trade 应用 Tier 1A filter
    try:
        _run_paper_trading(new_alerts, datetime.now(timezone.utc),
                            winrate_summary=winrate_summary)
    except Exception as e:
        _log(f"paper trading failed: {e}")

    return new_alerts


def cmd_show() -> int:
    alerts = _load_alerts()
    if not alerts:
        print("(无近期速率报警)")
        return 0
    print(f"=== 近 {KEEP_ALERT_WINDOW_MIN}min 内速率报警 ({len(alerts)} 个) ===")
    print(f"{'symbol':<14} {'dir':<6} {'类型':<10} {'涨跌':>8} {'vol倍':>7} {'强度':>4} {'when'}")
    print("─" * 78)
    for a in alerts[:20]:
        t = a.get("alert_type", "burst")
        icon = "⚡启动" if t == "burst" else "🔥持续"
        pct = a.get("price_change_pct", a.get("price_change_1m_pct", 0))
        win = a.get("metric_window_min", 1)
        print(f"{a['symbol']:<14} {a['direction']:<6} {icon:<10} {pct:>+7.2f}%/{win}m "
              f"{a['volume_ratio']:>6.1f}x {a['intensity']:>4} {a['detected_at'][:19]}")
    return 0


def main(argv) -> int:
    if argv and argv[0] == "show":
        return cmd_show()
    if argv and argv[0] == "test-email":
        # 测试邮件配置 (不依赖真钻石信号触发)
        if not EMAIL_SMTP_HOST:
            print("❌ EMAIL_SMTP_HOST 未配置. 请在 ~/.cresus/env.sh 中设置邮件环境变量")
            print("   参考: EMAIL_SMTP_HOST, EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_TO 等")
            return 1
        print(f"测试发送邮件到 {EMAIL_TO} 通过 {EMAIL_SMTP_HOST}:{EMAIL_SMTP_PORT} ...")
        ok = _send_email(
            "[CRESUS 💎] 邮件配置测试",
            "如果收到这封邮件, Cresus 钻石信号邮件通道配置成功!\n\n"
            "下次钻石信号触发时, 你会自动收到一封更详细的告警邮件.\n\n"
            "--\nCresus 量能加速雷达"
        )
        print("✅ 已发送" if ok else "❌ 发送失败 (检查 log: ~/cresus-bot/logs/velocity_scanner.log)")
        return 0 if ok else 1
    if argv and argv[0] == "test":
        # 单标的快速测试 (拉 24h vol + funding 给完整双路 + 富信息)
        sym = argv[1] if len(argv) >= 2 else "BTCUSDT"
        tdata = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr?symbol={sym}")
        qv = None
        if isinstance(tdata, dict):
            try:
                qv = float(tdata.get("quoteVolume", 0))
            except (ValueError, TypeError):
                qv = None
        # funding (单标的)
        pdata = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex?symbol={sym}")
        fr = None
        if isinstance(pdata, dict):
            try:
                fr = float(pdata.get("lastFundingRate", 0)) * 100
            except (ValueError, TypeError):
                fr = None
        result = analyze_symbol(sym, quote_vol_24h=qv, funding_rate_pct=fr)
        if result:
            print(f"✅ {sym} 触发 ({result.alert_type}): {result}")
        else:
            print(f"❌ {sym} 未触发 (启动 + 持续 都不达标)")
            if qv:
                print(f"   参考: 24h_vol=${qv:,.0f} → avg_1m=${qv/1440:,.0f}")
            if fr is not None:
                print(f"   funding: {fr:+.4f}%")
        return 0
    new = run_scan()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
