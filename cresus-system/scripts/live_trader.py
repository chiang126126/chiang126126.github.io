"""Live Trader — Mirror paper trader's decisions on real Binance Futures.

System Version: V3 (固化于 2026-05-24, 归档 cresus-system/archive/v3_baseline/)
  - $100 cap, 3x leverage, $20 margin/笔, 4 concurrent max
  - SL ~1%, TP 0.5/1.5/3%, hold ~30min
  - Phase 4.A → 4.K 全部累积
V4 (即将切换): paper + live 同步重写, day-scale hold + 5% SL + 1x leverage.
  详见 cresus-system/archive/v3_baseline/README.md.

Phase 3.1: DRY-RUN 骨架. 读 paper trader state, 输出 "would-mirror" 日志.
不真下单. 为 Phase 3.2+ 接入真交易铺路.

架构:
    paper trader (scanner cron)
        ↓ writes
    ~/cresus-bot/paper_trades_history.json
        ↓ reads
    live_trader (this script, separate cron / loop)
        ↓ writes
    ~/cresus-bot/.live_trades.json (private state)
    ~/cresus-bot/live_trades_history.json (public view for dashboard)

设计原则:
- 进程独立: live_trader 崩溃绝不影响 paper trader
- 共享信号源: 通过 paper 的 published state 读取
- DRY-RUN 默认: 需显式 --live + 实例 dry_run=False 才真下单
- Filter 链式: symbol whitelist → max concurrent → already mirrored
- 状态原子写 (.tmp → rename)

Phase 3.1 范围 (本文件):
- 加载 paper / live state
- 输出 would-mirror 日志
- 状态持久化 (mirrored_paper_ids tracking)
- CLI: --once (默认) / --loop (持续) / --live (关闭 dry-run)

Phase 3.2+ 范围 (后续):
- 实际调 binance_client.open_position
- Client-side SL polling
- Reconciliation with exchange
- Publish to live_trades_history.json
"""
from __future__ import annotations

import json
import logging
import time
import hashlib    # Phase 4.D: A/B 分组用 MD5 (确定性, 跨进程一致)
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Import binance_client (sibling module)
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from binance_client import BinanceClient, BinanceError, load_credentials

# ============================================================================
# 配置
# ============================================================================

# 文件路径
PAPER_HISTORY = Path.home() / "cresus-bot" / "paper_trades_history.json"
LIVE_STATE = Path.home() / "cresus-bot" / ".live_trades.json"
LIVE_HISTORY = Path.home() / "cresus-bot" / "live_trades_history.json"

# 系统版本代号 (V3 固化于 2026-05-24; V4 切换后 bump 至 "V4")
SYSTEM_VERSION = "V3"

# Live 交易配置 (小心调整)
# Phase 4.R6 (2026-05-24): notional 从 $20 → $400, 起始资金 $100 → $2000
# 跟 paper engine 1:1 同步 ($400/笔, 5 槽里取 4 槽, $2000 starting), 便于
# 直接对比 paper vs live 单笔 PnL / fees / slippage 绝对值, 减少缩放噪声.
# 风控阈值同步 20x 放大保持等效百分比 (daily DD 5%, max deploy 80%).
LIVE_NOTIONAL_USDT = 400.0             # 每笔基准 $400 (Phase 5.A 起按 score 分档)
# Phase 5.A (5/27) + 5.K (6/1) score 分档仓位 (与 paper 同步):
#   score 5 (92%): paper EV +$0.92, 减摩擦 $2-3 后实际 EV ~0 → 减半到 $200
#   score 6-7 (7%): paper EV +$4.50/笔, 摩擦后净 +$2-6 → 翻倍到 $800 (核心利润源)
#   score 8+ (0.5%): n=7 累计 -$118 反向证据 → 减半 $200
#
# Phase 5.A-restore + 5.K (6/1): 解除 5/28 hotfix.
#   依据: Phase 5.A-fix (binance_client MAX_QTY chunking + open_position 截断)
#   已完成 + 测试覆盖, DYMUSDT 类 -4005 死循环不可能再发生.
#   5/31 数据验证: score 5 在 live 上 EV 接近 0 (paper $0.92 - 摩擦 $2-3),
#   score 6-7 EV 远高 (paper $4.5 - 摩擦 $2-3 = $2-3 净). 仓位差异化是
#   "放大已知有 EV 的, 缩小没 EV 的"的最优解.
LIVE_NOTIONAL_BY_SCORE = {
    5:   200.0,   # Phase 5.K: 低 EV 减半
    6:   400.0,   # Phase 5.K-adjust (6/1): 撤回 5.A-restore 的 800.
                  # 数据反向 (5/31+6/1 共 6 笔 avg -$5.83, n=6 但全亏).
                  # 与历史 EV +$4.34 (n=45) 矛盾. 保守回 $400 等更多数据.
    7:   800.0,   # Phase 5.A-restore: 高 EV 翻倍 (验证: 11 笔 avg +$4.27)
    8:   200.0,
    9:   200.0,
    10:  200.0,
}


def _live_notional_for_paper(paper_trade: dict) -> float:
    """按 paper_trade.conviction_score 返回 live notional. 字段缺/异常返基准."""
    try:
        s = int(paper_trade.get("conviction_score"))
    except (TypeError, ValueError):
        return LIVE_NOTIONAL_USDT
    return LIVE_NOTIONAL_BY_SCORE.get(s, LIVE_NOTIONAL_USDT)


# ==========================================
# Phase 5.S (2026-06-02) — Regime-aware size multiplier (默认无行为变化)
# ==========================================
# 触发原因: audit_sub_regime_paper_outcomes.py (realized_pnl 端) 显示各 (direction,
#         regime, sub_regime) 桶 paper EV 差异大. 单一 score-based notional 不能
#         体现 regime 维度的 EV 差. 例如:
#           LONG  chop  /—            paper avg +$1.70 (n=797) — 高 EV 大样本
#           LONG  down  /down_acute   paper avg -$0.19 (n= 76) — 唯一 marginal 负
#           SHORT down  /down_stable  paper avg +$1.85 (n=280) — 高 EV 大样本
#
# 设计: 在 score-based base notional 上乘以 (direction, regime, sub_regime) 维度
#       multiplier. 默认空 dict = 完全等同 Phase 5.A 行为, 安全可回滚.
#
# Lookup 优先级 (从精到泛, 用于配置灵活性):
#   1) (direction, regime, sub_regime) 完全匹配
#   2) (direction, regime, None)        — regime 内不分 sub 的回退
#   3) (direction, None, None)          — 仅 direction 的回退
#   4) 默认 1.0
#
# 安全设计:
#   - multiplier ∈ [MIN, MAX] = [0.0, 3.0]
#     0.0 = 该桶完全停 mirror (会触发 is_eligible_for_mirror reject)
#     允许 cut to zero, 但不允许超 3x (防误配把仓位放飞)
#   - 应用后 final_notional ≤ LIVE_MAX_NOTIONAL_PER_TRADE 兜底
#     防 score 7 $800 × 3 = $2400 超出单仓上限
#
# 启用流程 (data-driven, 不要凭直觉 flip):
#   1. 跑 scripts/audit_sub_regime_paper_outcomes.py --direction BOTH
#   2. 看 [direction, regime, sub_regime] 桶 paper realized avg + n
#   3. 选择 multiplier:
#        avg > $3 且 n ≥ 100 → 候选 ×2.0 (强加杠杆)
#        avg > $2 且 n ≥  50 → 候选 ×1.5 (中等倾斜)
#        avg < $0 且 n ≥  50 → 候选 ×0.5 (减半) 或 ×0.0 (停)
#        n < 30 一律默认 1.0 (样本不足)
#   4. 部署: 修改 LIVE_REGIME_SIZE_MULTIPLIER, 重启 live-trader
#   5. 观察 24-48h, 用 fees-aware 日志 + per-bucket PnL 复盘
#
# 2026-06-02 首次启用 (基于 audit_sub_regime_paper_outcomes.py --direction BOTH
# 跑出的 21 天 2195 笔 paper realized PnL):
#   LONG  chop  /—            n=797 avg=+$1.70 → ×1.5 (大样本中等 EV 倾斜)
#   SHORT down  /down_stable  n=280 avg=+$1.85 → ×1.5 (大样本中等 EV 倾斜)
#   LONG  down  /down_acute   n= 76 avg=-$0.19 → ×0.5 (唯一 marginal 负 EV)
#                                              注: 当前 Phase 4.J 已拒 down+LONG,
#                                              此条目是"防御纵深" — 若未来 Phase 5.R
#                                              放开此 sub_regime, 此 ×0.5 自动接管.
#   其它桶未设 → mult 默认 1.0 (维持 Phase 5.A 行为)
LIVE_REGIME_SIZE_MULTIPLIER: "dict[tuple[str, Optional[str], Optional[str]], float]" = {
    ("LONG",  "chop", None):           1.5,
    ("SHORT", "down", "down_stable"):  1.5,
    ("LONG",  "down", "down_acute"):   0.5,
}
LIVE_REGIME_SIZE_MULT_MIN = 0.0   # 允许 cut to zero (该桶完全停 mirror)
LIVE_REGIME_SIZE_MULT_MAX = 3.0   # 单笔不允许超 3 倍 base
LIVE_MAX_NOTIONAL_PER_TRADE = 2000.0  # 单笔绝对上限, multiplier 后兜底


def _regime_size_multiplier(
    direction: str,
    btc_regime: Optional[str],
    btc_sub_regime: Optional[str],
) -> float:
    """Phase 5.S: 查 (direction, regime, sub_regime) → size multiplier (default 1.0).

    Args:
        direction: 'LONG' / 'SHORT' (会 upper())
        btc_regime: 'up' / 'chop' / 'down' / None (会 lower())
        btc_sub_regime: 'down_acute' / 'down_stable' / 'down_rebound' / None
                       (精确匹配, 不 normalize 大小写)

    Returns:
        Clamp 到 [LIVE_REGIME_SIZE_MULT_MIN, LIVE_REGIME_SIZE_MULT_MAX] 的 multiplier.
        默认 1.0 (空 dict / 找不到匹配 / direction 空 = 完全等同 Phase 5.A 行为).

    Lookup 优先级 (从精到泛):
        1) (d, r, sub) 完全匹配
        2) (d, r, None) regime 通配子状态 (适合 chop / up 这种无子状态的)
        3) (d, None, None) 仅 direction 的回退 (危险, 慎用)
        4) 1.0
    """
    if not direction:
        return 1.0
    d = direction.upper()
    r = btc_regime.lower() if btc_regime else None
    sub = btc_sub_regime  # 精确匹配, 不 normalize

    for key in ((d, r, sub), (d, r, None), (d, None, None)):
        if key in LIVE_REGIME_SIZE_MULTIPLIER:
            try:
                mult = float(LIVE_REGIME_SIZE_MULTIPLIER[key])
            except (TypeError, ValueError):
                return 1.0
            # Clamp 兜底, 防误配
            return max(LIVE_REGIME_SIZE_MULT_MIN, min(LIVE_REGIME_SIZE_MULT_MAX, mult))
    return 1.0


def _live_notional_for_mirror(
    paper_trade: dict,
    btc_regime: Optional[dict] = None,
) -> float:
    """Phase 5.S: 综合 score-based base × regime multiplier 算最终 notional.

    base = _live_notional_for_paper(paper_trade)   (Phase 5.A score-based)
    mult = _regime_size_multiplier(direction, regime, sub_regime)
    final = min(base * mult, LIVE_MAX_NOTIONAL_PER_TRADE)

    Args:
        paper_trade: paper 信号
        btc_regime: 可选, dict from _compute_btc_regime ({'regime': ..., 'sub_regime': ...})
                    None / 非 dict → 不应用 multiplier, 返 base (Phase 5.A 行为)

    Returns: float, 任何异常 fail-safe 返 base.
    """
    base = _live_notional_for_paper(paper_trade)
    if not isinstance(btc_regime, dict):
        return base
    try:
        direction = paper_trade.get("direction", "")
        regime = btc_regime.get("regime")
        sub = btc_regime.get("sub_regime")
    except (AttributeError, TypeError):
        return base
    mult = _regime_size_multiplier(direction, regime, sub)
    final = base * mult
    # 兜底单仓上限
    return min(final, LIVE_MAX_NOTIONAL_PER_TRADE)


LIVE_MAX_CONCURRENT = 4                # 实盘并发上限
LIVE_LEVERAGE = 1                      # 杠杆 1x (Phase 4.R6+ 跟 paper 一致).
                                       # PnL = notional × pct, 跟 leverage 无关 →
                                       # 1x vs 3x 不改 PnL/fees, 只改 margin 占用.
                                       # 1x 强平距离 100% (基本不可能), 比 3x 33% 更安全.
                                       # 每次 mirror_open 前强制 set_leverage(1) 防漂移.
LIVE_SYMBOL_WHITELIST = [              # Phase 6 第 1 周限主流币
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
]
LIVE_MIRROR_MAX_AGE_SEC = 600          # 仅 mirror 10min 内开的 paper trade
                                       # (防止启动时把陈年 paper open 全部 mirror)

# Phase 4.B 黑名单 (基于实盘历史 0 胜率的 symbol, 每周复盘时增删).
# 黑名单优先级最高 — 即使 OBS mode 跳过白名单, 黑名单仍然拒绝.
# 复审时间:
#   2026-05-17 (89 笔实盘数据, 仅加 n≥4 且 0 胜的 symbol)
#   2026-05-21 (333 笔实盘数据, 加 PLAY/GUA/STABLE — 均 n≥3 0 胜)
#   2026-05-25 (审计: DODO/NMR/PLAY/GUA 拉黑后 paper 0 新信号, scanner 自然淘汰,
#               Phase 4.U/4.V 新策略下释放观察; STABLE 5/0 p<0.05 保留)
LIVE_SYMBOL_BLACKLIST = [
    "STABLEUSDT",  # 5 笔 0 胜, 累计 -$1.08 (5/21 加); 5/25 复审: p<0.05 唯一统计显著, 保留
    "XAGUSDT",     # TradFi-Perps 需单独签协议, -4411 结构性错误 (5/25 加)
    # 已释放 (5/25): DODOXUSDT / NMRUSDT / PLAYUSDT / GUAUSDT
    #   原因: 拉黑后 paper 0 新信号 (scanner 自然淘汰), 样本 n≤4 统计不显著.
    #   Phase 4.U/4.V 新策略下重新开放, 若 paper 再出信号可积累新样本复审.
]

# Phase 4.W (5/26): Reconciliation 忽略列表 — testnet 上"无法平仓"的僵尸合约.
# 这些 symbol 在 exchange 上有 positionAmt > 0 但已下架/无对手方 (返 -2020),
# 永远无法平掉, 也不应当被 live_trader 重新认领. 跳过 reconciliation 警报,
# 避免 dashboard 持续报错. 不影响真正的 trading 逻辑.
LIVE_RECON_IGNORE_SYMBOLS = {
    "AZTECUSDT",  # 2026-05-26: testnet 价格归零, -2020 Unable to fill, -$86.52 锁住
}

# Phase 4.H Conviction filter (2026-05-22 部署) / Phase 4.R7 (2026-05-24 关闭)
# ==============================================================================
# 4.H 部署时论据 (回看不充分):
#   380 笔实盘按 conv 切片, conv≥6 26 笔 +$0.097/笔 vs conv=5 354 笔 -$0.028.
#   用户当时注: "n=26 样本不显著 (p≈0.1-0.2), 是 data leading change".
#   而且 5/24 后发现 paper / live 字段名 bug (realized_pnl_usdt vs
#   realized_usdt_pnl swap), 原数据切片可能也是错的.
#
# Phase 4.R7 (2026-05-24) — 关闭决策 (基于 9 天累计数据修正后):
#   Paper 全样本 5/15-5/24 (929 笔):
#     conv=5: n=847, avg +$0.868/笔  ← 被拒掉的, 其实大幅盈利
#     conv≥6: n=82,  avg +$2.969/笔
#   Paper 4.H 部署后 5/22-5/24 (283 笔):
#     conv<6 (被拒):  n=259, avg +$1.044/笔   ← 拒掉的盈利
#     conv≥6 (让过):  n=24,  avg -$1.426/笔   ← 反向, 让过的反而亏!
#   Live 4.H 部署后 (54 笔):
#     conv<6 (漏掉): n=39, avg -$0.0205/笔, win 35.9%
#     conv≥6 (让过): n=15, avg -$0.1725/笔, win 6.7%   ← 双源一致反向
#   累计影响: 4.H 部署 3 天 paper EV ~-$300 (拒盈利+让亏损).
# 决策: 关 filter. 后续累积 100+ 笔再重审.
LIVE_MIN_CONVICTION_SCORE = None       # Phase 4.R7: 关 filter (None = 全部 conv 通过)
                                        # 历史: 6 → None (基于双源数据反向证据)

# ⚠️ DRY-RUN 观察期标志 — 仅用于 testnet 观察 mirror lifecycle.
# True 时:
#   - 跳过 LIVE_SYMBOL_WHITELIST 检查 (接受 paper 所有 diamond signal)
#   - 启动若同时带 --live 会被 hard reject (安全锁)
#   - 每 tick 输出 warning 提醒
# 实盘前必须改回 False!
LIVE_OBSERVATION_MODE = True

