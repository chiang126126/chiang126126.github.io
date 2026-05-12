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

import json
import os
import sys
import time
import urllib.request
import urllib.error
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

# ---- Phase 4: 自动模拟仓 (仅钻石信号开仓, 跟踪真实收益曲线) ----
PAPER_STATE       = Path.home() / "cresus-bot" / ".paper_trades.json"       # 本地全量 state
PAPER_HISTORY     = Path.home() / "cresus-bot" / "paper_trades_history.json" # 推给看板
PAPER_AUTO_CLOSE_HOURS = 4            # 4 小时未触发 SL/TP 自动平仓 (跟 OUTCOME_STAGES 4h 对齐)
PAPER_RECENT_LIMIT = 50               # 看板只显示最近 50 个 closed
PAPER_MIN_TIER     = "diamond"        # 只对钻石信号自动开仓 (高质量 only)

BINANCE_FAPI = "https://fapi.binance.com"
UA = "Mozilla/5.0 (Macintosh) cresus-velocity-scanner"
HTTP_TIMEOUT = 12

SCAN_TOP_N             = 200      # 24h 成交额 Top N 标的
KLINE_LIMIT            = 240      # 1m 数据条数 (4h 窗口, 支持 1h/4h 多窗口涨幅 + ATR)

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

    # Funding rate (情绪拥挤指标): >0.05% = 多拥挤; <-0.05% = 空拥挤
    funding_rate_pct: Optional[float] = None

    # ATR-based 入场建议
    atr_pct: Optional[float] = None              # ATR / current price * 100
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
        change_4h  = _safe_pct(klines, 240) if len(klines) > 240 else None

        # 主动买盘占比 (kline 自带, 0 额外 API)
        taker_1m = _taker_buy_ratio(last_completed)
        taker_5m = _taker_buy_ratio_n(klines, 5)

        # ATR(14) 1m
        atr_abs = _compute_atr(klines, ATR_PERIOD)
        atr_pct = round(atr_abs / last_close * 100, 3) if (atr_abs and last_close > 0) else None

        # 入场建议: 以 last_close 为参考入场点
        sl = tp1 = tp2 = None
        if atr_abs and atr_abs > 0:
            if direction == "LONG":
                sl  = round(last_close - ATR_SL_MULT  * atr_abs, 8)
                tp1 = round(last_close + ATR_TP1_MULT * atr_abs, 8)
                tp2 = round(last_close + ATR_TP2_MULT * atr_abs, 8)
            else:
                sl  = round(last_close + ATR_SL_MULT  * atr_abs, 8)
                tp1 = round(last_close - ATR_TP1_MULT * atr_abs, 8)
                tp2 = round(last_close - ATR_TP2_MULT * atr_abs, 8)

        # OI 5m Δ (额外 1 次 API,可禁用)
        oi_delta = None
        if not skip_oi:
            oi_delta = fetch_oi_delta_5m(symbol)

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
            funding_rate_pct=round(funding_rate_pct, 4) if funding_rate_pct is not None else None,
            atr_pct=atr_pct,
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

def _compute_conviction(a: VelocityAlert, winrate_summary: Optional[dict]) -> tuple:
    """计算 (score 0-10, tier str). 失败保护: 任何 None 跳过该项不加分."""
    score = 0

    # 1. 极端 funding (+3): 单边过度拥挤,反转动能强
    if a.funding_rate_pct is not None and abs(a.funding_rate_pct) >= 0.3:
        score += 3

    # 2. 历史 30m 胜率 ≥ 60% AND N ≥ 5 (+3): 经验主义验证
    if winrate_summary and winrate_summary.get("by_key"):
        wkey = f"{a.symbol}|{a.alert_type}|{a.direction}"
        w = winrate_summary["by_key"].get(wkey)
        if w and w.get("stages"):
            s30 = w["stages"].get("30m")
            if s30 and s30.get("n", 0) >= 5 and s30.get("win_rate", 0) >= 0.60:
                score += 3

    # 3. OI 方向匹配 (+2): LONG+OI涨 = 真新多, SHORT+OI跌 = 真新空
    if a.oi_delta_5m_pct is not None:
        if a.direction == "LONG" and a.oi_delta_5m_pct > 0:
            score += 2
        elif a.direction == "SHORT" and a.oi_delta_5m_pct < 0:
            score += 2

    # 4. sustained 类型 (+1): 10m 累计比 1m burst 多一层时间验证
    if a.alert_type == "sustained":
        score += 1

    # 5. 多窗口同向 (+1): 5m / 1h / 4h 跟主方向一致 (允许 1 个不一致)
    if a.change_5m_pct is not None and a.change_1h_pct is not None:
        dir_sign = 1 if a.direction == "LONG" else -1
        matches = 0
        total = 0
        for v in (a.change_5m_pct, a.change_1h_pct, a.change_4h_pct):
            if v is None:
                continue
            total += 1
            if (v > 0) == (dir_sign > 0):
                matches += 1
        # 至少 2 个窗口, 多数同向
        if total >= 2 and matches >= total - 1:
            score += 1

    # tier 分级
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

    pushed = 0
    diamond_n = 0
    for a in new_alerts:
        is_diamond = (a.conviction_tier == "diamond")
        # 钻石信号: 跳过 intensity 门槛 + 跳过 cooldown (绝不漏)
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

    if pushed > 0:
        _save_tg_cooldown(cooldown)
        if diamond_n > 0:
            _log(f"📲 Telegram 推送 {pushed} 条 (其中 💎 钻石 {diamond_n} 条)")
        else:
            _log(f"📲 Telegram 推送 {pushed} 条 (intensity ≥ {TG_MIN_INTENSITY})")
    return pushed