# Phase 3.3.a 风控参数 (Phase 4.R6 调整: $100 → $2000 起始)
LIVE_STARTING_CAPITAL_USDT = 2000.0    # 实盘起始资金 (跟 paper 一致, testnet 资金)
LIVE_DAILY_DD_LIMIT_USDT = 100.0       # 日亏 -$100 (= 5% × $2000, 跟旧 5%/$100 等效)
LIVE_MAX_DEPLOY_USDT = 2400.0          # Phase 5.A (5/27): $1600 → $2400 — 适配 score 分档.
                                       # 极端场景 2×$800(score6-7) + 2×$400(score5) = $2400.
                                       # Paper 总额 $2000 + 20% buffer 实际为 $2400 等效上限.

# Phase 3.3.b 累计 DD kill switch
LIVE_TOTAL_DD_LIMIT_PCT = 5.0          # 总回撤 5% → 自动写 emergency flag

# Phase 4.A 滑点护栏 (alpha-coin 小币流动性差, 防止市价单灾难性进场)
# 开仓前先取当前价, 算 paper 信号价 → 当前价 的预滑点 (positive bps = 不利).
# 超阈值放弃 mirror, 记 missed_signal.
#
# 阈值演化:
#   v1 (5/17 上线): 30 bps  ← 基于初期 16 笔配对数据保守值
#   v2 (5/17 调整): 50 bps  ← 复盘 116 笔后发现 30 拒太严
#   v3 (5/21 放宽): 100 bps  ← 333 笔数据揭示反直觉现象:
#     高滑点 (>50bps) 48 笔 净 +$1.91, 人均 +$0.040
#     低滑点 (≤50bps) 267 笔 净 -$10.18, 人均 -$0.038
#     高滑点常出现在强势/弱势行情, 往往是真趋势. 656 bps STORJ 这种仍会被拦.
#   v4 (5/25 动态化): 按 intensity 分级 — intensity=1 保 100bps (v3 数据结论),
#     高动量信号接受更大阈值: intensity=3 最高 200 bps (XANUSDT/SAGAUSDT 型).
LIVE_MAX_ENTRY_SLIPPAGE_BPS = 100.0    # 预滑点上限 (intensity=1 默认值, fallback).
# Phase 4.V (5/25) 动态阈值: 高动量信号接受更大预滑点.
# 依据: XANUSDT intensity=3 场景下 134 bps 被拦截, 而 paper 后续 +$49.44;
# 高滑点在强趋势中可被后续走势消化; intensity=1 与 v3 100bps 数据分析一致.
LIVE_SLIPPAGE_THRESHOLD_BY_INTENSITY = {
    3: 200.0,   # 高速急拉 (XANUSDT/SAGAUSDT 型)
    2: 150.0,   # 中等动量
    1: 100.0,   # 低动量 / 默认 (与 v3 100bps 数据分析一致)
}

# Phase 4.W (5/26): 本 session 中 Binance 返 -1121 (Invalid symbol) 的合约.
# Testnet 未上线但主网存在的新币在 set_leverage 时触发 -1121.
# 内存集合, 不跨进程 — 切主网时这些 symbol 可能恢复可用, 故不写入永久 blacklist.
_EXCHANGE_UNAVAILABLE_SYMBOLS: set = set()

# Phase 4.C BTC regime-aware 标签 (每笔 trade 开仓时记录当时 BTC 状态)
# 用 1h K 线的 MA(25) 作 baseline (~24h 滚动均值, 匹配短线持仓视角).
# 距 MA25 >= +阈值% = up, <= -阈值% = down, 中间 = chop.
# 不做交易决策, 仅供复盘按 regime 切分胜率/PnL.
LIVE_BTC_REGIME_THRESHOLD_PCT = 0.5

# Phase 4.D SL slippage 补偿 — A/B 测试
# 问题: live 入场吃滑点后, paper_sl 离 live_entry 距离 < 离 paper_entry 距离,
#       普通波动就触发 sl_breach. 实测 24 笔 (21%) 是 live SL 触发但 paper 同信号是
#       hit_b_trail / hit_trail 盈利收 — 漏赚 ~$2.31 / 100 笔.
# 方案: live_sl = paper_sl + (live_entry - paper_entry), 保持 SL 跟 actual entry
#       的距离 = paper 设计距离. 兼容 LONG / SHORT 同公式.
# A/B 测试 (启动期): 按 paper_id MD5 哈希分组, 确定性可复现.
# 数据足够 (≥30 笔 each side) 后据实切换到 always.
# Phase 4.E (2026-05-19): 升级到 3-arm (A/B/C), 加 wick filter 组. B 组保留补偿测试.
# Phase 4.F (2026-05-21): 升级到 4-arm (A/B/C/D), 加 regime gate. 配置见下方.

# Phase 4.E SL Wick 过滤 (2026-05-19)
# ==========================================
# 数据驱动: 200 笔实盘里 47 笔(24%) 是 "live SL 触发但 paper 走到 trail/b_trail",
#         漏赚 ~$5.50. 其中 66% 的 live_sl == paper_sl, 即 SL 值正确但触发时机偏激.
# 根因: live 用 1m kline close 做 SL 检测, 30s 轮询能捕捉到 wick (闪针), 而 paper
#       (不同价源 / 频率) 看不见这种瞬时 wick.
# 方案: C 组对 SL breach 要求"连续 N 次轮询都越 SL"才触发 (默认 N=2, 即需价格在
#       SL 外停留 ≥ ~30s). 单次 wick 不再触发.
# A/B/C 测试: A=无补偿无过滤(基线), B=补偿无过滤(Phase 4.D), C=无补偿有过滤(Phase 4.E).
#   每组 ~1/3, 用同一 paper_id MD5 → 0/1/2 分配.
#
# Phase 4.L (2026-05-24): wick filter 推广 "abcd" → "always"
# ==========================================
# 9 天 (5/15-5/24, 239 笔 sl_breach_client) 数据驱动决策:
#   C 组 (filter ON):   17 假止损 / 52 = 32.7%  (人均漏赚 $4.70)
#   A+B+D (filter OFF): 71 假止损 / 187 = 38.0% (人均漏赚 ~$5)
#   差异: 5.3 pp 改善 (C 组显著优)
# 论断: wick filter 有真实 alpha. 推广全员 ('always' mode) 预期月度 +$170 (释放 paper edge).
# 风险: 极低. orthogonal to regime gate (D 组). 改回 'abcd' 1 行即回滚.
# 验证窗口: 2-3 天观察新数据. 若假止损率不降到 33% 左右, 重审.
LIVE_SL_WICK_FILTER_MODE = "always"  # Phase 4.L 推广 (was "abcd")
LIVE_WICK_FILTER_MIN_BREACHES = 4    # Phase 5.M (6/1): 3→4, 数据驱动再升一档.
                                      # Phase 5.J 5/31→6/1: sl_breach 64 → 27 (-58%) 已大幅改善,
                                      # 但 Top 10 失真案例 7/10 仍是 paper:hit_trail → live:sl_breach,
                                      # 即仍有 wick filter 漏网. 升 4 = 20s 确认窗口.
                                      # 预期再救 8-12 笔 × $1.87 = +$15-22/天, 代价 -$3-5/天 (真破位多 5s).

# Phase 4.M Funding-aware mirror filter (2026-05-24)
# ==========================================
# 9 天 (5/15-5/24, paper 1072 笔) 数据驱动 audit:
#   funding ≤ -0.05% (任意方向): paper 人均 +$3.97/笔 (LONG +$3.89, SHORT +$4.15)
#   funding ≥ +0.05% (任意方向): paper 人均 -$0.85/笔 (LONG -$0.75, SHORT -$1.13)
#   neutral |funding| < 0.05%:   paper 人均 +$0.57/笔 (基线)
# 现象: funding 不是简单的"收钱/付钱"作用, 而是市场情绪 leading indicator.
#       funding 负 = 恐慌/波动期, V3 volume-burst 信号在此环境更准.
# 实施:
#   1) funding ≥ +0.05% → 拒 mirror (live 不利组 16 笔 -$2, EV 负, 拒得起)
#   2) funding ≤ -0.05% → 友好标记, _check_sl_breach 用 +1 breaches (3 vs 默认 2)
#      给 favorable signal 多 30s wick 宽容, 减少假止损
# 预期 EV: 月度 +$85 (合计拒 negative + 救假止损)
# 风险: 低. fallback 'neutral' 不变. 改 LIVE_REJECT_ADVERSE_FUNDING=False 即回滚.
LIVE_FUNDING_FAVORABLE_THRESHOLD_PCT = -0.05    # paper funding_rate_pct ≤ 此 → 友好
LIVE_FUNDING_ADVERSE_THRESHOLD_PCT = 0.05       # ≥ 此 → 不利
LIVE_REJECT_ADVERSE_FUNDING = True              # True: funding 不利时拒 mirror
LIVE_FUNDING_FAVORABLE_WICK_BREACHES = 5        # Phase 5.M (6/1): 默认 4 后, 友好时 +1 = 5
                                                 # (维持 funding favorable 比 default 多 1 buffer 语义)

# Phase 4.F Regime Gate (2026-05-21)
# ==========================================
# 数据驱动: 333 笔实盘里 down regime LONG 10 笔 0 胜 0% (-$3.14, 人均 -$0.314),
#         显著差于 down SHORT (20% 胜率, -$0.071/笔), t-test p=0.042.
# 论据: paper 自己的 RISK_OFF 时段 SHORT 人均 +$0.126 也远好于 LONG +$0.037,
#       证实 BTC 下跌时 SHORT 是更优方向. paper 信号生成器未做此过滤.
# 方案 v1 (4.F): D 组对 paper 信号加 regime gate — A/B/C 组继续允许 down+LONG.
# A/B/C/D 测试: A=基线, B=补偿, C=wick filter, D=regime gate. 每组 ~1/4.
#
# Phase 4.J Update (2026-05-23) — Regime Gate 普及到全部组
# ==========================================
# 触发原因: 4.F 部署后 1-2 天观察, A/B/C 组的 down+LONG 持续亏损 (10 笔 9 亏),
#         继续 4-arm 测试等于"花真钱继续验证已知坏假设". 数据已足够支持
#         全面应用 (p=0.042 + 持续重现).
# 改动: mode 从 "abcd" 改 "always", 让 _ab_use_regime_gate 对所有组返 True.
#        regime_gate_enabled 字段在 live_trade 上仍记录, 但现在所有新 trade
#        都是 True (溯源仍可见).
# 不动:
#   - SL 补偿 (B 组) / Wick filter (C 组) 仍保留 4-arm A/B 测试 (这俩还有歧义)
#   - _should_block_for_regime 规则不变 (仅 down + LONG)
#   - ab_group 字段仍记录 A/B/C/D (溯源不变, 即使 D 组实质跟 A 组相同)
LIVE_REGIME_GATE_MODE = "always"    # "off" | "abcd" (legacy 4-arm) | "always" (Phase 4.J 默认)

# ==========================================
# Phase 5.R (2026-06-02) — Sub-regime aware regime gate (默认无行为变化)
# ==========================================
# 触发原因: Phase 4.F/4.J 把 down+LONG 一律拒, 数据驱动 (0/10 胜 p=0.042).
#         但 BTC down regime 并非铁板一块: Phase 4.K 已细分 down_acute (急跌) /
#         down_stable (企稳) / down_rebound (反弹 3h 涨>0.5%). 反弹时 LONG 表现
#         可能完全不同, 但当前 gate 不区分子状态, 把 rebound 也一刀切.
#
# 设计: 引入 allow-list — 列在 set 里的 sub_regime, 当 regime=down + direction=LONG
#       同时命中时, 豁免 gate (允许 mirror). 默认 **空 set** = 与 4.J 完全一致.
#
# 启用前置 (用户审计, 不要凭直觉 flip):
#   1) 跑 scripts/audit_sub_regime_paper_outcomes.py 拉 BTC 历史 1h K 线,
#      把每笔 paper LONG trade 在 entered_at 时刻的 regime + sub_regime 重算
#   2) 看 down + LONG 按 sub_regime 拆分的胜率 / 平均 PnL / 样本数
#   3) 仅当 (sub == "down_rebound" 且 样本 ≥ 20 且 avg PnL > +$0.5) 才考虑放开
#   4) 放开后用 LIVE_REGIME_GATE_SUB_REGIME_ALLOW = {"down_rebound"}, 观察 24-48h
#
# 安全特性:
#   - default = set() → 任何 sub_regime 都不豁免 (与 Phase 4.J 100% 一致)
#   - 仅影响 down + LONG 这一种组合; up / chop / SHORT 任何情形都不受影响
#   - sub_regime = None (regime 非 down 或 klines 不足) 永远不豁免 (fail-safe)
LIVE_REGIME_GATE_SUB_REGIME_ALLOW: set = set()  # 默认空 — 与 Phase 4.J 行为一致

# 升级 SL 补偿 / wick filter 到 4-arm 一致 (B/C 分别对应)
# Phase 4.Y (5/27): SL 补偿从 "abcd" (4-arm A/B 测试) 推广到 "always".
# 数据依据 (5/26 复盘 32 笔 mirror):
#   - 11 笔 paper hit_sl → live sl_breach_client, avg gap +$1.61/笔 = $17.71/天
#   - 平均 slippage 28.5 bps, 入场偏移使 live 实际 SL 距离 > paper, 触发时多损失
#   - SL 补偿将 live_sl 移动 = paper_sl + slippage, 让 live 实际 SL 距离 ≈ paper
#   - Wick filter 已 always, 防御 SL 移近后的误触发
# 预期: $7-17/天止损改善. 风险: SL 移近时 wick 触发率上升 (由 wick filter 防御).
LIVE_SL_COMPENSATION_MODE = "always"  # "off" | "ab" (legacy) | "abc" | "abcd" | "always"

# Phase 3.3.a/b 控制文件 (在 ~/.cresus-*)
PAUSE_FLAG_PATH = Path.home() / ".cresus-pause"
EMERGENCY_STOP_PATH = Path.home() / ".cresus-emergency-stop"

# 状态管理
MIRRORED_IDS_KEEP_LAST_N = 500          # mirrored_paper_ids 滚动窗口
MISSED_SIGNALS_KEEP_LAST_N = 50         # missed_signals 滚动窗口 (诊断用)

# 主循环
POLL_INTERVAL_SEC = 5                   # --loop 模式 poll 间隔 (Phase 4.V 5/25: 30→5s)

# 状态文件 schema 版本
STATE_VERSION = "1.0"

log = logging.getLogger(__name__)


# ============================================================================
# State I/O
# ============================================================================

def _empty_live_state() -> dict:
    return {
        "version": STATE_VERSION,
        "live_open_trades": [],
        "live_closed_trades": [],
        "mirrored_paper_ids": [],
        "missed_signals": [],
        "last_update": None,
        "session_started_at": datetime.now(timezone.utc).isoformat(),
    }


# Phase 5.E (5/28): 连损熔断 — 数据驱动 (1410 笔 + circuit breaker 模拟).
# 历史最长 streak: 13 笔连亏 (5/15 02:37-05:40, 22 笔 -$52).
# 模拟 4/30m/30m 净避亏 +$127 over 1410 笔.
#
# 触发: 滑动 LIVE_CB_WINDOW_MIN 分钟内 ≥ LIVE_CB_SL_THRESHOLD 笔 hit_sl
#       (含 sl_breach_client + paper:hit_sl, paper trail/breakeven 不算)
# 动作: 暂停 mirror_open LIVE_CB_PAUSE_MIN 分钟
#       paper 继续跑 (数据采集), live 不开新仓
#       现有持仓不影响 (close/sync 照常)
#
# 阈值组合优化结果 (净避亏 over 1410 笔):
#   3/30m/30m → -$32 (误杀太多)
#   4/30m/30m → +$127 ⭐ (最优)
#   5/30m/30m → -$25 (触发太少)
#   5/60m/60m → +$105 (次优, 窗口大)
LIVE_CB_SL_THRESHOLD = 4
LIVE_CB_WINDOW_MIN = 30
LIVE_CB_PAUSE_MIN = 30


# Phase 5.G (5/28): Post-fill 应急平仓 — 参考社区设计 "shadow_entry_deviation" +
# "shadow_levels_invalid". 思路:
#   open_position 返回 actual_fill 后再做 2 次校验, 任一不过立即应急平仓.
#   pre-check (bookTicker + 动态滑点) 是开仓前防御, post-fill 是兜底 — 即使
#   pre-check 放行, 真实成交价仍可能偏离过大 (限价 partial fill, 市价异常滑点).
#
# 校验 1: 入场偏离应急
#   |actual_fill - paper_entry| / paper_entry × 10000 > LIVE_POST_FILL_MAX_DEVIATION_BPS
#   触发: 立即应急平 + close_reason="entry_deviation_too_high"
#   200 bps (2%) 阈值: 高于任何 pre-check 阈值 (intensity=3 max 200bps) 的兜底,
#   只在真正灾难性偏离时才触发, 不与 Phase 4.V 冲突.
#
# 校验 2: TP/SL 结构有效性
#   LONG  必须满足: paper_sl < actual_fill < paper_tp1 < paper_tp2
#   SHORT 必须满足: paper_sl > actual_fill > paper_tp1 > paper_tp2
#   触发: 立即应急平 + close_reason="post_fill_structure_invalid"
#   场景: fill 价剧烈滑点导致已在 TP 区, 继续持仓就是"立即 TP1 ≈0 收益".
LIVE_POST_FILL_MAX_DEVIATION_BPS = 200.0