# ============================================================================
# Phase 4: 自动模拟仓 (仅钻石信号开仓, 跟踪真实收益曲线)
# 只读 alerts + 公共 ticker 价, 不真实下单. 每 60s 检查 SL/TP 命中.
# ============================================================================

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


def _open_paper_trade(a: VelocityAlert, state: dict, now: datetime) -> Optional[dict]:
    """钻石信号 → 开模拟仓. 返回 trade dict (None 表示跳过).
    初始 Phase A: SL = entry ± 1×ATR, TP1 = entry ± 1.5×ATR, TP2 = entry ± 3×ATR
    """
    if a.conviction_tier != PAPER_MIN_TIER:
        return None
    if a.suggested_sl is None or a.suggested_tp1 is None or a.suggested_tp2 is None:
        return None
    for t in state["open_trades"]:
        if t.get("symbol") == a.symbol and t.get("direction") == a.direction:
            return None
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
        "entered_at": now.isoformat(),
        "current_price": a.price,
        "unrealized_pnl_pct": 0.0,
        # 上下文 (复盘用)
        "funding_rate_pct": a.funding_rate_pct,
        "oi_delta_5m_pct": a.oi_delta_5m_pct,
        "taker_buy_ratio_1m": a.taker_buy_ratio_1m,
        "change_1h_pct": a.change_1h_pct,
        # ===== Phase 4 动态 SL/TP 状态机 =====
        "phase": "A",                            # A=初始 / B=TP1后BE / C=TP2后trailing
        "tp1_hit_at": None,                      # ISO ts when TP1 触发
        "tp2_hit_at": None,                      # ISO ts when TP2 触发
        "high_water_mark": a.price,              # 持仓期间最高价(LONG)/最低价(SHORT)
        "trailing_sl": None,                     # Phase C 跟踪止损 (只升不降棘轮)
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

        # 方向化 unrealized PnL
        raw_pct = (cur - entry) / entry * 100
        t["unrealized_pnl_pct"] = round(raw_pct if is_long else -raw_pct, 3)

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
            # 初始阶段: 检查 SL → TP1 (TP2 还远, 不直接跳到 C)
            if is_long:
                if cur <= t["sl"]:
                    close_reason = "hit_sl"; close_price = t["sl"]
                elif cur >= t["tp1"]:
                    # TP1 触发 → 进 Phase B, 不关仓!! SL 移到 entry (BE)
                    t["phase"] = "B"
                    t["tp1_hit_at"] = now.isoformat()
                    t["sl"] = entry  # BE
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
                    phase_transitions.append({
                        "type": "tp1", "trade": t.copy(), "old_sl_pct": -atr_pct,
                    })

        elif phase == "B":
            # TP1 后免费仓位: SL=BE, 等 TP2 或回吐
            if is_long:
                if cur <= t["sl"]:  # = entry
                    close_reason = "hit_be_sl"; close_price = entry
                elif cur >= t["tp2"]:
                    # TP2 触发 → 进 Phase C, 不关仓!! 启动 trailing
                    t["phase"] = "C"
                    t["tp2_hit_at"] = now.isoformat()
                    # trailing 起点: HWM - 2×ATR (用相对 % 算)
                    t["trailing_sl"] = hwm * (1 - 2 * atr_pct / 100.0)
                    phase_transitions.append({"type": "tp2", "trade": t.copy()})
            else:  # SHORT
                if cur >= t["sl"]:
                    close_reason = "hit_be_sl"; close_price = entry
                elif cur <= t["tp2"]:
                    t["phase"] = "C"
                    t["tp2_hit_at"] = now.isoformat()
                    t["trailing_sl"] = hwm * (1 + 2 * atr_pct / 100.0)
                    phase_transitions.append({"type": "tp2", "trade": t.copy()})

        elif phase == "C":
            # TP2 后跟踪止盈: trailing SL 棘轮只升 (LONG 时升 = 数字增大)
            if is_long:
                new_trail = hwm * (1 - 2 * atr_pct / 100.0)
                # 棘轮: 只朝有利方向更新
                if t.get("trailing_sl") is None or new_trail > t["trailing_sl"]:
                    t["trailing_sl"] = new_trail
                if cur <= t["trailing_sl"]:
                    close_reason = "hit_trail"; close_price = t["trailing_sl"]
            else:  # SHORT
                new_trail = hwm * (1 + 2 * atr_pct / 100.0)
                if t.get("trailing_sl") is None or new_trail < t["trailing_sl"]:
                    t["trailing_sl"] = new_trail
                if cur >= t["trailing_sl"]:
                    close_reason = "hit_trail"; close_price = t["trailing_sl"]

        # 任何 phase: 4h timeout 兜底
        if not close_reason and hold_min >= PAPER_AUTO_CLOSE_HOURS * 60:
            close_reason = "timeout"; close_price = cur

        if close_reason:
            realized_raw = (close_price - entry) / entry * 100
            realized = realized_raw if is_long else -realized_raw
            t["closed_at"] = now.isoformat()
            t["close_price"] = close_price
            t["close_reason"] = close_reason
            t["realized_pnl_pct"] = round(realized, 3)
            t["hold_time_min"] = round(hold_min, 1)
            state["closed_trades"].append(t)
            closed_now.append(t)
        else:
            still_open.append(t)

    state["open_trades"] = still_open
    return len(closed_now), closed_now, phase_transitions


def _compute_paper_stats(state: dict) -> dict:
    open_n = len(state.get("open_trades", []))
    closed = state.get("closed_trades", [])
    closed_n = len(closed)
    if closed_n == 0:
        return {
            "total_trades": open_n,
            "open": open_n,
            "closed": 0,
            "wins": 0, "losses": 0,
            "win_rate": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "best_trade": None,
            "worst_trade": None,
            "by_outcome": {},
        }
    wins = sum(1 for t in closed if t["realized_pnl_pct"] > 0)
    losses = closed_n - wins
    pnls = [t["realized_pnl_pct"] for t in closed]
    by_outcome: dict = {}
    for t in closed:
        reason = t.get("close_reason", "?")
        by_outcome.setdefault(reason, 0)
        by_outcome[reason] += 1
    return {
        "total_trades": open_n + closed_n,
        "open": open_n,
        "closed": closed_n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / closed_n, 3),
        "total_pnl_pct": round(sum(pnls), 2),
        "avg_pnl_pct": round(sum(pnls) / closed_n, 3),
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "by_outcome": by_outcome,
    }


def _save_paper_history(state: dict, stats: dict) -> None:
    """对外发布的 view: stats + open + 最近 N 条 closed."""
    closed = state.get("closed_trades", [])
    # 按 closed_at 降序取最近 N
    closed_sorted = sorted(closed, key=lambda t: t.get("closed_at", ""), reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "open_trades": state.get("open_trades", []),
        "recent_closed": closed_sorted[:PAPER_RECENT_LIMIT],
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


def _run_paper_trading(new_alerts: List[VelocityAlert], now: datetime) -> None:
    """钻石信号自动开仓 + 3 阶段动态 SL/TP 状态机 + 关仓 + 发布 history.
    全部失败保护 — 主流程不受影响.
    """
    try:
        state = _load_paper_state()
        # 1. 新钻石开仓
        opened_n = 0
        for a in new_alerts:
            if _open_paper_trade(a, state, now) is not None:
                opened_n += 1
                _log(f"💎 模拟开仓 {a.symbol} {a.direction} @ {a.price} "
                     f"SL={a.suggested_sl} TP1={a.suggested_tp1} TP2={a.suggested_tp2}")
        # 2. 更新 open trades (含 phase 转移)
        closed_n = 0
        closed_list: List[dict] = []
        transitions: List[dict] = []
        if state["open_trades"]:
            prices = _fetch_all_prices_now()
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
                    "hit_trail":  "跟踪止盈 (TP2 后)",
                    "hit_tp1":    "TP1 止盈 (旧逻辑)",
                    "hit_tp2":    "TP2 止盈 (旧逻辑)",
                    "timeout":    "超时 (4h)",
                }.get(t["close_reason"], t["close_reason"])
                msg = (
                    f"{emoji} <b>模拟仓平仓 — {t['symbol']} {t['direction']}</b>\n"
                    f"原因: {reason_label} · <b>{t['realized_pnl_pct']:+.2f}%</b> · 持仓 {t['hold_time_min']:.0f}min\n"
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
    try:
        for a in new_alerts:
            score, tier = _compute_conviction(a, winrate_summary)
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
    try:
        _run_paper_trading(new_alerts, datetime.now(timezone.utc))
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