def _validate_post_fill_structure(side: str, fill: float, sl: float,
                                    tp1: float, tp2: float) -> bool:
    """Phase 5.G: 校验 fill 价是否落在 paper SL/TP 结构内的"可交易"区间.

    LONG  (BUY):  sl < fill < tp2  (高于 SL 不会瞬触止损, 低于 TP2 还有利润空间)
    SHORT (SELL): sl > fill > tp2

    任一字段缺/异常 (= 0) 返 True (向后兼容, 跳过校验).

    Phase 5.G-fix (5/30): 不再要求 fill < tp1.
    BIOUSDT 案例显示 fill 比 tp1 仅高 1.4bps 也会触发, 死循环每 5s 应急平.
    实际上 fill 在 tp1 之上 = 进 Phase B 早一点 (SL → entry), 不是灾难.
    真正灾难是 fill 已在 SL 区 (insta-SL) 或 TP2 之上 (无利润空间).
    """
    if not (fill > 0 and sl > 0 and tp2 > 0):
        return True   # 字段不全, 跳过校验避免误平
    side = (side or "").upper()
    if side == "BUY":
        return sl < fill < tp2
    elif side == "SELL":
        return sl > fill > tp2
    return True   # 未知 side, 跳过


def _check_circuit_breaker(live_state: dict, now: datetime) -> tuple:
    """Phase 5.E: 检查是否触发连损熔断, 或仍在暂停期内.

    Returns (paused: bool, until_iso: Optional[str]).
    若 paused=True, 上层应跳过 mirror_open (但 sync/close 照常).
    """
    # 1. 仍在暂停期内?
    paused_until_iso = live_state.get("circuit_breaker_paused_until")
    if paused_until_iso:
        try:
            until = datetime.fromisoformat(
                str(paused_until_iso).replace("Z", "+00:00")
            )
            if now < until:
                return True, paused_until_iso
            # 暂停结束, 清掉标记
            live_state["circuit_breaker_paused_until"] = None
        except (ValueError, TypeError):
            live_state["circuit_breaker_paused_until"] = None

    # 2. 统计 window 内 hit_sl 数量
    cutoff = now - timedelta(minutes=LIVE_CB_WINDOW_MIN)
    recent_sl = 0
    for t in (live_state.get("live_closed_trades") or []):
        cr = t.get("close_reason", "")
        # 计入: sl_breach_client (live 自己 SL polling 触发)
        #      paper:hit_sl (mirror 了 paper 的 SL close)
        # 不计: paper:hit_b_trail / paper:hit_trail / timeout / already_closed_externally
        if cr in ("sl_breach_client", "paper:hit_sl"):
            ca = t.get("closed_at")
            if not ca: continue
            try:
                closed_dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                if closed_dt >= cutoff:
                    recent_sl += 1
            except (ValueError, TypeError):
                continue

    # 3. 是否触发?
    if recent_sl >= LIVE_CB_SL_THRESHOLD:
        until = now + timedelta(minutes=LIVE_CB_PAUSE_MIN)
        until_iso = until.isoformat()
        live_state["circuit_breaker_paused_until"] = until_iso
        log.warning(
            f"🛑 [circuit-breaker] {recent_sl} SL in last {LIVE_CB_WINDOW_MIN}min "
            f">= threshold {LIVE_CB_SL_THRESHOLD} → 暂停 mirror_open {LIVE_CB_PAUSE_MIN}min "
            f"(until {until_iso})"
        )
        return True, until_iso

    return False, None


def _record_missed_signal(live: dict, paper_trade: dict,
                          reason: str, now: datetime) -> None:
    """记录 missed signal (paper 出钻石但 live 没 mirror). 去重 by paper_id.

    "already mirrored" 是正常状态, 不算 missed (跳过).
    """
    paper_id = paper_trade.get("id", "")
    if not paper_id:
        return
    if reason and "already mirrored" in reason:
        return
    missed = live.setdefault("missed_signals", [])
    # 去重: 同 paper_id 保留最新 reason
    missed[:] = [m for m in missed if m.get("paper_id") != paper_id]
    missed.append({
        "paper_id": paper_id,
        "symbol": paper_trade.get("symbol"),
        "direction": paper_trade.get("direction"),
        "conviction_score": paper_trade.get("conviction_score"),
        "signal_at": paper_trade.get("entered_at"),
        "reason": reason,
        "last_checked_at": now.isoformat(),
    })
    if len(missed) > MISSED_SIGNALS_KEEP_LAST_N:
        del missed[:-MISSED_SIGNALS_KEEP_LAST_N]


def _prune_obsolete_missed(live: dict, paper_open_ids: set) -> None:
    """清理已不 actionable 的 missed 记录:
       - paper 已平仓 (不在 open list 里)
       - 后来被成功 mirror (在 mirrored_paper_ids 里)
    """
    mirrored = set(live.get("mirrored_paper_ids") or [])
    missed = live.get("missed_signals") or []
    live["missed_signals"] = [
        m for m in missed
        if m.get("paper_id") in paper_open_ids
        and m.get("paper_id") not in mirrored
    ]


def load_paper_state() -> dict:
    """读 paper trader 发布的 state. 返回最小可用 dict."""
    if not PAPER_HISTORY.exists():
        log.warning(f"paper state file not found: {PAPER_HISTORY}")
        return {"open_trades": [], "recent_closed": []}
    try:
        data = json.loads(PAPER_HISTORY.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.error("paper state is not a dict, ignoring")
            return {"open_trades": [], "recent_closed": []}
        data.setdefault("open_trades", [])
        data.setdefault("recent_closed", [])
        return data
    except Exception as e:
        log.error(f"failed to load paper state: {e}")
        return {"open_trades": [], "recent_closed": []}


def load_live_state() -> dict:
    """读 live trader 自己的持久化 state. 缺失/损坏返回空 state."""
    if not LIVE_STATE.exists():
        log.info(f"live state not found, creating: {LIVE_STATE}")
        return _empty_live_state()
    try:
        data = json.loads(LIVE_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.error("live state is not a dict, starting fresh")
            return _empty_live_state()
        # 容错 schema migration
        data.setdefault("version", STATE_VERSION)
        data.setdefault("live_open_trades", [])
        data.setdefault("live_closed_trades", [])
        data.setdefault("mirrored_paper_ids", [])
        data.setdefault("missed_signals", [])
        data.setdefault("last_update", None)
        data.setdefault("session_started_at",
                        datetime.now(timezone.utc).isoformat())
        return data
    except Exception as e:
        log.error(f"failed to load live state, starting fresh: {e}")
        return _empty_live_state()


def save_live_state(state: dict) -> None:
    """原子写 state. .tmp → rename pattern."""
    LIVE_STATE.parent.mkdir(parents=True, exist_ok=True)
    state["last_update"] = datetime.now(timezone.utc).isoformat()
    # 滚动 mirrored_paper_ids (保留最近 N)
    mids = state.get("mirrored_paper_ids", [])
    if len(mids) > MIRRORED_IDS_KEEP_LAST_N:
        state["mirrored_paper_ids"] = mids[-MIRRORED_IDS_KEEP_LAST_N:]
    tmp = LIVE_STATE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(LIVE_STATE)


# ============================================================================
# Mirror logic
# ============================================================================

def _trade_age_sec(paper_trade: dict, now: datetime) -> Optional[float]:
    """Paper trade 开仓至今多久 (秒). 解析失败返回 None."""
    entered = paper_trade.get("entered_at") or paper_trade.get("opened_at")
    if not entered:
        return None
    try:
        dt = datetime.fromisoformat(str(entered).replace("Z", "+00:00"))
        return (now - dt).total_seconds()
    except Exception:
        return None


def _funding_signal(paper_trade: dict) -> str:
    """Phase 4.M: 根据 paper 信号 funding_rate_pct 判定 funding 类型.

    Returns:
        "favorable"  — funding_rate_pct ≤ LIVE_FUNDING_FAVORABLE_THRESHOLD_PCT (-0.05%).
                       paper 数据: 人均 +$3.97/笔 (LONG +$3.89, SHORT +$4.15)
        "adverse"    — funding_rate_pct ≥ LIVE_FUNDING_ADVERSE_THRESHOLD_PCT (+0.05%).
                       paper 数据: 人均 -$0.85/笔 (LONG -$0.75, SHORT -$1.13)
        "neutral"    — |funding| < 0.05%, 或字段缺失.
                       paper 数据: 人均 +$0.57/笔 (基线)
    """
    f = paper_trade.get("funding_rate_pct")
    if f is None:
        return "neutral"
    try:
        f = float(f)
    except (ValueError, TypeError):
        return "neutral"
    if f <= LIVE_FUNDING_FAVORABLE_THRESHOLD_PCT:
        return "favorable"
    if f >= LIVE_FUNDING_ADVERSE_THRESHOLD_PCT:
        return "adverse"
    return "neutral"


def is_eligible_for_mirror(
    paper_trade: dict, live_state: dict, now: datetime,
    btc_regime: Optional[str] = None,
    btc_sub_regime: Optional[str] = None,
    btc_change_3h_pct: Optional[float] = None,
) -> tuple:
    """检查 paper trade 是否应在 live mirror.

    Args:
        paper_trade: paper 信号
        live_state: live 当前状态
        now: 当前时刻
        btc_regime: 可选, 当前 BTC regime ('up'/'chop'/'down'). 用于 Phase 4.F
                    regime gate. 不传则跳过此 gate (向后兼容).
        btc_sub_regime: 可选, Phase 4.K sub_regime ('down_acute'/'down_stable'/
                    'down_rebound'). 仅用于丰富拒绝原因的日志/missed_signal 记录,
                    **不影响 gate 决策** (gate 仍然 down+LONG 一律拒).
        btc_change_3h_pct: 可选, BTC 过去 3 小时收盘价变化百分比. 同上, 仅 log.

    Returns (eligible: bool, reason: str).
    """
    paper_id = paper_trade.get("id", "")
    if not paper_id:
        return False, "paper trade missing id"
    # 1. 已 mirror 过
    if paper_id in live_state.get("mirrored_paper_ids", []):
        return False, "already mirrored"
    # Symbol 规范化为大写, 避免黑/白名单 case-sensitive 比较失败.
    # paper 通常用大写, 但防御性规范化更稳 (与 _compute_pre_entry_slippage_bps 一致).
    sym = (paper_trade.get("symbol") or "").upper()
    # 2a. Symbol 黑名单 (Phase 4.B) — 优先于一切 symbol filter, 即使 OBS mode 也拒.
    if sym in LIVE_SYMBOL_BLACKLIST:
        return False, f"symbol {sym} in live blacklist (历史 0 胜)"
    # 2a'. Phase 4.W: 本 session 中 -1121 (Invalid symbol) 的合约 — session 内跳过,
    #      不写永久 blacklist (主网可能有效).
    if sym in _EXCHANGE_UNAVAILABLE_SYMBOLS:
        return False, f"symbol {sym} not available on exchange (-1121, session skip)"
    # 2b. Symbol 白名单 (observation mode 下跳过, 让我们看 mirror 真实流程)
    if not LIVE_OBSERVATION_MODE and sym not in LIVE_SYMBOL_WHITELIST:
        return False, f"symbol {sym} not in live whitelist {LIVE_SYMBOL_WHITELIST}"
    # 2c. Phase 4.H: Conviction filter — 仅 mirror 高分位钻石信号.
    # 在 symbol filter 之后, 资源占用前的最早位置拒绝低分信号.
    # LIVE_MIN_CONVICTION_SCORE 为 None 或 0 时跳过 (退路).
    if LIVE_MIN_CONVICTION_SCORE:
        raw_conv = paper_trade.get("conviction_score")
        try:
            conv_val = int(raw_conv) if raw_conv is not None else None
        except (ValueError, TypeError):
            conv_val = None
        if conv_val is None:
            return False, f"conviction_score 缺失或非数 ({raw_conv!r}), 拒绝 mirror"
        if conv_val < LIVE_MIN_CONVICTION_SCORE:
            return False, (f"conviction_score {conv_val} < threshold "
                          f"{LIVE_MIN_CONVICTION_SCORE} (Phase 4.H filter)")
    # 3. 并发上限
    current_open = len(live_state.get("live_open_trades", []))
    if current_open >= LIVE_MAX_CONCURRENT:
        return False, f"max_concurrent reached ({current_open}/{LIVE_MAX_CONCURRENT})"
    # 4. Phase 3.3.b: 单 symbol 最多 1 笔 (不分方向, 防对冲 / 重复曝光)
    existing_symbols = {
        lt.get("symbol", "")
        for lt in (live_state.get("live_open_trades") or [])
    }
    if sym in existing_symbols:
        return False, f"symbol {sym} already has open live position (1-per-symbol cap)"
    # 5. Trade 太旧 (启动时陈年 paper trades 不 mirror)
    age = _trade_age_sec(paper_trade, now)
    if age is not None and age > LIVE_MIRROR_MAX_AGE_SEC:
        return False, f"paper trade too old ({age:.0f}s > {LIVE_MIRROR_MAX_AGE_SEC}s)"
    # 6. Direction 有效
    direction = paper_trade.get("direction", "").upper()
    if direction not in ("LONG", "SHORT"):
        return False, f"invalid direction {direction!r}"
    # 7. Phase 4.F regime gate (Phase 4.J 后默认 always — 全部组适用)
    #    Phase 5.R: 传入 sub_regime, 命中 LIVE_REGIME_GATE_SUB_REGIME_ALLOW 则豁免
    # 只在 btc_regime 提供时才检查; 测试 / 旧调用不带此参数时跳过 (向后兼容)
    if btc_regime is not None and _ab_use_regime_gate(paper_id, LIVE_REGIME_GATE_MODE):
        if _should_block_for_regime(direction, btc_regime, btc_sub_regime):
            mode = LIVE_REGIME_GATE_MODE
            # Phase 4.K Shadow Log: 拒绝原因附带 sub_regime + 3h 动量,
            # 便于事后从 missed_signals 反查"down_rebound 时段被拒的 paper 信号"
            # 真实表现 (paper 最终 PnL), 决定是否要放开 rebound 子状态.
            sub_info = ""
            if btc_sub_regime:
                sub_info = f" sub={btc_sub_regime}"
                if btc_change_3h_pct is not None:
                    sub_info += f" 3h={btc_change_3h_pct:+.2f}%"
            return False, (f"regime gate ({mode}): {btc_regime} regime + {direction} "
                          f"被拒{sub_info} (数据驱动: down+LONG 历史 0/10 胜 p=0.042)")
        # Phase 5.R: gate 放行 down+LONG 时不在此处 log — is_eligible_for_mirror
        # 每 tick 被调多次 (POLL_INTERVAL_SEC=5, 信号留存 10 分钟 → ~240 行 spam/signal).
        # 真正的 [regime-gate-allow] log 在 _try_mirror_open 实际开仓时打,
        # 通过 btc_sub_regime_at_open 字段 + log 同步, 每个 mirror 只 log 一次.
    # 7b. Phase 5.S: regime size multiplier — 若该桶配置 mult=0.0, 等同于 reject.
    #     仅当 btc_regime 提供且 LIVE_REGIME_SIZE_MULTIPLIER 非空时检查 (向后兼容).
    if btc_regime is not None and LIVE_REGIME_SIZE_MULTIPLIER:
        mult = _regime_size_multiplier(direction, btc_regime, btc_sub_regime)
        if mult <= 0:
            return False, (f"regime size multiplier=0 for ({direction}, "
                          f"{btc_regime}, {btc_sub_regime or '—'}) (Phase 5.S: 该桶停 mirror)")
    # 8. Phase 4.M Funding gate: 拒 funding 不利信号 (≥ +0.05%, 任意方向)
    if LIVE_REJECT_ADVERSE_FUNDING:
        fs = _funding_signal(paper_trade)
        if fs == "adverse":
            f_pct = paper_trade.get("funding_rate_pct")
            return False, (f"funding adverse ({f_pct}% ≥ {LIVE_FUNDING_ADVERSE_THRESHOLD_PCT}%) "
                          f"(Phase 4.M: paper 历史人均 -$0.85, 不利 mirror)")
    # Phase 4.T 审计后撤销 (5/25):
    # 设计意图是"1h 涨幅 >8% 时禁止 SHORT", 但数据审计显示:
    #   - 历史 171 笔 SHORT, change_1h_pct 最高仅 +0.58%, 从未超过 1%
    #   - velocity scanner 方向对齐检查已在上游过滤反向信号
    #   - 8% 阈值是永不触发的死条件, 提供虚假安全感
    # 真正的反向过滤保护在 scanner 上游. 若未来 paper_trade 加入 change_4h_pct
    # 字段, 可以用 4h 趋势 (更稳健) 重新实现此 gate.
    return True, "ok"


def _paper_to_live_side(direction: str) -> str:
    """Paper 用 LONG/SHORT, Binance 用 BUY/SELL."""
    return "BUY" if direction.upper() == "LONG" else "SELL"


def _generate_trade_id(paper_id: str) -> str:
    """从 paper_id 生成 live trade_id, 满足 binance_client _validate_trade_id
    (1-25 chars, [a-zA-Z0-9_-]).

    paper_id 例: "BTCUSDT|LONG|2026-05-14T11:08:07.253118+00:00"
    → "live_BTCUSDT_L_1715472487" (use symbol prefix + dir char + timestamp)
    """
    # 解析 paper_id (容错)
    parts = paper_id.split("|")
    if len(parts) >= 3:
        sym = parts[0][:8]   # 截断保险
        dir_char = parts[1][:1].upper()  # L or S
        try:
            dt = datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
            ts = int(dt.timestamp())
        except Exception:
            ts = int(time.time())
    else:
        sym = "X"
        dir_char = "?"
        ts = int(time.time())
    # 控制总长度 < 25
    trade_id = f"L{ts}_{sym[:7]}_{dir_char}"
    # 去掉非 [a-zA-Z0-9_-]
    trade_id = "".join(c for c in trade_id if c.isalnum() or c in ("_", "-"))
    return trade_id[:25]


# ============================================================================
# Phase 3.3.a 风控硬装置 — soft gates (仅 block 新开, 不影响管理已有)
# ============================================================================

def _check_emergency_stop_flag() -> Optional[str]:
    """~/.cresus-emergency-stop 文件存在 → 完全停 (Phase 3.3.b 触发后自动创建).
    人工删文件才能恢复. 优先级最高."""
    if not EMERGENCY_STOP_PATH.exists():
        return None
    try:
        content = EMERGENCY_STOP_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        content = ""
    short = content[:200] if content else "no reason"
    return f"emergency stop flag exists: {short!r}"


def _check_pause_flag() -> Optional[str]:
    """~/.cresus-pause 文件存在 → 手动暂停 (人工新建/删除即可).
    适用场景: 你出门/睡觉/不想 bot 操作时."""
    if not PAUSE_FLAG_PATH.exists():
        return None
    try:
        content = PAUSE_FLAG_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        content = ""
    short = content[:80] if content else ""
    return f"manual pause flag exists: {short!r}" if short else "manual pause"


def _check_cash_reserve(live_state: dict) -> Optional[str]:
    """部署总额 >= LIVE_MAX_DEPLOY_USDT → 不开新仓.
    保护现金缓冲, 防止满仓后任何额外开销/突发情况."""
    deployed = sum(
        float(lt.get("notional_usdt", 0) or 0)
        for lt in (live_state.get("live_open_trades") or [])
    )
    if deployed >= LIVE_MAX_DEPLOY_USDT:
        return (
            f"deployed ${deployed:.2f} >= cap ${LIVE_MAX_DEPLOY_USDT:.2f} "
            f"({LIVE_STARTING_CAPITAL_USDT - LIVE_MAX_DEPLOY_USDT:.0f}% 现金保留触发)"
        )
    return None


def _calculate_daily_realized_pnl(
    live_state: dict, now: datetime,
) -> tuple:
    """汇总 UTC 今日已平仓 trades 的 realized_pnl_usdt.
    Returns: (total_pnl, count, day_start_dt).
    """
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    count = 0
    for lt in (live_state.get("live_closed_trades") or []):
        ca = lt.get("closed_at")
        if not ca:
            continue
        try:
            dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= day_start:
            total += float(lt.get("realized_pnl_usdt", 0) or 0)
            count += 1
    return total, count, day_start


def _check_daily_dd(live_state: dict, now: datetime) -> Optional[str]:
    """日内已实现亏损达 -LIVE_DAILY_DD_LIMIT_USDT → 不开新仓.
    (不含未实现 PnL — 那是 Phase 3.3.b 用 get_account 才能精确计算)"""
    pnl, count, day_start = _calculate_daily_realized_pnl(live_state, now)
    if pnl <= -LIVE_DAILY_DD_LIMIT_USDT:
        return (
            f"daily realized PnL ${pnl:+.2f} <= -${LIVE_DAILY_DD_LIMIT_USDT:.2f} "
            f"({count} closed trades since {day_start.strftime('%Y-%m-%dT%H:%MZ')})"
        )
    return None


def _get_account_balance(client: BinanceClient) -> Optional[float]:
    """获取 totalMarginBalance (含未实现 PnL). API 失败返 None.

    Phase 3.3.b 用. 关键: 用 totalMarginBalance 而非 totalWalletBalance,
    因为前者 = wallet + unrealized profit, 是真实账户净值.
    """
    try:
        account = client.get_account()
    except (BinanceError, ValueError) as e:
        log.warning(f"[recon] get_account failed: {e}")
        return None
    except Exception as e:
        log.error(f"[recon] get_account unexpected: {type(e).__name__}: {e}")
        return None
    try:
        return float(account.get("totalMarginBalance") or 0)
    except (ValueError, TypeError):
        return None


def _check_cumulative_dd_and_trigger(client: BinanceClient) -> Optional[str]:
    """Phase 3.3.b: 累计 DD kill switch.

    查 totalMarginBalance vs LIVE_STARTING_CAPITAL_USDT.
    若 DD >= LIVE_TOTAL_DD_LIMIT_PCT, AUTO-CREATE EMERGENCY_STOP_PATH 文件.
    人工删文件 + 修复底层 (注资 / 减仓) 才能恢复 (因为下次跑还是 trigger).

    Returns: 触发时 reason string, 否则 None.
    """
    balance = _get_account_balance(client)
    if balance is None:
        return None  # API 不可用, 不 block (其他 gate 会兜底)
    threshold = LIVE_STARTING_CAPITAL_USDT * (1 - LIVE_TOTAL_DD_LIMIT_PCT / 100)
    if balance >= threshold:
        return None  # 安全
    dd_pct = (LIVE_STARTING_CAPITAL_USDT - balance) / LIVE_STARTING_CAPITAL_USDT * 100
    reason = (
        f"cumulative DD {dd_pct:.2f}% (balance ${balance:.2f} < "
        f"threshold ${threshold:.2f})"
    )
    # AUTO 写 emergency flag
    try:
        EMERGENCY_STOP_PATH.write_text(
            f"AUTO {datetime.now(timezone.utc).isoformat()}: {reason}",
            encoding="utf-8",
        )
        log.critical(
            f"🚨🚨 KILL SWITCH TRIGGERED: {reason}. "
            f"Emergency stop flag created: {EMERGENCY_STOP_PATH}. "
            f"To recover: 1) 注资或减仓 to bring balance > ${threshold:.2f} "
            f"2) rm {EMERGENCY_STOP_PATH}"
        )
    except Exception as e:
        log.error(f"[KILL] failed to write emergency flag: {e}")
    return reason


def check_position_reconciliation(
    client: BinanceClient, live_state: dict,
) -> dict:
    """Phase 3.3.b: 对账 live state vs exchange.

    比对 symbol (不比 quantity / direction, 简化第一版).
    Returns: {
        'ok': bool (无 mismatch),
        'mismatches': [{'symbol', 'kind', 'message'}, ...],
        'live_symbols': set,
        'exchange_symbols': set,
        'api_failed': bool,  # API 调用失败时 True
    }

    mismatch kinds:
    - 'live_only': live tracks 但 exchange 已无 (可能外部 close)
    - 'exchange_only': exchange 有但 live 不知 (可能用户手动开 / 我们丢 state)
    """
    live_symbols = {
        lt.get("symbol", "")
        for lt in (live_state.get("live_open_trades") or [])
        if lt.get("symbol")
    }
    try:
        positions = client.get_positions()
    except (BinanceError, ValueError) as e:
        log.warning(f"[recon] get_positions failed: {e}")
        return {
            "ok": True, "mismatches": [], "api_failed": True,
            "live_symbols": list(live_symbols), "exchange_symbols": [],
        }
    except Exception as e:
        log.error(f"[recon] get_positions unexpected: {e}")
        return {
            "ok": True, "mismatches": [], "api_failed": True,
            "live_symbols": list(live_symbols), "exchange_symbols": [],
        }

    exchange_symbols = set()
    ignored_zombies = []
    for p in positions:
        try:
            amt = float(p.get("positionAmt") or 0)
        except (ValueError, TypeError):
            continue
        if abs(amt) > 0:
            sym = p.get("symbol", "")
            # Phase 4.W: 跳过 testnet 无法平仓的僵尸合约 (避免持续误报)
            if sym in LIVE_RECON_IGNORE_SYMBOLS:
                ignored_zombies.append(sym)
                continue
            exchange_symbols.add(sym)
    if ignored_zombies:
        log.debug(f"[recon] 忽略僵尸持仓: {ignored_zombies}")

    mismatches = []
    for sym in live_symbols - exchange_symbols:
        mismatches.append({
            "symbol": sym, "kind": "live_only",
            "message": f"live tracks {sym} but exchange has no position "
                       f"(externally closed? or state stale)",
        })
    for sym in exchange_symbols - live_symbols:
        mismatches.append({
            "symbol": sym, "kind": "exchange_only",
            "message": f"exchange has {sym} position but live doesn't track "
                       f"(user manual? or state lost)",
        })
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "api_failed": False,
        "live_symbols": list(live_symbols),
        "exchange_symbols": list(exchange_symbols),
    }


def _compute_live_stats(live_state: dict) -> dict:
    """计算 dashboard 用的统计数字 (类似 paper trader 的 stats).

    仅含 realized PnL — unrealized 由 dashboard 端按当前价计算.
    """
    closed = live_state.get("live_closed_trades") or []
    opens = live_state.get("live_open_trades") or []

    wins = [t for t in closed if (t.get("realized_pnl_usdt", 0) or 0) > 0.001]
    losses = [t for t in closed if (t.get("realized_pnl_usdt", 0) or 0) < -0.001]
    decisive = wins + losses
    win_rate = (len(wins) / len(decisive)) if decisive else 0.0
    total_pnl = sum(float(t.get("realized_pnl_usdt", 0) or 0) for t in closed)
    avg_pnl = (total_pnl / len(closed)) if closed else 0.0
    # 已实现费用 — 仅来自已平仓 (entry+close 都已记录)
    fees_realized = sum(
        float(t.get("fees_paid_usdt", 0) or 0) for t in closed
    )
    # 未实现 (持仓中只产生 entry fee, close fee 还未发生)
    fees_open = sum(
        float(t.get("fees_paid_usdt", 0) or 0) for t in opens
    )
    fees_total = fees_realized + fees_open
    net_pnl = total_pnl - fees_realized
    fees_all_actual = all(
        bool(t.get("fees_are_actual", False))
        for t in list(closed) + list(opens)
    ) if (closed or opens) else True
    best_trade = max(
        (float(t.get("realized_pnl_usdt", 0) or 0) for t in closed),
        default=0.0,
    )
    worst_trade = min(
        (float(t.get("realized_pnl_usdt", 0) or 0) for t in closed),
        default=0.0,
    )
    deployed = sum(float(t.get("notional_usdt", 0) or 0) for t in opens)

    return {
        "total_trades": len(closed) + len(opens),
        "open": len(opens),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3),
        "total_pnl_usdt": round(total_pnl, 4),
        "net_pnl_usdt": round(net_pnl, 4),
        "avg_pnl_usdt": round(avg_pnl, 4),
        "best_trade_usdt": round(best_trade, 4),
        "worst_trade_usdt": round(worst_trade, 4),
        "fees_paid_usdt": round(fees_total, 4),
        "fees_realized_usdt": round(fees_realized, 4),
        "fees_open_usdt": round(fees_open, 4),
        "fees_are_actual": fees_all_actual,
        "starting_capital_usdt": LIVE_STARTING_CAPITAL_USDT,
        "deployed_usdt": round(deployed, 2),
        # free_capital: 钱包余额视角 — 起始 − 当前部署 + 已实现毛利 − 全部已付费用
        # (持仓中的开仓费也已从钱包扣过, 必须减; 否则可用资金虚高)
        "free_capital_usdt": round(LIVE_STARTING_CAPITAL_USDT
                                    - deployed + total_pnl - fees_total, 2),
        "max_concurrent_slots": LIVE_MAX_CONCURRENT,
        "slots_used": len(opens),
    }


def publish_live_history(
    live_state: dict, *,
    risk: Optional[dict] = None,
    recon: Optional[dict] = None,
) -> bool:
    """Phase 3.2.c: 发布对外可见的 live trades history (供 dashboard 读取).

    类比 paper trader 的 _save_paper_history. 原子写 LIVE_HISTORY 文件.
    Returns: True 若成功写入, False 失败.
    """
    stats = _compute_live_stats(live_state)
    closed = sorted(
        live_state.get("live_closed_trades") or [],
        key=lambda t: t.get("closed_at", ""),
        reverse=True,
    )
    payload = {
        "version": STATE_VERSION,
        "system_version": SYSTEM_VERSION,   # V3 / V4 系统代号 (清晰分辨)
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_started_at": live_state.get("session_started_at"),
        "stats": stats,
        "risk_status": risk or {
            "block_new_opens": False, "reasons": [],
            "daily_pnl": 0.0, "deployed_usdt": 0.0,
        },
        "reconciliation": recon or {
            "ok": True, "mismatches": [], "api_failed": False,
            "live_symbols": [], "exchange_symbols": [],
        },
        "open_trades": list(live_state.get("live_open_trades") or []),
        "recent_closed": list(closed),
        # 按 last_checked_at 倒序, 最近的在前
        "missed_signals": sorted(
            list(live_state.get("missed_signals") or []),
            key=lambda m: m.get("last_checked_at", ""),
            reverse=True,
        ),
        # Phase 4.C 当前 BTC regime (最近一次 main_loop tick 计算的 snapshot)
        "btc_regime_now": live_state.get("_btc_regime_now"),
        "config": {
            "starting_capital_usdt": LIVE_STARTING_CAPITAL_USDT,
            "notional_per_trade_usdt": LIVE_NOTIONAL_USDT,
            "leverage": LIVE_LEVERAGE,
            "max_concurrent": LIVE_MAX_CONCURRENT,
            "symbol_whitelist": list(LIVE_SYMBOL_WHITELIST),
            "symbol_blacklist": list(LIVE_SYMBOL_BLACKLIST),
            "daily_dd_limit_usdt": LIVE_DAILY_DD_LIMIT_USDT,
            "max_deploy_usdt": LIVE_MAX_DEPLOY_USDT,
            "total_dd_limit_pct": LIVE_TOTAL_DD_LIMIT_PCT,
            "mirror_max_age_sec": LIVE_MIRROR_MAX_AGE_SEC,
            "max_entry_slippage_bps": LIVE_MAX_ENTRY_SLIPPAGE_BPS,
            "sl_compensation_mode": LIVE_SL_COMPENSATION_MODE,
            "wick_filter_mode": LIVE_SL_WICK_FILTER_MODE,
            "wick_filter_min_breaches": LIVE_WICK_FILTER_MIN_BREACHES,
            "regime_gate_mode": LIVE_REGIME_GATE_MODE,
            "min_conviction_score": LIVE_MIN_CONVICTION_SCORE,
            "observation_mode": LIVE_OBSERVATION_MODE,
        },
    }
    try:
        LIVE_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIVE_HISTORY.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(LIVE_HISTORY)
        return True
    except Exception as e:
        log.error(f"publish_live_history failed: {e}")
        return False


def check_risk_gates(
    live_state: dict, now: datetime, *,
    client: Optional[BinanceClient] = None,
) -> dict:
    """Phase 3.3.a/b 软+硬门聚合检查. 只 block 新开仓, 不影响已有持仓管理.

    Args:
        live_state: 本地 state.
        now: 当前 UTC 时间.
        client: 可选; 提供后启用 cumulative DD 检查 (需 API). 不提供则跳过该检查
                (向后兼容; 适合单测).

    Returns: {
        'block_new_opens': bool,
        'reasons': [str, ...],
        'daily_pnl': float,
        'deployed_usdt': float,
    }
    """
    reasons = []
    # 优先级 1: emergency stop (Phase 3.3.b 累计 DD 自动写, 人工删)
    msg = _check_emergency_stop_flag()
    if msg: reasons.append(msg)
    # 优先级 2: 累计 DD (Phase 3.3.b, 触发时自动写 emergency 文件)
    if client is not None:
        msg = _check_cumulative_dd_and_trigger(client)
        if msg: reasons.append(msg)
    # 优先级 3: 手动 pause (人工随时新建/删)
    msg = _check_pause_flag()
    if msg: reasons.append(msg)
    # 优先级 4: 现金保留
    msg = _check_cash_reserve(live_state)
    if msg: reasons.append(msg)
    # 优先级 5: 日 DD
    msg = _check_daily_dd(live_state, now)
    if msg: reasons.append(msg)

    daily_pnl, _, _ = _calculate_daily_realized_pnl(live_state, now)
    deployed = sum(
        float(lt.get("notional_usdt", 0) or 0)
        for lt in (live_state.get("live_open_trades") or [])
    )
    return {
        "block_new_opens": bool(reasons),
        "reasons": reasons,
        "daily_pnl": round(daily_pnl, 4),
        "deployed_usdt": round(deployed, 2),
    }


def _get_current_price(client: BinanceClient, symbol: str) -> Optional[float]:
    """获取 symbol 当前价 (用 1m 最新 kline). 失败返 None."""
    try:
        klines = client.get_klines(symbol, interval="1m", limit=1)
        if not klines:
            return None
        return float(klines[0][4])  # close price
    except (BinanceError, ValueError, IndexError, TypeError) as e:
        log.warning(f"[get_price] {symbol}: {type(e).__name__}: {e}")
        return None


def _get_entry_reference_price(
    client: BinanceClient, symbol: str, direction: str,
) -> Optional[float]:
    """获取开仓侧实时基准价 (盘口最优价, <1s).

    LONG (市价 BUY)  → 用 askPrice (我们要吃卖一)
    SHORT (市价 SELL) → 用 bidPrice (我们要打买一)

    失败时 fallback 到 1m kline close (旧行为). fallback 路径会带 0-60s
    滞后, 但保证 fail-safe (返 None 让上层放行而非误拒).
    """
    try:
        bt = client.get_book_ticker(symbol)
        if direction == "LONG":
            px = float(bt.get("askPrice") or 0)
        else:
            px = float(bt.get("bidPrice") or 0)
        if px > 0:
            return px
    except (BinanceError, ValueError, TypeError, KeyError) as e:
        log.warning(f"[book_ticker] {symbol}: {type(e).__name__}: {e}, "
                    f"fallback 1m kline")
    return _get_current_price(client, symbol)


def _compute_pre_entry_slippage_bps(
    client: BinanceClient, paper_trade: dict,
) -> Optional[float]:
    """计算 paper 信号价 → 当前开仓侧实时价 的预滑点 (bps).

    Phase 4.U (5/25): 改用盘口最优价 (askPrice for LONG, bidPrice for SHORT)
    替代 1m kline close, 消除 0-60s 价格滞后. 修复 SAGAUSDT 07:32 等
    "gate 假通过但实际成交滑点远超阈值" 的场景.

    返回值约定:
        正 bps = 不利 (开仓即吃亏)
        负 bps = 有利 (价格朝我们方向走了)
        None   = 无法计算 (字段缺 / API 失败 / 数据异常) → 调用方应"放行"不拒绝

    侧向 (LONG / SHORT) 归一化:
        LONG (BUY):  current > paper → 不利 (买贵了),  raw bps × +1
        SHORT (SELL): current < paper → 不利 (卖便宜了), raw bps × -1
    """
    sym = (paper_trade.get("symbol") or "").upper()
    direction = (paper_trade.get("direction") or "").upper()
    if not sym or direction not in ("LONG", "SHORT"):
        return None
    try:
        paper_entry = float(paper_trade.get("entry_price") or 0)
    except (TypeError, ValueError):
        return None
    if paper_entry <= 0:
        return None
    current = _get_entry_reference_price(client, sym, direction)
    if current is None or current <= 0:
        return None
    side_sign = 1 if direction == "LONG" else -1
    return (current - paper_entry) / paper_entry * 10000.0 * side_sign


def _ab_group(paper_id: str, n_groups: int = 4) -> str:
    """Phase 4.E/4.F: paper_id 哈希到 A/B/C/D 多组 (MD5 mod n).

    n_groups=3: Phase 4.E (A/B/C), 各 ~1/3
    n_groups=4: Phase 4.F 默认 (A/B/C/D), 各 ~1/4

    用途: 让多个独立特性共享同一组别系统, 保证统计上不会产生交叉污染.
    A 永远是基线, B/C/D 各承载一个独立特性.

    Returns: 'A' | 'B' | 'C' | 'D'. 空 paper_id 返 'A' (基线安全退路).
    """
    if not paper_id:
        return "A"
    if n_groups < 1 or n_groups > 4:
        n_groups = 4
    h = int(hashlib.md5(paper_id.encode("utf-8")).hexdigest(), 16)
    return ["A", "B", "C", "D"][h % n_groups]


def _should_block_for_regime(
    direction: str,
    regime: Optional[str],
    sub_regime: Optional[str] = None,
) -> bool:
    """Phase 4.F regime gate 核心规则 (Phase 5.R: sub_regime aware).

    Args:
        direction: paper trade 方向, 'LONG' / 'SHORT' (会 upper())
        regime: 当前 BTC regime, 'up' / 'chop' / 'down' / None
        sub_regime: 可选, Phase 4.K BTC down 内子状态. 当
                    sub_regime ∈ LIVE_REGIME_GATE_SUB_REGIME_ALLOW 且
                    regime=down + direction=LONG 时, 豁免本 gate (返 False).
                    Default = None → 等同未传, 不豁免 (fail-safe).

    Returns:
        True 表示该 (regime, direction) 组合应该被拒绝 mirror.

    当前规则 (Phase 5.R):
        - down + LONG + sub_regime ∈ LIVE_REGIME_GATE_SUB_REGIME_ALLOW → False (放行)
        - down + LONG (其它 sub_regime 或 None)                        → True  (拒)
        - 其余组合                                                     → False
        默认 ALLOW set = 空, 行为与 Phase 4.J 完全一致.

    设计原则:
        - 单功能: 只判规则, 不查 A/B/C/D 分组 (由 caller 判定是否该应用此规则)
        - 防御性: 永远只能拒绝, 不能误开仓; allow-list 必须显式启用
        - 易扩展: 未来若数据证明 chop+SHORT 也该拒, 只需改这一处
    """
    if not regime or not direction:
        return False
    d = direction.upper()
    r = regime.lower()
    if r == "down" and d == "LONG":
        # Phase 5.R: sub_regime 命中 allow-list → 豁免本 gate
        # 注意: sub_regime 必须非空才能匹配, None 永远不被豁免
        if sub_regime and sub_regime in LIVE_REGIME_GATE_SUB_REGIME_ALLOW:
            return False
        return True
    return False


def _get_effective_slippage_threshold(paper_trade: dict) -> float:
    """动态预滑点阈值: 按 paper_trade.intensity 查 LIVE_SLIPPAGE_THRESHOLD_BY_INTENSITY.

    intensity=3 → 200 bps  (高速急拉, XANUSDT/SAGAUSDT 型)
    intensity=2 → 150 bps  (中等动量)
    intensity=1 → 100 bps  (低动量 / 默认, 与 v3 100bps 数据分析一致)
    字段缺 / 非整数 → 100 bps  (fail-safe, 与 LIVE_MAX_ENTRY_SLIPPAGE_BPS 对齐)
    """
    try:
        inten = int(paper_trade.get("intensity") or 1)
    except (TypeError, ValueError):
        inten = 1
    return LIVE_SLIPPAGE_THRESHOLD_BY_INTENSITY.get(inten, LIVE_MAX_ENTRY_SLIPPAGE_BPS)


def _ab_use_sl_compensation(paper_id: str, mode: str = None) -> bool:
    """Phase 4.D SL 补偿启用判定.

    mode='off':    永远 False
    mode='always': 永远 True
    mode='ab':     legacy 2-arm — MD5 50/50 分组 (兼容 Phase 4.D 老数据)
    mode='abc':    Phase 4.E 3-arm — B 组启用补偿, A/C 组不启用

    用 hashlib.md5 而非 Python 内置 hash() — 后者跨进程值不稳 (PYTHONHASHSEED).
    """
    if mode is None:
        mode = LIVE_SL_COMPENSATION_MODE
    if mode == "off":
        return False
    if mode == "always":
        return True
    if mode == "ab":
        if not paper_id:
            return False
        h = int(hashlib.md5(paper_id.encode("utf-8")).hexdigest(), 16)
        return (h % 2) == 0
    if mode == "abc":
        return _ab_group(paper_id, n_groups=3) == "B"
    if mode == "abcd":
        return _ab_group(paper_id, n_groups=4) == "B"
    log.warning(f"[ab-sl] unknown mode={mode!r}, fallback to off")
    return False


def _ab_use_wick_filter(paper_id: str, mode: str = None) -> bool:
    """Phase 4.E/4.F: SL wick 过滤启用判定 (C 组).

    mode='off':    永远 False (legacy 行为)
    mode='always': 永远 True
    mode='abc':    Phase 4.E 3-arm — C 组启用过滤
    mode='abcd':   Phase 4.F 4-arm — C 组启用过滤 (D 组转用 regime gate)

    启用后, _check_sl_breach 要求连续 LIVE_WICK_FILTER_MIN_BREACHES 次轮询
    都越 SL 才返回 True. 单次 wick 不再触发.
    """
    if mode is None:
        mode = LIVE_SL_WICK_FILTER_MODE
    if mode == "off":
        return False
    if mode == "always":
        return True
    if mode == "abc":
        return _ab_group(paper_id, n_groups=3) == "C"
    if mode == "abcd":
        return _ab_group(paper_id, n_groups=4) == "C"
    log.warning(f"[ab-wick] unknown mode={mode!r}, fallback to off")
    return False


def _ab_use_regime_gate(paper_id: str, mode: str = None) -> bool:
    """Phase 4.F: BTC regime gate 启用判定 (D 组).

    mode='off':    永远 False (legacy / 不启用)
    mode='always': 永远 True (全员启用 gate, 适合数据足够后切换)
    mode='abcd':   4-arm — D 组启用 regime gate, A/B/C 组不启用

    启用后, is_eligible_for_mirror 会在 _should_block_for_regime 返 True 时
    拒绝该 trade. 当前规则: down regime + LONG 被拒.
    """
    if mode is None:
        mode = LIVE_REGIME_GATE_MODE
    if mode == "off":
        return False
    if mode == "always":
        return True
    if mode == "abcd":
        return _ab_group(paper_id, n_groups=4) == "D"
    log.warning(f"[ab-regime] unknown mode={mode!r}, fallback to off")
    return False


def _compute_compensated_sl(
    paper_sl: float, live_entry: float, paper_entry: float,
) -> Optional[float]:
    """补偿后的 SL: live_sl = paper_sl + (live_entry - paper_entry).

    保持 SL 跟 actual entry 的距离 = paper 设计距离.
    Returns None 如任何输入异常 (caller 应 fallback 到原 paper_sl).
    """
    try:
        psl = float(paper_sl)
        le = float(live_entry)
        pe = float(paper_entry)
    except (TypeError, ValueError):
        return None
    if psl <= 0 or le <= 0 or pe <= 0:
        return None
    return psl + (le - pe)


def _compute_btc_regime(client: BinanceClient) -> Optional[dict]:
    """计算当前 BTC 市场 regime (up / chop / down), 供 trade 开仓时打标签.

    用 BTCUSDT 1h K 线 MA(25) 作 baseline (~24h 滚动均值, 匹配短线视角):
        current vs MA25 >= +LIVE_BTC_REGIME_THRESHOLD_PCT %  → up
        current vs MA25 <= -LIVE_BTC_REGIME_THRESHOLD_PCT %  → down
        其他                                                  → chop

    返回 dict (含 regime + 上下文供复盘), 任何失败返 None (调用方应忽略, 不阻止 mirror).

    本函数不做交易决策, 不影响 gate 行为 — 纯观察标签.
    """
    try:
        klines = client.get_klines("BTCUSDT", interval="1h", limit=25)
    except (BinanceError, ValueError) as e:
        log.warning(f"[btc-regime] get_klines failed: {type(e).__name__}: {e}")
        return None
    if not klines or len(klines) < 25:
        log.warning(f"[btc-regime] insufficient klines: {len(klines) if klines else 0}/25")
        return None
    try:
        closes = [float(k[4]) for k in klines]
    except (ValueError, IndexError, TypeError) as e:
        log.warning(f"[btc-regime] klines parse failed: {e}")
        return None
    current = closes[-1]
    if current <= 0:
        return None
    ma25 = sum(closes) / len(closes)
    if ma25 <= 0:
        return None
    pct_vs_ma = (current - ma25) / ma25 * 100.0
    # 24h change: 用 25 根 1h 的首根 close (≈ 24h 前) vs 当前
    first = closes[0]
    change_24h_pct = ((current - first) / first * 100.0) if first > 0 else 0.0
    if pct_vs_ma >= LIVE_BTC_REGIME_THRESHOLD_PCT:
        regime = "up"
    elif pct_vs_ma <= -LIVE_BTC_REGIME_THRESHOLD_PCT:
        regime = "down"
    else:
        regime = "chop"

    # Phase 4.K Shadow Log (2026-05-23): 在 down regime 内细分 sub_regime,
    # 用于观察 down_rebound (近期反弹) 是否真的跟 down_acute/stable 表现不同.
    # 阈值依据用户观察 + 经验值, 后续根据 shadow log 数据调整.
    # 仅作记录用 — Phase 4.J gate 仍然 down+LONG 一律拒, 不分 sub_regime.
    sub_regime = None
    change_3h_pct = None
    if regime == "down" and len(closes) >= 4:
        first_3h = closes[-4]   # 3 小时前的 1h close
        if first_3h > 0:
            change_3h_pct = (current - first_3h) / first_3h * 100.0
            if change_3h_pct < -1.0:
                sub_regime = "down_acute"        # 仍在急跌
            elif change_3h_pct > 0.5:
                sub_regime = "down_rebound"      # 已开始反弹
            else:
                sub_regime = "down_stable"       # 横盘企稳

    return {
        "regime": regime,
        "btc_price": round(current, 2),
        "btc_ma25_1h": round(ma25, 2),
        "pct_vs_ma25": round(pct_vs_ma, 3),
        "change_24h_pct": round(change_24h_pct, 3),
        # Phase 4.K 新字段 (down regime 时填充, 其它为 None)
        "sub_regime": sub_regime,
        "change_3h_pct": round(change_3h_pct, 3) if change_3h_pct is not None else None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def _check_sl_breach(live_trade: dict, current_price: float) -> bool:
    """检查当前价是否触 SL.
    LONG (side=BUY): current ≤ sl 触发
    SHORT (side=SELL): current ≥ sl 触发

    Phase 4.E: 若 wick_filter_enabled, 要求连续 LIVE_WICK_FILTER_MIN_BREACHES 次
    轮询都越 SL 才返回 True. 计数器 'sl_breach_count' 持久化在 live_trade 上
    (跟随 ~/cresus-bot/.live_trades.json 存档), 非 breach 时清零.
    """
    sl = live_trade.get("sl_price")
    if sl is None:
        return False
    sl = float(sl)
    side = live_trade.get("side", "").upper()
    if side == "BUY":
        breach = current_price <= sl
    elif side == "SELL":
        breach = current_price >= sl
    else:
        return False

    # Phase 4.E: C 组用 wick filter, A/B 组保持旧行为 (instant trigger)
    if not live_trade.get("wick_filter_enabled"):
        # 清零计数 (一致性: 即使没启用过滤, 也保证 sl_breach_count 字段不残留)
        if "sl_breach_count" in live_trade and live_trade["sl_breach_count"] != 0:
            live_trade["sl_breach_count"] = 0
        return breach

    # C 组逻辑: 累计连续 breach 次数
    cnt = int(live_trade.get("sl_breach_count") or 0)
    if breach:
        cnt += 1
        live_trade["sl_breach_count"] = cnt
        min_n = int(live_trade.get("wick_filter_min_breaches") or LIVE_WICK_FILTER_MIN_BREACHES)
        return cnt >= min_n
    else:
        # 非 breach: 计数器清零 (单次 wick 之后回归, 不该累积"半 breach"状态)
        if cnt != 0:
            live_trade["sl_breach_count"] = 0
        return False


def _sync_live_with_paper(live_trade: dict, paper_open_trade: dict) -> bool:
    """从 paper 同步 sl_price / phase 到 live_trade (mutates in place).
    Returns: True 若有更新.

    设计逻辑: Live trader 是 paper 的执行层. Paper 内部管理 Phase A/B/C 转换
    (TP1 命中 → SL 移 BE; TP2 → trailing), live 只需 mirror paper 当前的 sl.
    """
    updated = False
    new_sl = paper_open_trade.get("sl")
    new_phase = paper_open_trade.get("phase")
    if new_sl is not None:
        try:
            new_paper_sl = float(new_sl)
            # 更新 paper 当前 SL 记录 (Phase 4.D)
            live_trade["sl_paper_current"] = new_paper_sl
            # Phase 4.D: 若此 trade 是 SL 补偿组, 应用 offset 计算 live SL
            offset = float(live_trade.get("sl_compensation_offset") or 0)
            new_live_sl = new_paper_sl + offset    # offset=0 时退化为旧行为
            if abs(new_live_sl - float(live_trade.get("sl_price", 0))) > 1e-9:
                old = live_trade.get("sl_price")
                live_trade["sl_price"] = new_live_sl
                # Phase 4.E: SL 移动后, 之前的 breach 计数失效, 清零
                if live_trade.get("sl_breach_count"):
                    live_trade["sl_breach_count"] = 0
                comp_note = f" (paper {new_paper_sl} + offset {offset:+.6f})" if offset != 0 else ""
                log.info(
                    f"[sl-sync] {live_trade['symbol']}: {old} → {new_live_sl} "
                    f"(paper phase={new_phase}){comp_note}"
                )
                updated = True
        except (ValueError, TypeError):
            pass
    if new_phase and new_phase != live_trade.get("phase"):
        log.info(
            f"[phase-sync] {live_trade['symbol']}: "
            f"{live_trade.get('phase')} → {new_phase}"
        )
        live_trade["phase"] = new_phase
        updated = True
    # Per-phase MFE: paper 已经在监控 high water mark, 直接拷 (live 不重复计算)
    for k in ("phase_a_mfe_pct", "phase_b_mfe_pct", "phase_c_mfe_pct",
              "phase_a_mfe_price", "phase_b_mfe_price", "phase_c_mfe_price"):
        v = paper_open_trade.get(k)
        if v is not None and live_trade.get(k) != v:
            live_trade[k] = v
            updated = True
    return updated


def _try_mirror_close(
    client: BinanceClient,
    live_trade: dict,
    *,
    reason: str,
    dry_run: bool,
) -> Optional[dict]:
    """关 live position. Returns updated live_trade (含 close 信息), 失败 None."""
    sym = live_trade.get("symbol", "")
    side = live_trade.get("side", "")
    trade_id = live_trade.get("trade_id", "")
    log.info(f"[mirror-close] {sym} {side} reason={reason} trade_id={trade_id}")
    try:
        result = client.close_position(
            symbol=sym, side=side, trade_id=trade_id,
        )
    except (BinanceError, ValueError) as e:
        err_str = str(e)
        # Phase 5.A-fix (5/28): exchange 已无持仓 (外部 close / 之前的手动平仓 /
        # 异常重复 close) → 标记关闭, 不无限重试.
        # binance_client.close_position 抛 2 种"无持仓"信号:
        #   "X 当前无持仓 (positionAmt=0)"  ← 仓位记录在但 amt=0
        #   "无 X 持仓记录"                 ← 仓位完全不在 positions 列表
        no_position = (
            "无持仓" in err_str
            or "positionAmt=0" in err_str
            or "持仓记录" in err_str
        )
        if no_position:
            log.warning(
                f"[mirror-close] {sym}: exchange 已无持仓 — 视为 already_closed_externally, "
                f"清理 live state 不再重试. 原因: {reason}"
            )
            closed = dict(live_trade)
            closed["closed_at"] = datetime.now(timezone.utc).isoformat()
            closed["close_reason"] = "already_closed_externally"
            closed["realized_pnl_usdt"] = 0.0   # 无法精确知道, 外部 close 时 PnL 在 binance 手动结算
            closed["close_qty"] = 0.0
            closed["avg_exit_price"] = 0.0
            return closed
        log.error(f"[mirror-close FAILED] {sym}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        log.error(f"[mirror-close UNEXPECTED] {sym}: {type(e).__name__}: {e}",
                  exc_info=True)
        return None

    # 把 close 信息合并进 live_trade
    closed = dict(live_trade)
    closed["closed_at"] = result.get("closed_at")
    closed["close_reason"] = reason
    exit_price = float(result.get("avg_exit_price") or 0)
    closed["avg_exit_price"] = exit_price
    closed["realized_pnl_usdt"] = float(result.get("realized_pnl_usdt") or 0)
    closed["close_order_id"] = result.get("close_order_id")
    closed["close_qty"] = float(result.get("qty_closed") or 0)

    # 平仓 slippage (期望价 vs 实际成交).
    #   SL 触发: 期望 = sl_price (我们设的止损)
    #   其他情况 (paper closed, timeout): 期望 = 最后一次记录的 current_price (无则跳过)
    expected_exit = None
    if reason == "sl_breach_client":
        expected_exit = float(live_trade.get("sl_price") or 0) or None
    elif live_trade.get("current_price") is not None:
        try:
            expected_exit = float(live_trade["current_price"])
        except (ValueError, TypeError):
            expected_exit = None
    close_slip_bps = None
    if expected_exit and expected_exit > 0 and exit_price > 0:
        side = str(live_trade.get("side", "")).upper()
        # 平仓: BUY (LONG 平出 SELL): 实际 < 期望 = 不利
        # SHORT 平出 BUY: 实际 > 期望 = 不利. 统一 正 bps = 不利.
        raw = (exit_price - expected_exit) / expected_exit * 10000.0
        close_slip_bps = round(-raw if side == "BUY" else raw, 2)
    closed["close_expected_price"] = expected_exit
    closed["close_slippage_bps"] = close_slip_bps

    # 费用聚合: 开仓侧 (live_trade 已有) + 平仓侧 (来自 close 返回)
    entry_fee = float(live_trade.get("fees_paid_usdt") or 0)
    entry_fee_is_actual = bool(live_trade.get("fees_are_actual", False))
    close_fee = float(result.get("fees_paid_usdt") or 0)
    close_fee_is_actual = bool(result.get("fees_are_actual", False))
    closed["entry_fees_usdt"] = round(entry_fee, 6)
    closed["close_fees_usdt"] = round(close_fee, 6)
    closed["fees_paid_usdt"] = round(entry_fee + close_fee, 6)
    closed["fees_are_actual"] = entry_fee_is_actual and close_fee_is_actual
    return closed


def _try_mirror_open(
    client: BinanceClient,
    paper_trade: dict,
    *,
    dry_run: bool,
    btc_regime: Optional[dict] = None,
) -> Optional[dict]:
    """实际 mirror paper trade → live. Plan B (client-side SL).

    Returns: 新的 live trade dict (添加到 live_open_trades 用), 失败返回 None.
    异常完全捕获 (不让单笔失败让 loop 崩).

    Args:
        btc_regime: 可选; 由 main_loop 每 tick 调一次 _compute_btc_regime 传入,
                    会被打到 live_trade["btc_regime_at_open"] 等字段供复盘.
                    None 时不打标签 (向后兼容).
    """
    paper_id = paper_trade.get("id", "")
    sym = paper_trade.get("symbol", "")
    direction = paper_trade.get("direction", "")
    side = _paper_to_live_side(direction)
    trade_id = _generate_trade_id(paper_id)

    # 提取必需字段
    try:
        sl_price = float(paper_trade["sl"])
        paper_entry = float(paper_trade.get("entry_price", 0))
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"[mirror-open FAILED] {sym}: paper trade 缺关键字段: {e}")
        return None

    # Phase 5.A: 按 conviction score base notional ($400 / $800 / $200)
    # Phase 5.S: × (direction, regime, sub_regime) multiplier (default 1.0 = 无变化)
    base_notional = _live_notional_for_paper(paper_trade)
    trade_notional = _live_notional_for_mirror(paper_trade, btc_regime)
    score = paper_trade.get("conviction_score")

    # 仅当 multiplier 真改变了 notional 才在 log 里 highlight, 否则简洁
    if abs(trade_notional - base_notional) > 0.01:
        mult = trade_notional / base_notional if base_notional > 0 else 1.0
        log.info(
            f"[mirror-open] {sym} {side} notional=${trade_notional:.0f} "
            f"(base=${base_notional:.0f} × Phase 5.S mult={mult:.2f}) "
            f"score={score} lev={LIVE_LEVERAGE}x sl={sl_price} "
            f"paper_id={paper_id[:40]} → trade_id={trade_id}"
        )
    else:
        log.info(
            f"[mirror-open] {sym} {side} notional=${trade_notional:.0f} (score={score}) "
            f"lev={LIVE_LEVERAGE}x sl={sl_price} "
            f"paper_id={paper_id[:40]} → trade_id={trade_id}"
        )

    # 1. 强制 set_leverage (防 Binance 默认 20x; 已有相同杠杆是 idempotent).
    #    失败 → 放弃本次 mirror, 下 tick 重试 (避免误用错的杠杆开仓).
    try:
        client.set_leverage(sym, LIVE_LEVERAGE)
    except (BinanceError, ValueError) as e:
        if "-1121" in str(e):
            # Phase 4.W: symbol 在本环境不可交易 (testnet 未上线), 加入 session 跳过集合.
            # 不写永久 blacklist — 主网切换后此 symbol 可能恢复.
            _EXCHANGE_UNAVAILABLE_SYMBOLS.add(sym)
            log.warning(
                f"[mirror-open] {sym}: -1121 Invalid symbol "
                f"(本环境不可交易, session 内后续 tick 将自动跳过)"
            )
        else:
            log.error(
                f"[mirror-open FAILED] {sym}: set_leverage({LIVE_LEVERAGE}x) "
                f"失败 → 放弃本次 mirror: {type(e).__name__}: {e}"
            )
        return None
    except Exception as e:
        log.error(
            f"[mirror-open UNEXPECTED] {sym}: set_leverage: "
            f"{type(e).__name__}: {e}", exc_info=True,
        )
        return None

    # Phase 4.V: 取实时盘口价用于 IOC 限价入场 (价格有硬上限, 不追价).
    # LONG: ask (我们要吃的卖一价); SHORT: bid (我们要打的买一价).
    # 失败时 fallback None → open_position 回退到市价单 (向后兼容).
    entry_limit_price: Optional[float] = None
    try:
        bt = client.get_book_ticker(sym)
        side_key = "askPrice" if side == "BUY" else "bidPrice"
        raw_px = float(bt.get(side_key) or 0)
        if raw_px > 0:
            entry_limit_price = raw_px
    except (BinanceError, ValueError, KeyError, TypeError) as e:
        log.warning(f"[mirror-open] {sym}: bookTicker 失败 ({e}), 回退市价单")

    # Phase 4.W (5/26): SL 有效性预检 — 在下单前完成, 不额外消耗 API quota.
    # paper_sl 在 paper 开仓时基于当时价设定. 若 mirror 延迟期间价格逆向移动,
    # SL 可能已被穿越 → SHORT sl < 当前价 (止损在下方, 永不触发); LONG sl > 当前价.
    # 此类信号入场即为错方向充分运动的残局, 直接跳过, 不重试.
    if entry_limit_price is not None and sl_price > 0:
        sl_breached = (
            (side == "SELL" and sl_price < entry_limit_price) or
            (side == "BUY"  and sl_price > entry_limit_price)
        )
        if sl_breached:
            gap_pct = abs(entry_limit_price - sl_price) / entry_limit_price * 100
            log.warning(
                f"[mirror-open SKIP] {sym} {side}: paper SL={sl_price:.6f} 已被当前价 "
                f"{entry_limit_price:.6f} 穿越 ({gap_pct:.2f}%) — 放弃, 不重试"
            )
            return None

    try:
        result = client.open_position(
            symbol=sym,
            side=side,
            notional_usdt=trade_notional,
            sl_price=sl_price,
            trade_id=trade_id,
            limit_price=entry_limit_price,
            use_exchange_sl=False,   # Plan B: client-side SL (Phase 3.2.b 实现 polling)
        )
    except (BinanceError, ValueError) as e:
        log.error(f"[mirror-open FAILED] {sym} {side}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        # 兜底: 任何意外异常都不让 loop 崩
        log.error(f"[mirror-open UNEXPECTED] {sym} {side}: {type(e).__name__}: {e}",
                  exc_info=True)
        return None

    # Phase 5.H (5/30) CRITICAL: result=None 表示 open_position 决定不开仓 (IOC 完全 EXPIRED
    # 或部分成交已应急平). 此时 exchange 上无新仓位, _try_mirror_open 直接返 None 让上层
    # 当 retryable miss 处理. 之前 panic_trade 路径会 result.get() 抛 AttributeError
    # 再上抛, 是 5/28-5/30 14 个孤儿仓 root cause 链的第二层.
    if result is None:
        log.info(f"[mirror-open] {sym} {side}: open_position 返 None (无成交), 跳过")
        return None

    # ━━━ 此处 result 已存在 — 仓位已实际在 Binance 开了 ━━━
    # Phase 4.X (5/26): 包裹所有 post-open 计算到 try/except.
    # 若任何字段构造失败 (paper_trade 异常值 / result 字段缺等), 不能直接 raise —
    # 否则仓位泄漏成孤儿. 构造最小合法 live_trade 兜底, 后续 sync 会补全字段.
    try:
        # Slippage 计算 (paper_entry 是信号时价格, actual_fill 是真实成交)
        actual_fill = float(result.get("avg_fill_price") or 0)
        slippage_bps = 0.0
        if paper_entry > 0 and actual_fill > 0:
            # LONG: 实际成交 > 预期 = 不利 (正 bps)
            # SHORT: 实际成交 < 预期 = 不利 (取负)
            raw_bps = (actual_fill - paper_entry) / paper_entry * 10000.0
            slippage_bps = raw_bps if side == "BUY" else -raw_bps

        # Phase 5.G (5/28): Post-fill 应急平仓兜底.
        # 此时仓位已在 Binance 开成 (open_position 成功), 但需要 2 次后置校验:
        #   1) 入场偏离 > 200 bps → entry_deviation_too_high
        #   2) TP/SL 结构无效 (fill 在错误区间) → post_fill_structure_invalid
        # 任一触发: 立即调 close_position 应急平仓, 返回 None (mirror 视作失败).
        paper_tp1 = 0.0
        paper_tp2 = 0.0
        try:
            paper_tp1 = float(paper_trade.get("tp1") or 0)
            paper_tp2 = float(paper_trade.get("tp2") or 0)
        except (TypeError, ValueError):
            pass
        deviation_bps = (abs(actual_fill - paper_entry) / paper_entry * 10000.0
                          if (paper_entry > 0 and actual_fill > 0) else 0.0)
        structure_ok = _validate_post_fill_structure(
            side, actual_fill, sl_price, paper_tp1, paper_tp2,
        )
        emergency_reason = None
        if deviation_bps > LIVE_POST_FILL_MAX_DEVIATION_BPS:
            emergency_reason = "entry_deviation_too_high"
        elif not structure_ok:
            emergency_reason = "post_fill_structure_invalid"
        if emergency_reason:
            log.warning(
                f"🛑 [post-fill-emergency] {sym} {side}: {emergency_reason} "
                f"(fill={actual_fill} paper_entry={paper_entry} "
                f"deviation={deviation_bps:.1f}bps sl={sl_price} tp1={paper_tp1} tp2={paper_tp2}). "
                f"立即应急平仓."
            )
            emergency_fees = 0.0
            try:
                close_result = client.close_position(
                    symbol=sym, side=side, trade_id=trade_id,
                )
                emergency_fees = float(close_result.get("fees_paid_usdt") or 0)
                log.info(f"[post-fill-emergency] {sym} 应急平仓完成")
            except (BinanceError, ValueError) as e:
                log.error(
                    f"[post-fill-emergency] {sym} 应急平仓失败 ({type(e).__name__}: {e}). "
                    f"position 留在 exchange, 下 tick recon 会识别并提示."
                )
            # Phase 5.G-fix (5/30): 返回 terminal dict 标记 _terminal_no_retry,
            # 让 main_loop 把 paper_id 加入 mirrored_paper_ids 避免每 5s 死循环
            # 重开 + 重应急平 (BIOUSDT 案例: 烧 ~$0.32/次 round-trip fees).
            entry_fees = float(result.get("fees_paid_usdt") or 0)
            total_fees = entry_fees + emergency_fees
            return {
                "_terminal_no_retry": True,
                "_post_fill_rejected": True,
                "paper_id": paper_id,
                "trade_id": trade_id,
                "symbol": sym,
                "side": side,
                "direction": direction,
                "entry_price_paper": paper_entry,
                "avg_fill_price": actual_fill,
                "avg_exit_price": actual_fill,   # 应急平接近 fill 价
                "qty": float(result.get("qty") or 0),
                "notional_usdt": float(result.get("actual_notional") or 0),
                "fees_paid_usdt": round(total_fees, 4),
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "opened_at": result.get("opened_at"),
                "close_reason": emergency_reason,
                "realized_pnl_usdt": round(-total_fees, 4),   # 损失 ≈ 双向手续费
                "is_dry_run": bool(dry_run or result.get("_dryRun")),
            }

        # 风险金额: (entry - SL) / entry × notional = "最多丢多少"
        actual_notional = float(result.get("actual_notional", 0) or 0)
        risk_usdt = 0.0
        risk_pct = 0.0
        if actual_fill > 0 and sl_price > 0 and actual_notional > 0:
            risk_pct = abs(actual_fill - sl_price) / actual_fill * 100.0
            risk_usdt = risk_pct / 100.0 * actual_notional

        # 信号→镜像延迟: paper 开仓时间到 live 开仓时间
        mirror_latency_sec = None
        paper_entered_at = paper_trade.get("entered_at") or paper_trade.get("opened_at")
        opened_at_iso = result.get("opened_at")
        if paper_entered_at and opened_at_iso:
            try:
                paper_dt = datetime.fromisoformat(
                    str(paper_entered_at).replace("Z", "+00:00")
                )
                live_dt = datetime.fromisoformat(
                    str(opened_at_iso).replace("Z", "+00:00")
                )
                mirror_latency_sec = round((live_dt - paper_dt).total_seconds(), 1)
            except (ValueError, TypeError):
                pass

        # Phase 4.D + 4.E + 4.F: A/B/C/D 4-arm 测试
        # 在 live_trade 构造时一次性确定, 整个 trade 生命周期保持同一分组.
        # 注: trade 走到这里说明已通过 is_eligible_for_mirror, 所以 D 组 regime gate
        #     如果该拒已经拒了; 这里只是记录"曾经分到了 D 组"作为统计标签.
        sl_comp_enabled = _ab_use_sl_compensation(paper_id, LIVE_SL_COMPENSATION_MODE)
        wick_filter_enabled = _ab_use_wick_filter(paper_id, LIVE_SL_WICK_FILTER_MODE)
        regime_gate_enabled = _ab_use_regime_gate(paper_id, LIVE_REGIME_GATE_MODE)
        ab_group = _ab_group(paper_id)   # 'A' / 'B' / 'C' / 'D' — 记录用
        # Phase 4.M: funding signal 类型 (友好/不利/中性). 友好时 wick filter 用 +1 breaches.
        funding_signal = _funding_signal(paper_trade)
        wick_min_breaches = (LIVE_FUNDING_FAVORABLE_WICK_BREACHES
                             if funding_signal == "favorable" and wick_filter_enabled
                             else LIVE_WICK_FILTER_MIN_BREACHES)
        sl_comp_offset = 0.0
        final_sl = float(result.get("sl_price", sl_price))   # 默认 = paper_sl
        sl_paper_at_open = float(sl_price)                    # 记录原始 paper_sl
        if sl_comp_enabled and actual_fill > 0 and paper_entry > 0:
            compensated = _compute_compensated_sl(sl_price, actual_fill, paper_entry)
            if compensated is not None and compensated > 0:
                sl_comp_offset = round(actual_fill - paper_entry, 8)
                final_sl = compensated
                log.info(
                    f"[sl-comp] {sym} {side} paper_sl={sl_price:.6f} "
                    f"→ live_sl={final_sl:.6f} (offset {sl_comp_offset:+.6f}, "
                    f"slip {slippage_bps:+.1f}bps)"
                )
        if wick_filter_enabled:
            log.info(
                f"[wick-filter] {sym} {side} 启用 wick 过滤 "
                f"(需 ≥{wick_min_breaches} 次连续 breach 才触发 SL; funding={funding_signal})"
            )
        if regime_gate_enabled:
            log.info(
                f"[regime-gate] {sym} {side} group=D, 启用 regime gate "
                f"(规则: down + LONG 被拒, 但本笔已通过 = 非 down 或非 LONG)"
            )

        live_trade = {
            "paper_id": paper_id,
            "trade_id": trade_id,
            "symbol": sym,
            "side": side,
            "direction": direction,
            "entry_price_paper": paper_entry,
            "avg_fill_price": actual_fill,
            "slippage_bps": round(slippage_bps, 2),
            "qty": result.get("qty", 0),
            "notional_usdt": actual_notional,
            "leverage": LIVE_LEVERAGE,
            "risk_usdt": round(risk_usdt, 4),
            "risk_pct": round(risk_pct, 3),
            "mirror_latency_sec": mirror_latency_sec,
            "sl_price": final_sl,                          # SL polling 实际用此值
            "sl_paper_current": sl_paper_at_open,          # 当前 paper SL (sync 时更新)
            "sl_compensation_enabled": sl_comp_enabled,    # B 组标记 (Phase 4.D)
            "sl_compensation_offset": sl_comp_offset,      # offset (B 组非 0; A/C 组 0)
            "sl_compensation_mode": LIVE_SL_COMPENSATION_MODE,  # 部署时模式 (溯源用)
            # Phase 4.E / 4.L / 4.M: Wick filter 字段
            "wick_filter_enabled": wick_filter_enabled,    # C 组标记 (4.L 起 always)
            "wick_filter_min_breaches": wick_min_breaches if wick_filter_enabled else None,
            "wick_filter_mode": LIVE_SL_WICK_FILTER_MODE,  # 部署时模式 (溯源用)
            "sl_breach_count": 0,                          # 连续 breach 计数器
            # Phase 4.M: Funding-aware filter 字段
            "funding_signal": funding_signal,              # 'favorable' / 'adverse' / 'neutral'
            "funding_rate_pct_at_open": paper_trade.get("funding_rate_pct"),
            # Phase 4.F: Regime gate 字段
            "regime_gate_enabled": regime_gate_enabled,    # D 组标记
            "regime_gate_mode": LIVE_REGIME_GATE_MODE,     # 部署时模式 (溯源用)
            "ab_group": ab_group,                          # 'A' / 'B' / 'C' / 'D' 显式记录
            # Phase 4.H: Conviction filter 溯源 (部署时阈值, None=未启用)
            "min_conviction_threshold": LIVE_MIN_CONVICTION_SCORE,

            "tp1_price": float(paper_trade.get("tp1") or 0),
            "tp2_price": float(paper_trade.get("tp2") or 0),
            "phase": "A",
            "entry_order_id": result.get("entry_order_id"),
            "entry_client_id": result.get("entry_client_id"),
            "sl_order_id": result.get("sl_order_id"),
            "sl_mode": result.get("sl_mode", "client_side"),
            "conviction_score": paper_trade.get("conviction_score"),
            "alert_type": paper_trade.get("alert_type"),
            "atr_pct": paper_trade.get("atr_pct"),
            # Phase 4.Z (5/27): 从 paper 复制大户/散户多空比快照, 让 live_trades_history
            # 直接含这些维度, 复盘时可直接按 live 切片 (无需 JOIN paper).
            "top_trader_position_ratio": paper_trade.get("top_trader_position_ratio"),
            "top_trader_account_ratio":  paper_trade.get("top_trader_account_ratio"),
            "global_account_ratio":      paper_trade.get("global_account_ratio"),
            # MFE 字段初始化为 None — 会在 _sync_live_with_paper 中从 paper 拷过来
            "phase_a_mfe_pct": None,
            "phase_b_mfe_pct": None,
            "phase_c_mfe_pct": None,
            "fees_paid_usdt": result.get("fees_paid_usdt", 0),
            "fees_are_actual": bool(result.get("fees_are_actual", False)),
            "opened_at": opened_at_iso,
            "is_dry_run": bool(dry_run or result.get("_dryRun")),
        }
        # Phase 4.C BTC regime 标签 (开仓时刻 snapshot, 供复盘按 regime 切分).
        # 仅在 main_loop 传入时打, None 不打 (向后兼容旧数据).
        if isinstance(btc_regime, dict) and btc_regime.get("regime"):
            live_trade["btc_regime_at_open"] = btc_regime["regime"]
            live_trade["btc_price_at_open"] = btc_regime.get("btc_price")
            live_trade["btc_change_24h_at_open"] = btc_regime.get("change_24h_pct")
            live_trade["btc_pct_vs_ma25_at_open"] = btc_regime.get("pct_vs_ma25")
            # Phase 4.K Shadow Log: 在 down regime 时附带 sub_regime + 3h 动量,
            # 后续可按 sub_regime 切分 PnL, 验证 down_rebound 是否真的跟其它子状态不同.
            sub_regime_at_open = btc_regime.get("sub_regime")
            live_trade["btc_sub_regime_at_open"] = sub_regime_at_open
            live_trade["btc_change_3h_at_open"] = btc_regime.get("change_3h_pct")
            # Phase 5.R: 实际开仓时 log 一次 (每个 mirror 仅一次, 不会 spam).
            # 仅在 allow-list 命中且属于 down+LONG 时打, 便于事后审计哪些 trade
            # 是因 Phase 5.R 放开而 mirror.
            if (btc_regime.get("regime") == "down"
                    and side == "BUY"
                    and sub_regime_at_open
                    and sub_regime_at_open in LIVE_REGIME_GATE_SUB_REGIME_ALLOW):
                log.info(f"[regime-gate-allow] {sym} {side} mirrored: "
                         f"sub={sub_regime_at_open} (Phase 5.R, allow="
                         f"{sorted(LIVE_REGIME_GATE_SUB_REGIME_ALLOW)})")
        return live_trade
    except Exception as e:
        # Phase 4.X: post-open 构造异常 — 仓位已在 exchange, 必须返回最小 live_trade
        # 让外层正确记录到 state. 缺的字段会在后续 sync tick 由 _sync_live_with_paper 补全.
        log.error(
            f"[mirror-open PARTIAL] {sym} {side}: post-open 构造异常 "
            f"({type(e).__name__}: {e}). 仓位已在 exchange, 用最小字段记录防孤儿.",
            exc_info=True,
        )
        # Phase 5.H: 防御性 — 即使 result 为 None (理论上不应到这里, 因为前面已经
        # 早退 return None) 也不再二次 AttributeError 上抛.
        r = result if isinstance(result, dict) else {}
        panic_trade = {
            "paper_id": paper_id,
            "trade_id": trade_id,
            "symbol": sym,
            "side": side,
            "direction": direction,
            "entry_price_paper": paper_entry,
            "avg_fill_price": float(r.get("avg_fill_price") or 0),
            "qty": r.get("qty", 0),
            "notional_usdt": float(r.get("actual_notional", 0) or 0),
            "leverage": LIVE_LEVERAGE,
            "sl_price": float(r.get("sl_price", sl_price) or sl_price),
            "sl_paper_current": float(sl_price),
            "tp1_price": 0.0,
            "tp2_price": 0.0,
            "phase": "A",
            "entry_order_id": r.get("entry_order_id"),
            "entry_client_id": r.get("entry_client_id"),
            "opened_at": r.get("opened_at"),
            "is_dry_run": bool(dry_run or r.get("_dryRun")),
            # Phase 4.Z: 即使 panic 路径也保留大户/散户多空比 (数据完整性)
            "top_trader_position_ratio": paper_trade.get("top_trader_position_ratio"),
            "top_trader_account_ratio":  paper_trade.get("top_trader_account_ratio"),
            "global_account_ratio":      paper_trade.get("global_account_ratio"),
            "_partial_record": True,  # 标记: 缺 A/B 组 / wick / regime 等字段
        }
        return panic_trade


# ============================================================================
# Main loop
# ============================================================================

def main_loop(client: BinanceClient, *, dry_run: bool = True) -> dict:
    """单次循环 — 读 paper, mirror eligible 的, 持久化 live state.

    Phase 3.2.a: 真实调 open_position (但 dry_run=True 时仍返回 mock).
    Returns: live state dict (for testing/inspection).
    """
    paper = load_paper_state()
    live = load_live_state()
    now = datetime.now(timezone.utc)

    paper_open = paper.get("open_trades", []) or []
    live_open = live.get("live_open_trades", []) or []
    log.info(
        f"[live_trader] paper_open={len(paper_open)} "
        f"live_open={len(live_open)} "
        f"mode={'DRY-RUN' if dry_run else 'LIVE'} "
        f"client_dry_run={client.dry_run}"
    )

    if LIVE_OBSERVATION_MODE:
        log.warning(
            "⚠️ LIVE_OBSERVATION_MODE=True: 跳过 symbol 白名单, "
            "接受 paper 所有 diamond signal. 实盘前必须改回 False!"
        )

    # Phase 3.3.a/b: 风控软+硬门检查
    risk = check_risk_gates(live, now, client=client)
    log.info(
        f"[risk] daily_pnl=${risk['daily_pnl']:+.2f} "
        f"deployed=${risk['deployed_usdt']:.2f}/{LIVE_MAX_DEPLOY_USDT:.0f} "
        f"block_new_opens={risk['block_new_opens']}"
    )
    if risk["block_new_opens"]:
        for r in risk["reasons"]:
            log.warning(f"🛑 [risk-gate] {r}")

    # Phase 3.3.b: 仓位对账 (live state vs exchange).
    # DRY-RUN 模式下不对账 (因为 mock 单不会出现在 exchange, 必然 false mismatch).
    if client.dry_run:
        log.debug("[recon] skipped (DRY-RUN mode, mirror creates mock state)")
        recon = {
            "ok": True, "mismatches": [], "api_failed": False,
            "live_symbols": [], "exchange_symbols": [], "_skipped": "dry_run",
        }
    else:
        recon = check_position_reconciliation(client, live)
        if not recon["ok"]:
            for m in recon["mismatches"]:
                log.warning(f"⚠️ [recon-{m['kind']}] {m['message']}")
            log.warning(
                f"[recon] live_symbols={recon['live_symbols']} "
                f"exchange_symbols={recon['exchange_symbols']}"
            )
        elif recon["api_failed"]:
            log.debug(f"[recon] API failed, skipped reconciliation this tick")
        else:
            log.debug(f"[recon] OK ({len(recon['live_symbols'])} symbols matched)")

    # Phase 4.C BTC regime 取样 — 本 tick 内所有 mirror_open 共享同一 snapshot.
    # 单次 1h kline 调用, 节省 API. 失败返 None → trade 不打 regime 标签 (不阻止 mirror).
    btc_regime_snapshot = _compute_btc_regime(client)
    if btc_regime_snapshot:
        live["_btc_regime_now"] = btc_regime_snapshot   # 供 publish 展示
        log.info(
            f"[btc-regime] {btc_regime_snapshot['regime']:>4s}  "
            f"price=${btc_regime_snapshot['btc_price']:.0f}  "
            f"vs MA25 {btc_regime_snapshot['pct_vs_ma25']:+.2f}%  "
            f"24h {btc_regime_snapshot['change_24h_pct']:+.2f}%"
        )
    # 注: snapshot 为 None 时保留旧值 (上次成功的 regime), 避免 dashboard 闪烁

    # 1. 找 eligible candidates (即使风控触发也走 eligibility 检查, 便于日志一致)
    # Phase 4.F/J: 传 btc_regime 给 is_eligible_for_mirror, 应用 regime gate
    # Phase 4.K (Shadow Log): 同时传 sub_regime + 3h 动量, 让被拒原因附带这些信息
    #   方便事后审计 down_rebound 时段的拒绝是否错杀
    current_regime = btc_regime_snapshot.get("regime") if btc_regime_snapshot else None
    current_sub_regime = btc_regime_snapshot.get("sub_regime") if btc_regime_snapshot else None
    current_change_3h = btc_regime_snapshot.get("change_3h_pct") if btc_regime_snapshot else None
    mirror_candidates = []
    skip_log = []
    for pt in paper_open:
        eligible, reason = is_eligible_for_mirror(
            pt, live, now,
            btc_regime=current_regime,
            btc_sub_regime=current_sub_regime,
            btc_change_3h_pct=current_change_3h,
        )
        if eligible:
            mirror_candidates.append(pt)
        else:
            skip_log.append((pt.get("symbol"), pt.get("id"), reason))
            # 记录 missed signal 供 dashboard 诊断 (排除"已 mirror"噪音)
            _record_missed_signal(live, pt, reason, now)

    # Phase 5.D (5/28): slot 稀缺时按 conviction_score 优先 + 趋势对齐隐式优先.
    # 之前 mirror_candidates 按 paper_open 出现顺序 (FIFO), 当 max_concurrent 满
    # 或 deploy cap 接近时, 后到的高 EV 信号被前面低 EV 信号挤掉.
    # 数据 (1410 笔): score 6-7 avg +$4.50 vs score 5 avg +$0.92 (5×).
    # Phase 5.B 已给 BTC trend-aligned (up+LONG / down+SHORT) +1 score,
    # 故 score 降序排自动让趋势对齐信号优先入场 — 无需额外的 regime alignment 逻辑.
    # 同 score 时按 entered_at FIFO (公平 + 确定性).
    mirror_candidates.sort(
        key=lambda pt: (
            -int(pt.get("conviction_score") or 0),
            pt.get("entered_at", ""),
        )
    )

    if skip_log:
        for sym, pid, reason in skip_log[:5]:
            log.debug(f"[skip-mirror] {sym} ({pid[:30]}...): {reason}")

    # 若风控 block, 把所有 eligible 候选也记为 missed (原因 = 风控原因列表)
    if risk["block_new_opens"] and mirror_candidates:
        block_reason = "risk_gate: " + ", ".join(risk.get("reasons", []) or ["blocked"])
        for pt in mirror_candidates:
            _record_missed_signal(live, pt, block_reason, now)

    # 清理已平仓/已 mirror 的过期 missed 记录
    paper_open_ids = {pt.get("id") for pt in paper_open if pt.get("id")}
    _prune_obsolete_missed(live, paper_open_ids)

    # Phase 5.E: 连损熔断检查 (在 risk gate 之后, 候选迭代之前).
    # 数据驱动 (1410 笔 + 模拟): 30min 内 ≥4 笔 hit_sl 触发, 暂停 30min mirror_open.
    # 净避亏 +$127 over 1410 笔, 不影响 sync/close 现有持仓.
    cb_paused, cb_until = _check_circuit_breaker(live, now)
    if cb_paused and mirror_candidates:
        log.warning(
            f"⏸ [circuit-breaker active] 暂停至 {cb_until}, "
            f"{len(mirror_candidates)} candidate(s) 全部记 missed"
        )
        for pt in mirror_candidates:
            _record_missed_signal(
                live, pt,
                f"circuit_breaker ({LIVE_CB_SL_THRESHOLD}+ SL in {LIVE_CB_WINDOW_MIN}min, "
                f"pause until {cb_until[:19]})",
                now,
            )
        mirror_candidates = []   # 清空, 跳过下面的迭代

    # 2. 对每个 candidate 真实下单 (Plan B: 无 exchange SL)
    #    仅当风控未 block 时执行
    #
    # ⚠️ Bug fix: 每次 mirror 前重新检查 eligibility, 因为 live state 在循环中变化:
    #   - single_symbol cap: 上一笔 mirror 后, 同 symbol 应被阻止
    #   - max_concurrent: 达到 3 笔后, 后续应被阻止
    #   - cash reserve: 部署达 $60 后, 后续应被阻止
    # 不再用一次性预计算的 mirror_candidates 列表盲目迭代.
    mirrored_count = 0
    if risk["block_new_opens"]:
        if mirror_candidates:
            log.warning(
                f"🛑 {len(mirror_candidates)} eligible paper trade(s) "
                f"NOT mirrored due to risk gate."
            )
    else:
        # Orphan protection: 用 recon 数据预防"state 被清后重复 mirror"
        # 场景: 第一次 mirror 已下单到 exchange, 但 state file 被 rm 等清掉,
        #       下次 tick fresh state 看不到 mirrored_paper_ids → 又 mirror 一次,
        #       Binance one-way mode 合并成 2× size 的孤儿持仓.
        # 防护: 若 recon 数据可信 (非 dry_run / api_failed), 且 symbol 已在 exchange,
        #       skip mirror (即使 live_state 不知). 用户需手动 reconcile.
        recon_has_data = (not recon.get("api_failed", False)
                          and not recon.get("_skipped"))
        exchange_symbols_now = set(recon.get("exchange_symbols", []))

        for pt in mirror_candidates:
            sym = pt.get("symbol", "")
            # Orphan check: 第一道防线
            if recon_has_data and sym in exchange_symbols_now:
                log.warning(
                    f"[skip-orphan] {sym}: exchange 已有持仓但 live_state 不知 "
                    f"(可能 state 被清). 跳过 mirror 防 2× size 孤儿. "
                    f"修复: 检查 testnet UI 手动平仓 OR 等 exchange 自然平仓后下次重试"
                )
                # 加入 mirrored_paper_ids 防本 tick 反复触发同 warning
                live.setdefault("mirrored_paper_ids", []).append(pt["id"])
                continue

            # Re-check eligibility 用最新 live state (含本轮已 mirror 的)
            # Phase 4.F/J/K: 传 btc_regime + sub_regime + 3h 动量
            eligible, reason = is_eligible_for_mirror(
                pt, live, now,
                btc_regime=current_regime,
                btc_sub_regime=current_sub_regime,
                btc_change_3h_pct=current_change_3h,
            )
            if not eligible:
                log.debug(f"[skip-during-iter] {pt['symbol']}: {reason}")
                # 不加 mirrored_paper_ids — 下 tick 状态变化后可以重试
                continue

            # Re-check cash reserve (mirror_candidates 计算时未含本轮已部署)
            # Phase 5.A: 用 score-based notional 而非固定 LIVE_NOTIONAL_USDT.
            # Phase 5.S: × regime multiplier — cash check 必须用 final notional, 否则
            #            mult=1.5 时 cap 计算偏小, 会让超 cap 的 trade 漏过去开仓.
            pt_notional = _live_notional_for_mirror(pt, btc_regime_snapshot)
            deployed_now = sum(
                float(lt.get("notional_usdt", 0) or 0)
                for lt in (live.get("live_open_trades") or [])
            )
            if deployed_now + pt_notional > LIVE_MAX_DEPLOY_USDT:
                log.debug(
                    f"[skip-during-iter-cash] {pt['symbol']}: "
                    f"deployed ${deployed_now:.0f} + ${pt_notional:.0f} "
                    f"> cap ${LIVE_MAX_DEPLOY_USDT}"
                )
                continue

            # 滑点护栏 (Phase 4.A/4.V): paper 信号价 → 盘口实时价 预滑点检测.
            # 4.V 动态阈值: intensity=3 → 200 bps / =2 → 150 / =1 → 50 (默认).
            # fail-safe: 取价失败 / 字段缺 → 返 None → 不拒绝, 按原流程开仓.
            pre_slip_bps = _compute_pre_entry_slippage_bps(client, pt)
            slip_threshold = _get_effective_slippage_threshold(pt)
            if pre_slip_bps is not None and pre_slip_bps > slip_threshold:
                reason = (
                    f"pre_slippage_too_high "
                    f"(+{pre_slip_bps:.1f}bps > {slip_threshold:.0f}bps "
                    f"intensity={pt.get('intensity',1)})"
                )
                _record_missed_signal(live, pt, reason, now)
                log.warning(
                    f"[skip-mirror-slip] {pt['symbol']}: 预滑点 "
                    f"+{pre_slip_bps:.1f}bps 超阈值 {slip_threshold:.0f}bps "
                    f"(intensity={pt.get('intensity',1)}), "
                    f"放弃 mirror (paper_id={pt.get('id','')[:40]})"
                )
                continue

            new_trade = _try_mirror_open(client, pt, dry_run=dry_run,
                                          btc_regime=btc_regime_snapshot)
            if new_trade is None:
                # 失败不永久 blacklist — 改为记录 missed signal, 下 tick 重试.
                # 之前直接加 mirrored_paper_ids 导致 set_leverage 临时失败 / API 超时
                # 等可恢复的错误也永不重试. 由 mirror_max_age (10min) 和 paper 自然
                # 平仓提供退出条件, 不会无限循环.
                _record_missed_signal(
                    live, pt, "open_failed (retry next tick)", now,
                )
                log.warning(
                    f"[mirror-open] {pt['symbol']} 失败, 不 blacklist, "
                    f"下 tick 重试 (或等 paper 关闭 / mirror_max_age 过期)"
                )
                continue
            # Phase 5.G-fix (5/30): terminal_no_retry 信号 (post-fill 应急平后).
            # 仓位已开 + 已平, 加入 closed_trades 留 audit, 加入 mirrored_paper_ids
            # 防下一 tick 重复触发同一 paper_id 死循环 (BIOUSDT 案例每 5s 烧 fees).
            if new_trade.get("_terminal_no_retry"):
                live.setdefault("live_closed_trades", []).append(new_trade)
                live.setdefault("mirrored_paper_ids", []).append(pt["id"])
                log.warning(
                    f"[mirror-terminal] {new_trade['symbol']} "
                    f"close_reason={new_trade.get('close_reason')} "
                    f"PnL=${new_trade.get('realized_pnl_usdt', 0):+.2f}. "
                    f"paper_id 加入 mirrored, 不再 retry."
                )
                try:
                    save_live_state(live)
                except Exception as e:
                    log.error(f"[state-save terminal] {new_trade['symbol']}: {e}",
                              exc_info=True)
                continue
            live.setdefault("live_open_trades", []).append(new_trade)
            live.setdefault("mirrored_paper_ids", []).append(pt["id"])
            mirrored_count += 1
            log.info(
                f"[mirrored ✓] {new_trade['symbol']} {new_trade['side']} "
                f"slippage={new_trade['slippage_bps']:+.1f}bps "
                f"({len(live['live_open_trades'])} live open now)"
            )
            # Phase 4.X (5/26): 立即 flush state — 防 tick 后续异常导致刚开的仓
            # 没记录到 state, 形成孤儿. binance 下单 + state 保存必须原子可见.
            # save_live_state 是原子 .tmp→rename, 多次调用安全.
            try:
                save_live_state(live)
            except Exception as e:
                log.error(
                    f"[state-save IMMEDIATE FAILED] {new_trade['symbol']}: {e}. "
                    f"位置已在 exchange, 后续 tick 会被 recon 识别为孤儿!",
                    exc_info=True,
                )

    # 3. Sync + monitor live opens, 触发 close 条件 (Phase 3.2.b)
    # 三个 close 触发器 (按优先级):
    #   A. paper 已关 (timeout / TP / SL 等) → mirror close
    #   B. paper 还开着但 sl 已更新 (BE move / trailing) → sync sl
    #   C. 当前价触 SL (client-side polling) → close
    paper_open_by_id = {pt.get("id"): pt for pt in paper_open if pt.get("id")}
    paper_closed_ids = {
        pt.get("id") for pt in paper.get("recent_closed", []) or []
        if pt.get("id")
    }
    paper_closed_by_id = {
        pt.get("id"): pt for pt in paper.get("recent_closed", []) or []
        if pt.get("id")
    }

    still_open = []
    closed_now = []
    for lt in (live.get("live_open_trades") or []):
        paper_id = lt.get("paper_id", "")

        # === A. Paper 已关 → mirror close ===
        if paper_id in paper_closed_ids:
            paper_closed = paper_closed_by_id.get(paper_id, {})
            paper_reason = paper_closed.get("close_reason", "paper_closed")
            closed_lt = _try_mirror_close(
                client, lt, reason=f"paper:{paper_reason}", dry_run=dry_run,
            )
            if closed_lt is not None:
                closed_now.append(closed_lt)
            else:
                # close 失败 → 保留 open, 下 tick 重试
                still_open.append(lt)
                log.warning(f"[mirror-close retry] {lt.get('symbol')} 留下 tick 重试")
            continue

        # === B. Sync sl/phase from paper ===
        paper_current = paper_open_by_id.get(paper_id)
        if paper_current is not None:
            _sync_live_with_paper(lt, paper_current)
        else:
            # Paper 关了但还没进 recent_closed (罕见 race)
            log.warning(
                f"[mirror-sync] {lt.get('symbol')} paper_id={paper_id[:30]} "
                f"既不在 paper open 也不在 recent_closed (race?)"
            )

        # === C. Client-side SL polling ===
        current_price = _get_current_price(client, lt.get("symbol", ""))
        if current_price is None:
            # 取价失败 → 保留, 下 tick 重试
            still_open.append(lt)
            continue

        # 记录当前价 + 浮动盈亏 (供 dashboard 显示)
        try:
            entry = float(lt.get("avg_fill_price") or 0)
            qty = float(lt.get("qty") or 0)
            sign = 1 if str(lt.get("side", "")).upper() == "BUY" else -1
            lt["current_price"] = current_price
            if entry > 0 and qty > 0:
                lt["unrealized_pnl_usdt"] = round((current_price - entry) * qty * sign, 4)
                lt["unrealized_pnl_pct"] = round((current_price - entry) / entry * 100 * sign, 3)
            else:
                lt["unrealized_pnl_usdt"] = 0.0
                lt["unrealized_pnl_pct"] = 0.0
            lt["last_price_check_at"] = now.isoformat()
        except Exception as e:
            log.debug(f"[live-monitor] failed to record unrealized for {lt.get('symbol')}: {e}")

        if _check_sl_breach(lt, current_price):
            # Phase 4.Y (5/27): 加强 SL-BREACH 日志, 记录完整上下文供后续审计.
            # 5/26 复盘发现 5 笔 paper hit_b_trail → live sl_breach (盈利变亏损),
            # 但没有足够日志判断是 wick 误触发还是真实 SL. 此日志填补诊断缺口.
            try:
                entry = float(lt.get("avg_fill_price") or 0)
                sl = float(lt.get("sl_price") or 0)
                paper_sl = float(lt.get("sl_paper_current") or 0)
                pct_below_entry = ((current_price - entry) / entry * 100
                                   if entry > 0 else 0)
                slip = lt.get("slippage_bps") or 0
                breach_cnt = lt.get("sl_breach_count") or "?"
                comp = "comp" if lt.get("sl_compensation_enabled") else "no-comp"
                wick = "wick" if lt.get("wick_filter_enabled") else "no-wick"
                log.warning(
                    f"[SL-BREACH] {lt.get('symbol')} {lt.get('side')} "
                    f"phase={lt.get('phase')} "
                    f"current={current_price} live_sl={sl} paper_sl={paper_sl} "
                    f"pct_from_entry={pct_below_entry:+.2f}% slip_at_open={slip:+.1f}bps "
                    f"breach_cnt={breach_cnt} mode={comp}/{wick} "
                    f"ab_group={lt.get('ab_group')}"
                )
            except (ValueError, TypeError) as e:
                log.warning(
                    f"[SL-BREACH] {lt.get('symbol')} {lt.get('side')}: "
                    f"current={current_price} crossed sl={lt.get('sl_price')} "
                    f"(log enhance failed: {e})"
                )
            closed_lt = _try_mirror_close(
                client, lt, reason="sl_breach_client", dry_run=dry_run,
            )
            if closed_lt is not None:
                closed_now.append(closed_lt)
            else:
                still_open.append(lt)
                log.error(f"[SL-BREACH retry] {lt.get('symbol')} close 失败, 下 tick 重试")
            continue

        # 没触发任何 close → 仍 open
        still_open.append(lt)
        log.debug(
            f"[live-monitor] {lt.get('symbol')} {lt.get('side')} "
            f"phase={lt.get('phase')} entry={lt.get('avg_fill_price')} "
            f"sl={lt.get('sl_price')} current={current_price}"
        )

    # 更新 state
    live["live_open_trades"] = still_open
    live.setdefault("live_closed_trades", []).extend(closed_now)

    # 4. 持久化私有 state
    save_live_state(live)

    # 5. Phase 3.2.c: 发布对外 history (供 dashboard 读)
    published = publish_live_history(live, risk=risk, recon=recon)
    if not published:
        log.warning("[live_trader] failed to publish live_trades_history.json")

    if mirrored_count > 0 or closed_now:
        log.info(
            f"[live_trader tick] +{mirrored_count} opened, "
            f"+{len(closed_now)} closed, "
            f"{len(still_open)} still open"
        )
    return live


# ============================================================================
# CLI
# ============================================================================

def _cli_main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Live Trader — mirror paper decisions on real Binance Futures",
    )
    p.add_argument("--once", action="store_true",
                   help="跑一次循环 (默认行为)")
    p.add_argument("--loop", action="store_true",
                   help=f"持续循环 (每 {POLL_INTERVAL_SEC}s 一次, Ctrl+C 退出)")
    p.add_argument("--live", action="store_true",
                   help="🛑 关闭 dry-run, Phase 3.2+ 才有实际效果. "
                        "Phase 3.1 仍仅输出 would-mirror 日志.")
    p.add_argument("--mainnet", action="store_true",
                   help="使用主网 (默认 testnet, 慎用!)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="DEBUG 级日志 (含 skip-mirror 细节)")
    args = p.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 加载凭证
    key, secret, env_testnet = load_credentials()
    use_testnet = not args.mainnet
    dry_run = not args.live

    # 🛑 安全锁: LIVE_OBSERVATION_MODE + --live 互斥 (不允许观察模式 + 真钱)
    # 🛑 安全锁: OBS mode + 主网 + 真单 = 真钱风险, reject.
    # (testnet + OBS + --live 是 OK 的: testnet 钱用于观察真订单流程)
    if LIVE_OBSERVATION_MODE and not dry_run and not use_testnet:
        raise SystemExit(
            "🛑 LIVE_OBSERVATION_MODE=True + 主网 + --live = 真钱风险.\n"
            "观察模式跳过 symbol 白名单, 跟主网严格白名单不兼容.\n"
            "修复方案 1: 改 LIVE_OBSERVATION_MODE=False (恢复严格白名单)\n"
            "修复方案 2: 不带 --mainnet (用 testnet 真单观察)"
        )

    client = BinanceClient(key, secret, testnet=use_testnet, dry_run=dry_run)
    log.info(
        f"live_trader started: {client} dry_run={dry_run} "
        f"testnet={use_testnet} once={not args.loop}"
    )
    log.info(
        f"config: notional=${LIVE_NOTIONAL_USDT} max_concurrent={LIVE_MAX_CONCURRENT} "
        f"whitelist={LIVE_SYMBOL_WHITELIST} max_age={LIVE_MIRROR_MAX_AGE_SEC}s"
    )

    if not args.loop:
        # Phase 4.X (5/26): --once 模式加 try/except, 跟 --loop 一致.
        # 之前裸调用导致 main_loop 中任何异常都让进程崩溃退出, 而 plist
        # 5s 后又重启, 中间的 state 写入完全丢失 — 是孤儿仓的核心成因.
        try:
            main_loop(client, dry_run=dry_run)
        except Exception as e:
            log.error(f"main_loop error in --once mode: {e}", exc_info=True)
            return 1
        return 0

    while True:
        try:
            main_loop(client, dry_run=dry_run)
        except KeyboardInterrupt:
            log.info("Interrupted, exiting cleanly")
            return 0
        except Exception as e:
            log.error(f"main_loop error (will retry in {POLL_INTERVAL_SEC}s): {e}",
                      exc_info=True)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(_cli_main())
