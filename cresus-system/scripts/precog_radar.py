"""precog_radar.py — Crésus 预兆雷达

目标:
  在异动发生前夕（蓄势期）或刚启动时,捕捉 Top 5 最可能大涨 (LONG) +
  Top 5 最可能大跌 (SHORT) 的标的。

二阶段流水线:
  阶段 A — 量化前兆扫描 (~150 symbols × 8 indicators)
    输出: 每个标的的 breakout_score (0-1, 方向无关)
    Top 30 进入下阶段

  阶段 B — DeepSeek 深度推理 (per candidate)
    输入: 该标的多周期K线 + 8 指标值 + 价格结构
    输出: BREAKOUT_LONG / BREAKOUT_SHORT / NEUTRAL + 置信度 + 证据 + 风险

  阶段 C — 聚合排名
    Top 5 LONG (confidence >= 65 的 BREAKOUT_LONG, 按 confidence × breakout_score 排序)
    Top 5 SHORT (同上)

输出: ~/cresus-bot/precog_radar.json

8 个前兆指标 (基于 1h K 线):
  1. bb_squeeze         — Bollinger 带宽 / 30日均带宽 (蓄势)
  2. oi_velocity        — OI 1h 增速 / 24h 均值 (资金涌入)
  3. volume_spike       — 1h 成交 / 24h 均时量 (异常放量)
  4. price_coiling      — ATR(14) 24h / 7d 比值 (波动收敛)
  5. ma_convergence     — MA20/50 间距标准差 (均线缠绕)
  6. funding_extremity  — 资金费率偏离 30d 中位数倍数
  7. range_compression  — 4h 价格区间 / 24h 区间 (横盘压缩)
  8. time_in_squeeze    — BB 处于窄区的连续小时数

依赖: 纯 stdlib (urllib + json + math)
DeepSeek API: 通过环境变量 DEEPSEEK_API_KEY (无 key 时降级为仅量化版)
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median, stdev
from typing import List, Optional, Dict, Tuple

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_OUTPUT = Path.home() / "cresus-bot" / "precog_radar.json"
CACHE_DIR      = Path.home() / "cresus-bot" / ".precog_cache"

# ---- Phase 2: A/B 评估用胜率追踪 (跟 velocity 同 schema) ----
OUTCOMES_STATE   = Path.home() / "cresus-bot" / ".precog_outcomes.json"
WINRATE_OUTPUT   = Path.home() / "cresus-bot" / "precog_winrate.json"
OUTCOME_STAGES_MIN = [30, 60, 240]   # 30m / 1h / 4h
OUTCOMES_RETENTION_DAYS = 60
OUTCOME_WIN_THRESHOLD_PCT = 0.5
WINRATE_MIN_SAMPLES = 5
# precog 每 10min 跑一次, 同一 (symbol, direction, pred_type) 在窗口内只算一次 prediction
# 否则同一行情被算 6 次, 胜率失真
PRED_DEDUP_WINDOW_MIN = 60

BINANCE_FAPI = "https://fapi.binance.com"
OKX_API      = "https://www.okx.com"

UA = "Mozilla/5.0 (Macintosh) cresus-precog-radar"
HTTP_TIMEOUT = 15

# 扫描配置
SCAN_TOP_N_SYMBOLS = 200              # 取 24h 成交额 Top N 进入扫描 (扩大可发现"妖币")
KLINE_INTERVAL_MAIN = "1h"            # 主分析周期 (蓄势 setup detection)
KLINE_INTERVAL_FAST = "5m"            # 快速周期 (ignition detection)
KLINE_INTERVAL_MID  = "15m"           # 中速周期 (transition confirmation)
KLINE_LIMIT_MAIN = 200                # 1h × 200 ≈ 8 天
KLINE_LIMIT_FAST = 60                 # 5m × 60 = 5 小时
KLINE_LIMIT_MID  = 60                 # 15m × 60 = 15 小时
CANDIDATE_TOP_N = 30                  # 量化前兆 Top N 进入 AI 分析
FINAL_TOP_N = 5                       # 最终 LONG/SHORT 各取 Top N

# 缓存 TTL (秒)
CACHE_TTL = {
    "tickers":  60,
    "kline_1h": 60 * 5,
    "kline_15m": 60 * 3,
    "kline_5m":  60,                  # 1m 刷新 — 启动检测要快
    "oi_hist":  60 * 5,
    "funding":  60 * 30,
}

# 蓄势指标权重 (sum=1.0)
SETUP_WEIGHTS = {
    "bb_squeeze":         0.20,
    "oi_velocity":        0.15,
    "volume_spike":       0.15,
    "price_coiling":      0.10,
    "ma_convergence":     0.05,
    "funding_extremity":  0.10,
    "range_compression":  0.15,
    "time_in_squeeze":    0.10,
}

# 启动指标权重 (sum=1.0)
IGNITION_WEIGHTS = {
    "candle_burst_5m":      0.30,    # 5m 大阳/大阴线 (body > 2x ATR)
    "volume_surge_5m":      0.25,    # 5m 成交量爆发 (>5x avg)
    "range_breakout":       0.25,    # 突破 1h 区间
    "trend_acceleration":   0.20,    # 短期均线发散 + 同向加速
}

# 排除杠杆代币 / 稳定币
EXCLUDE_PATTERNS = ("DOWN", "UP", "BEAR", "BULL", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "_PERP", "_PRE")


# ============================================================================
# Data classes
# ============================================================================

@dataclass
class Indicators:
    # 蓄势指标 (1h K 线)
    bb_squeeze:        float = 0.0
    oi_velocity:       float = 0.0
    volume_spike:      float = 0.0
    price_coiling:     float = 0.0
    ma_convergence:    float = 0.0
    funding_extremity: float = 0.0
    range_compression: float = 0.0
    time_in_squeeze:   float = 0.0
    # 启动指标 (5m + 15m K 线)
    candle_burst_5m:    float = 0.0
    volume_surge_5m:    float = 0.0
    range_breakout:     float = 0.0
    trend_acceleration: float = 0.0
    # 原始值 (诊断用)
    raw: dict = field(default_factory=dict)


@dataclass
class Candidate:
    symbol: str
    base: str
    price: float
    setup_score: float           # 蓄势分 0-1
    ignition_score: float        # 启动分 0-1
    breakout_score: float        # max(setup, ignition) 用于排序
    direction_hint: str          # 量化推断方向: LONG / SHORT / NEUTRAL
    direction_reasons: List[str] = field(default_factory=list)
    indicators: Indicators = field(default_factory=Indicators)
    tradable_okx: bool = False
    okx_inst_id: Optional[str] = None
    # 价格结构 (供 AI + dashboard 复用)
    price_change_24h_pct: float = 0.0
    dist_24h_high_pct: float = 0.0
    dist_24h_low_pct: float = 0.0
    # AI 分析结果 (Phase B 填充)
    ai_prediction: Optional[str] = None     # BREAKOUT_LONG / SHORT / NEUTRAL
    ai_confidence: Optional[int] = None     # 0-100
    expected_move: Optional[str] = None     # "+5% ~ +10%"
    expected_window: Optional[str] = None
    key_evidence: Optional[List[str]] = None
    key_risks: Optional[List[str]] = None


# ============================================================================
# HTTP helpers + cache
# ============================================================================

def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT) -> Optional[object]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[precog] {url[:80]}... FAILED: {e}", file=sys.stderr)
        return None


def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 60) -> Optional[object]:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[precog] POST {url[:60]}... FAILED: {e}", file=sys.stderr)
        return None


class JsonCache:
    """Simple file-based JSON cache with TTL per category."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, category: str, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_")
        return self.root / f"{category}_{safe}.json"

    def get(self, category: str, key: str, ttl: int) -> Optional[object]:
        p = self._path(category, key)
        if not p.exists():
            return None
        try:
            age = time.time() - p.stat().st_mtime
            if age > ttl:
                return None
            return json.loads(p.read_text())
        except Exception:
            return None

    def set(self, category: str, key: str, value: object) -> None:
        p = self._path(category, key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(value))
            tmp.replace(p)
        except Exception as e:
            print(f"[precog] cache write FAILED: {e}", file=sys.stderr)


# ============================================================================
# Data fetchers
# ============================================================================

def fetch_top_symbols(cache: JsonCache, top_n: int = SCAN_TOP_N_SYMBOLS) -> List[str]:
    """Pull Binance perp 24h ticker, return top-N USDT symbols by quote volume."""
    cached = cache.get("tickers", "all", CACHE_TTL["tickers"])
    if cached:
        data = cached
    else:
        data = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr")
        if not isinstance(data, list):
            return []
        cache.set("tickers", "all", data)

    pairs = []
    for t in data:
        sym = t.get("symbol", "")
        if not sym or not sym.isascii():    # 过滤非 ASCII (如 '币安人生USDT' 这类奇葩)
            continue
        if not sym.endswith("USDT"):
            continue
        if any(p in sym for p in EXCLUDE_PATTERNS):
            continue
        try:
            vol = float(t.get("quoteVolume", 0))
        except ValueError:
            continue
        if vol < 1_000_000:
            continue
        pairs.append((sym, vol))
    pairs.sort(key=lambda x: -x[1])
    return [s for s, _ in pairs[:top_n]]


def fetch_klines(symbol: str, cache: JsonCache, interval: str = KLINE_INTERVAL_MAIN, limit: int = KLINE_LIMIT_MAIN) -> List[list]:
    """Return list of klines: [openTime, open, high, low, close, volume, closeTime, quoteVol, trades, ...]."""
    cache_cat = f"kline_{interval}"
    ttl = CACHE_TTL.get(cache_cat, 60 * 5)
    cache_key = f"{symbol}_{limit}"
    cached = cache.get(cache_cat, cache_key, ttl)
    if cached:
        return cached
    data = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if not isinstance(data, list) or not data:
        return []
    cache.set(cache_cat, cache_key, data)
    return data


def fetch_oi_hist(symbol: str, cache: JsonCache, period: str = "1h", limit: int = 48) -> List[dict]:
    """Open interest history."""
    cache_key = f"{symbol}_{period}_{limit}"
    cached = cache.get("oi_hist", cache_key, CACHE_TTL["oi_hist"])
    if cached:
        return cached
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist?symbol={symbol}&period={period}&limit={limit}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    cache.set("oi_hist", cache_key, data)
    return data


def fetch_funding_rate(symbol: str, cache: JsonCache, limit: int = 30) -> List[dict]:
    cache_key = f"{symbol}_{limit}"
    cached = cache.get("funding", cache_key, CACHE_TTL["funding"])
    if cached:
        return cached
    url = f"{BINANCE_FAPI}/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    cache.set("funding", cache_key, data)
    return data


def fetch_okx_swap_set() -> set:
    data = _http_get_json(f"{OKX_API}/api/v5/market/tickers?instType=SWAP")
    if not data or not isinstance(data, dict):
        return set()
    out = set()
    for t in data.get("data", []):
        inst = t.get("instId", "")
        if not inst or not inst.isascii():
            continue
        if inst.endswith("-USDT-SWAP"):
            out.add(inst.replace("-USDT-SWAP", "").upper())
    return out


# ============================================================================
# Indicators (pure stdlib)
# ============================================================================

def _close_prices(klines: list) -> List[float]:
    return [float(k[4]) for k in klines]

def _high_prices(klines: list) -> List[float]:
    return [float(k[2]) for k in klines]

def _low_prices(klines: list) -> List[float]:
    return [float(k[3]) for k in klines]

def _volumes_quote(klines: list) -> List[float]:
    return [float(k[7]) for k in klines]

def _rolling_mean(arr: List[float], window: int) -> List[Optional[float]]:
    out = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(mean(arr[i - window + 1 : i + 1]))
    return out

def _rolling_std(arr: List[float], window: int) -> List[Optional[float]]:
    out = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
        else:
            try:
                out.append(stdev(arr[i - window + 1 : i + 1]))
            except Exception:
                out.append(None)
    return out

def _bb_width(closes: List[float], window: int = 20, mult: float = 2.0) -> List[Optional[float]]:
    """Returns BB width as percentage of MA: (upper - lower) / MA = 2 * mult * std / MA."""
    ma = _rolling_mean(closes, window)
    sd = _rolling_std(closes, window)
    out = []
    for i in range(len(closes)):
        if ma[i] is None or sd[i] is None or ma[i] == 0:
            out.append(None)
        else:
            out.append(2 * mult * sd[i] / ma[i])
    return out

def _atr(klines: list, window: int = 14) -> List[Optional[float]]:
    """ATR as percentage of close."""
    out = [None]
    for i in range(1, len(klines)):
        h = float(klines[i][2])
        l = float(klines[i][3])
        prev_c = float(klines[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        c = float(klines[i][4])
        out.append(tr / c if c > 0 else 0)
    # Smooth with rolling mean
    return _rolling_mean(out[1:], window)


def compute_indicators(symbol: str, klines: list, oi_hist: list, funding_rates: list) -> Optional[Indicators]:
    """Compute all 8 pre-breakout indicators. Return None if data insufficient."""
    if len(klines) < 50:
        return None

    closes = _close_prices(klines)
    highs  = _high_prices(klines)
    lows   = _low_prices(klines)
    vols   = _volumes_quote(klines)

    raw = {}
    ind = Indicators()

    # 1. BB Squeeze: current bb_width / 30d avg bb_width
    bb_widths = _bb_width(closes, window=20)
    valid_bb = [b for b in bb_widths if b is not None]
    if len(valid_bb) >= 30:
        cur_bb = valid_bb[-1]
        avg_bb_30d = mean(valid_bb[-min(168, len(valid_bb)):])  # 7 days ≈ 168 1h candles
        ratio = cur_bb / avg_bb_30d if avg_bb_30d > 0 else 1.0
        # 越小越收口 → 反向 (1 - ratio), 限制 0~1
        ind.bb_squeeze = max(0.0, min(1.0, 1.0 - ratio))
        raw["bb_width_now"] = cur_bb
        raw["bb_width_7d_avg"] = avg_bb_30d

    # 2. OI Velocity
    if len(oi_hist) >= 24:
        try:
            oi_vals = [float(o["sumOpenInterest"]) for o in oi_hist]
            recent_changes = []
            for i in range(1, len(oi_vals)):
                if oi_vals[i-1] > 0:
                    recent_changes.append(abs(oi_vals[i] - oi_vals[i-1]) / oi_vals[i-1])
            if len(recent_changes) >= 12:
                last_1h_chg = recent_changes[-1]
                avg_chg = mean(recent_changes[:-1])
                ratio = last_1h_chg / avg_chg if avg_chg > 0 else 0
                # 3x 均值 = 满分
                ind.oi_velocity = min(1.0, ratio / 3.0)
                raw["oi_1h_change"] = last_1h_chg
                raw["oi_24h_avg_change"] = avg_chg
        except Exception:
            pass

    # 3. Volume Spike
    if len(vols) >= 25:
        last_1h_vol = vols[-1]
        avg_24h_vol = mean(vols[-25:-1])  # exclude current
        ratio = last_1h_vol / avg_24h_vol if avg_24h_vol > 0 else 0
        # 2x 均值 = 满分
        ind.volume_spike = min(1.0, ratio / 2.5)
        raw["vol_1h"] = last_1h_vol
        raw["vol_24h_avg"] = avg_24h_vol

    # 4. Price Coiling: ATR 24h / ATR 7d
    atrs = _atr(klines, window=14)
    valid_atrs = [a for a in atrs if a is not None]
    if len(valid_atrs) >= 168:
        atr_recent = mean(valid_atrs[-24:])
        atr_long   = mean(valid_atrs[-168:])
        ratio = atr_recent / atr_long if atr_long > 0 else 1.0
        ind.price_coiling = max(0.0, min(1.0, 1.0 - ratio))
        raw["atr_24h"] = atr_recent
        raw["atr_7d"] = atr_long

    # 5. MA Convergence
    if len(closes) >= 50:
        ma20 = _rolling_mean(closes, 20)
        ma50 = _rolling_mean(closes, 50)
        # 取最近 24 根的 |ma20-ma50|/price 标准差,越小越缠绕
        gaps = []
        for i in range(max(0, len(closes)-24), len(closes)):
            if ma20[i] is not None and ma50[i] is not None and closes[i] > 0:
                gaps.append(abs(ma20[i] - ma50[i]) / closes[i])
        if len(gaps) >= 12:
            avg_gap = mean(gaps)
            # 0.5% gap 内视为高度缠绕 → 满分
            ind.ma_convergence = max(0.0, min(1.0, 1.0 - avg_gap / 0.01))
            raw["ma_avg_gap_pct"] = avg_gap * 100

    # 6. Funding Extremity
    if len(funding_rates) >= 10:
        try:
            rates = [float(f["fundingRate"]) for f in funding_rates]
            cur = rates[-1]
            med = median(rates[:-1])
            sd = stdev(rates[:-1]) if len(rates) > 2 else 0.0001
            if sd > 0:
                z = abs(cur - med) / sd
                ind.funding_extremity = min(1.0, z / 3.0)
                raw["funding_now"] = cur
                raw["funding_z"] = z
        except Exception:
            pass

    # 7. Range Compression: 4h range / 24h range
    if len(klines) >= 24:
        last_4h_high = max(highs[-4:])
        last_4h_low = min(lows[-4:])
        last_24h_high = max(highs[-24:])
        last_24h_low = min(lows[-24:])
        rng_4h = last_4h_high - last_4h_low
        rng_24h = last_24h_high - last_24h_low
        if rng_24h > 0:
            ratio = rng_4h / rng_24h
            # 0.3 = 4h range 仅占 24h 的 30% → 高度压缩 → 满分
            ind.range_compression = max(0.0, min(1.0, 1.0 - ratio / 0.5))
            raw["range_4h"] = rng_4h
            raw["range_24h"] = rng_24h

    # 8. Time in Squeeze: 连续 N 根 BB 处于均值以下
    if valid_bb and len(valid_bb) >= 50:
        avg_bb = mean(valid_bb[-100:])
        threshold = avg_bb * 0.7
        count = 0
        for b in reversed(valid_bb):
            if b < threshold:
                count += 1
            else:
                break
        # 8h+ 连续紧缩 = 满分
        ind.time_in_squeeze = min(1.0, count / 8.0)
        raw["squeeze_hours"] = count

    ind.raw = raw
    return ind


def compute_ignition_indicators(klines_5m: list, klines_15m: list, klines_1h: list, ind: Indicators) -> None:
    """阶段 A2: 启动信号 (just-started breakout). 直接修改 ind 对象."""
    raw = ind.raw

    if len(klines_5m) >= 30:
        closes_5m = _close_prices(klines_5m)
        highs_5m  = _high_prices(klines_5m)
        lows_5m   = _low_prices(klines_5m)
        vols_5m   = _volumes_quote(klines_5m)

        # 1. Candle Burst — 最近 1-2 根 5m 是大阳/大阴线 (body / atr_5m > 2)
        atrs_5m = _atr(klines_5m, window=14)
        valid_atrs = [a for a in atrs_5m if a is not None]
        if valid_atrs:
            atr_pct = valid_atrs[-1]
            if atr_pct > 0:
                # 检查最近 3 根 5m,取最大 body/atr
                max_body_ratio = 0.0
                max_body_dir = 0   # +1 阳, -1 阴
                for k in klines_5m[-3:]:
                    o, c = float(k[1]), float(k[4])
                    body_pct = abs(c - o) / o if o > 0 else 0
                    ratio = body_pct / atr_pct if atr_pct > 0 else 0
                    if ratio > max_body_ratio:
                        max_body_ratio = ratio
                        max_body_dir = 1 if c > o else -1
                # body > 2x ATR 即满分
                ind.candle_burst_5m = min(1.0, max_body_ratio / 2.0)
                raw["candle_burst_ratio"] = max_body_ratio
                raw["candle_burst_dir"]   = max_body_dir
                raw["atr_5m_pct"] = atr_pct * 100

        # 2. Volume Surge — 最近 1 根 5m 量 / 24根 5m 均
        if len(vols_5m) >= 25:
            recent_vol = vols_5m[-1]
            avg_vol = mean(vols_5m[-25:-1]) if mean(vols_5m[-25:-1]) > 0 else 1
            ratio = recent_vol / avg_vol
            # 5x avg = 满分
            ind.volume_surge_5m = min(1.0, ratio / 5.0)
            raw["vol_surge_ratio_5m"] = ratio

    # 3. Range Breakout — 最近价格 vs 24h 范围
    if len(klines_1h) >= 24:
        closes_1h = _close_prices(klines_1h)
        highs_1h = _high_prices(klines_1h)
        lows_1h  = _low_prices(klines_1h)
        cur = closes_1h[-1]
        h24 = max(highs_1h[-24:-1])  # 排除当前
        l24 = min(lows_1h[-24:-1])
        rng = h24 - l24
        if rng > 0:
            # 突破多少: 0 = 在区间内, +1 = 完全突破上沿, -1 = 完全突破下沿
            if cur > h24:
                breakout = (cur - h24) / rng  # 上突破
                ind.range_breakout = min(1.0, breakout * 5)  # 突破 20% rng 即满分
                raw["range_breakout_dir"] = 1
                raw["range_breakout_pct"] = (cur - h24) / h24 * 100
            elif cur < l24:
                breakout = (l24 - cur) / rng
                ind.range_breakout = min(1.0, breakout * 5)
                raw["range_breakout_dir"] = -1
                raw["range_breakout_pct"] = (cur - l24) / l24 * 100
            else:
                # 接近上/下沿但未突破也给部分分
                upper_proximity = 1.0 - max(0, (h24 - cur) / rng)
                lower_proximity = 1.0 - max(0, (cur - l24) / rng)
                proximity_score = max(upper_proximity, lower_proximity)
                if proximity_score > 0.85:  # 距离边界 < 15%
                    ind.range_breakout = (proximity_score - 0.85) / 0.15 * 0.5  # 最多 0.5
                    raw["range_breakout_dir"] = 1 if upper_proximity > lower_proximity else -1
                    raw["range_breakout_pct"] = 0.0

    # 4. Trend Acceleration — 15m 短期均线发散 + 加速
    if len(klines_15m) >= 25:
        closes_15m = _close_prices(klines_15m)
        ma5  = _rolling_mean(closes_15m, 5)
        ma20 = _rolling_mean(closes_15m, 20)
        if ma5[-1] is not None and ma20[-1] is not None and closes_15m[-1] > 0:
            spread_now = (ma5[-1] - ma20[-1]) / closes_15m[-1]
            spread_prev = ((ma5[-3] - ma20[-3]) / closes_15m[-3]
                           if ma5[-3] is not None and ma20[-3] is not None else 0)
            # 发散加速 = |spread_now| > |spread_prev| 且同向
            accel = abs(spread_now) - abs(spread_prev)
            if accel > 0:
                # 0.5% 加速 = 满分
                ind.trend_acceleration = min(1.0, accel / 0.005)
                raw["trend_spread_now_pct"] = spread_now * 100
                raw["trend_accel_pct"] = accel * 100


def aggregate_setup_score(ind: Indicators) -> float:
    """蓄势 (setup) 综合分 — 越高越接近爆发."""
    return (
        SETUP_WEIGHTS["bb_squeeze"]         * ind.bb_squeeze +
        SETUP_WEIGHTS["oi_velocity"]        * ind.oi_velocity +
        SETUP_WEIGHTS["volume_spike"]       * ind.volume_spike +
        SETUP_WEIGHTS["price_coiling"]      * ind.price_coiling +
        SETUP_WEIGHTS["ma_convergence"]     * ind.ma_convergence +
        SETUP_WEIGHTS["funding_extremity"]  * ind.funding_extremity +
        SETUP_WEIGHTS["range_compression"]  * ind.range_compression +
        SETUP_WEIGHTS["time_in_squeeze"]    * ind.time_in_squeeze
    )


def aggregate_ignition_score(ind: Indicators) -> float:
    """启动 (ignition) 综合分 — 越高越像刚启动."""
    return (
        IGNITION_WEIGHTS["candle_burst_5m"]    * ind.candle_burst_5m +
        IGNITION_WEIGHTS["volume_surge_5m"]    * ind.volume_surge_5m +
        IGNITION_WEIGHTS["range_breakout"]     * ind.range_breakout +
        IGNITION_WEIGHTS["trend_acceleration"] * ind.trend_acceleration
    )


def aggregate_score(ind: Indicators) -> float:
    """兼容旧调用. 取 max(setup, ignition × 1.1) — 启动分稍微加权,因为已经在动."""
    setup = aggregate_setup_score(ind)
    ignition = aggregate_ignition_score(ind)
    return max(setup, ignition * 1.1)


def infer_direction(ind: Indicators, klines_1h: list) -> Tuple[str, List[str]]:
    """量化阶段方向推断. 返回 (LONG/SHORT/NEUTRAL, 理由列表)."""
    raw = ind.raw
    long_score = 0.0
    short_score = 0.0
    reasons = []

    # 1. 启动方向 (5m 大蜡烛方向)
    burst_dir = raw.get("candle_burst_dir", 0)
    burst_ratio = raw.get("candle_burst_ratio", 0)
    if burst_dir > 0 and burst_ratio > 1.5:
        long_score += 2.0
        reasons.append(f"5m 大阳线 body/ATR={burst_ratio:.1f}x")
    elif burst_dir < 0 and burst_ratio > 1.5:
        short_score += 2.0
        reasons.append(f"5m 大阴线 body/ATR={burst_ratio:.1f}x")

    # 2. 区间突破方向
    bo_dir = raw.get("range_breakout_dir", 0)
    bo_pct = raw.get("range_breakout_pct", 0)
    if bo_dir > 0 and bo_pct > 0:
        long_score += 1.5
        reasons.append(f"突破 24h 区间上沿 +{bo_pct:.2f}%")
    elif bo_dir < 0 and bo_pct < 0:
        short_score += 1.5
        reasons.append(f"跌破 24h 区间下沿 {bo_pct:.2f}%")
    elif bo_dir > 0:
        long_score += 0.5
        reasons.append("接近 24h 区间上沿")
    elif bo_dir < 0:
        short_score += 0.5
        reasons.append("接近 24h 区间下沿")

    # 3. 资金费率拥挤反转 (蓄势场景)
    funding_now = raw.get("funding_now", 0)
    funding_z = raw.get("funding_z", 0)
    if funding_z > 1.5:
        if funding_now > 0:
            short_score += 1.0
            reasons.append(f"资金费极正({funding_now*100:.4f}%) → 多头拥挤,反转概率高")
        elif funding_now < 0:
            long_score += 1.0
            reasons.append(f"资金费极负({funding_now*100:.4f}%) → 空头拥挤,反弹概率高")

    # 4. 趋势加速方向 (15m)
    trend_spread = raw.get("trend_spread_now_pct", 0)
    if abs(trend_spread) > 0.3:
        if trend_spread > 0:
            long_score += 0.8
            reasons.append(f"15m MA5>MA20 (+{trend_spread:.2f}%) 上行加速")
        else:
            short_score += 0.8
            reasons.append(f"15m MA5<MA20 ({trend_spread:.2f}%) 下行加速")

    # 5. 价格在区间相对位置 (蓄势 + 接近边界)
    if len(klines_1h) >= 24:
        closes = _close_prices(klines_1h)
        highs = _high_prices(klines_1h)
        lows  = _low_prices(klines_1h)
        cur = closes[-1]
        h24 = max(highs[-24:])
        l24 = min(lows[-24:])
        if h24 > l24:
            position = (cur - l24) / (h24 - l24)  # 0 底, 1 顶
            if position < 0.2:
                long_score += 0.3
                reasons.append(f"位于 24h 底部区域(下沿+{position*100:.0f}%)")
            elif position > 0.8:
                short_score += 0.3
                reasons.append(f"位于 24h 顶部区域(上沿-{(1-position)*100:.0f}%)")

    # 决策
    diff = abs(long_score - short_score)
    if diff < 0.5:
        return "NEUTRAL", reasons
    if long_score > short_score:
        return "LONG", reasons
    return "SHORT", reasons


# ============================================================================
# DeepSeek deep analysis (Phase B)
# ============================================================================

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

ANALYSIS_SYSTEM_PROMPT = """你是专业的加密货币高频交易分析师。请基于提供的多维数据,判断该标的是否处于"异动前夕"(即将爆发大涨或大跌)。

只输出 JSON,不要任何额外文本。结构如下:
{
  "prediction": "BREAKOUT_LONG" | "BREAKOUT_SHORT" | "NEUTRAL",
  "confidence": 0-100 整数,
  "expected_move_pct": "+5% ~ +10%" 或 "-3% ~ -8%" 或 null,
  "expected_window": "30min" | "1-2h" | "2-4h" | "4-8h" | null,
  "key_evidence": ["证据1 (具体引用数据)", "证据2", "证据3"],
  "key_risks": ["风险1", "风险2"]
}

判断原则:
- BREAKOUT_LONG: 蓄势中 + 资金费率极负 (空头拥挤反转) / 接近近期低点反弹 / OI 累积但价未涨
- BREAKOUT_SHORT: 蓄势中 + 资金费率极正 (多头拥挤砸盘) / 接近近期高点失败 / 派发结构
- NEUTRAL: 信号不足或方向矛盾
- confidence 严格分级: 80+ 强烈, 65-79 较强, 50-64 中性, <50 弱
- 不要过度自信。证据不足就 NEUTRAL"""


def _build_analysis_prompt(c: Candidate, klines: list) -> str:
    """Construct user prompt with candidate's multi-dimensional data."""
    closes = _close_prices(klines)
    highs  = _high_prices(klines)
    lows   = _low_prices(klines)

    if len(closes) < 24:
        return ""

    h24 = max(highs[-24:])
    l24 = min(lows[-24:])
    h7d = max(highs[-168:]) if len(highs) >= 168 else max(highs)
    l7d = min(lows[-168:])  if len(lows)  >= 168 else min(lows)
    cur = closes[-1]

    dist_high_24h = (cur - h24) / h24 * 100 if h24 > 0 else 0
    dist_low_24h  = (cur - l24) / l24 * 100 if l24 > 0 else 0
    dist_high_7d  = (cur - h7d) / h7d * 100 if h7d > 0 else 0
    dist_low_7d   = (cur - l7d) / l7d * 100 if l7d > 0 else 0

    raw = c.indicators.raw

    # 最近 12 根 1h K 线 摘要
    recent_klines_summary = []
    for k in klines[-12:]:
        ts = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        recent_klines_summary.append(
            f"[{ts}] O:{float(k[1]):.6g} H:{float(k[2]):.6g} L:{float(k[3]):.6g} C:{float(k[4]):.6g} V:{float(k[7])/1e6:.2f}M"
        )

    return f"""SYMBOL: {c.symbol}
当前价: {cur:.6g}
24h 涨跌: {c.price_change_24h_pct:+.2f}%
breakout_score: {c.breakout_score:.3f} (蓄势 {c.setup_score:.3f} / 启动 {c.ignition_score:.3f})

【量化阶段方向推断】: {c.direction_hint}
理由:
{chr(10).join(['- ' + r for r in c.direction_reasons])}

【蓄势指标】(0-1, 越大越接近爆发):
- BB 收口度:        {c.indicators.bb_squeeze:.2f}    (BB 带宽 {raw.get('bb_width_now', 0)*100:.2f}% / 7d 均值 {raw.get('bb_width_7d_avg', 0)*100:.2f}%)
- OI 加速度:        {c.indicators.oi_velocity:.2f}    (1h 变化 {raw.get('oi_1h_change', 0)*100:.2f}% vs 均 {raw.get('oi_24h_avg_change', 0)*100:.2f}%)
- 成交量异常 (1h):  {c.indicators.volume_spike:.2f}   (1h 量 {raw.get('vol_1h', 0)/1e6:.1f}M vs 24h均 {raw.get('vol_24h_avg', 0)/1e6:.1f}M)
- 价格收敛 (ATR):   {c.indicators.price_coiling:.2f}  (24h ATR% {raw.get('atr_24h', 0)*100:.2f} vs 7d {raw.get('atr_7d', 0)*100:.2f})
- 均线缠绕:         {c.indicators.ma_convergence:.2f} (MA20/50 间距 {raw.get('ma_avg_gap_pct', 0):.2f}%)
- 资金费率极端:     {c.indicators.funding_extremity:.2f} (当前 {raw.get('funding_now', 0)*100:.4f}%, z={raw.get('funding_z', 0):.2f})
- 区间压缩:         {c.indicators.range_compression:.2f} (4h/24h)
- 横盘时长:         {c.indicators.time_in_squeeze:.2f}  ({int(raw.get('squeeze_hours', 0))} 小时连续紧缩)

【启动指标】(0-1, 越大越像刚启动):
- 5m 大蜡烛:        {c.indicators.candle_burst_5m:.2f} (body/ATR={raw.get('candle_burst_ratio', 0):.2f}x, 方向 {raw.get('candle_burst_dir', 0)})
- 5m 量爆发:        {c.indicators.volume_surge_5m:.2f} ({raw.get('vol_surge_ratio_5m', 0):.2f}x 均值)
- 区间突破:         {c.indicators.range_breakout:.2f}  (突破 {raw.get('range_breakout_pct', 0):+.2f}%, 方向 {raw.get('range_breakout_dir', 0)})
- 趋势加速 (15m):   {c.indicators.trend_acceleration:.2f} (MA5-MA20 {raw.get('trend_spread_now_pct', 0):+.2f}%)

价格结构:
- 距 24h 高点 ({h24:.6g}): {dist_high_24h:+.2f}%
- 距 24h 低点 ({l24:.6g}): {dist_low_24h:+.2f}%
- 距 7d  高点 ({h7d:.6g}): {dist_high_7d:+.2f}%
- 距 7d  低点 ({l7d:.6g}): {dist_low_7d:+.2f}%

最近 12 根 1h K 线:
{chr(10).join(recent_klines_summary)}

任务: 判断该标的是即将"异动前夕(蓄势型)"还是"刚启动(启动型)"?方向是 LONG 还是 SHORT?
- 严肃验证或推翻量化阶段的方向推断
- 引用具体数据作证据(数字精确)
- 标识 1-2 个关键风险
- 严格 JSON 输出"""


def call_deepseek(prompt: str, api_key: str) -> Optional[dict]:
    """Call DeepSeek chat completion. Return parsed JSON dict or None."""
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _http_post_json(DEEPSEEK_API_URL, body, headers, timeout=60)
    if not resp:
        return None
    try:
        content = resp["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[precog] DeepSeek parse failed: {e}", file=sys.stderr)
        return None


def analyze_candidate(c: Candidate, klines: list, api_key: str) -> Optional[Candidate]:
    """Phase B: enrich candidate with AI prediction."""
    prompt = _build_analysis_prompt(c, klines)
    if not prompt:
        return c
    result = call_deepseek(prompt, api_key)
    if not result:
        return c
    c.ai_prediction    = result.get("prediction")
    c.ai_confidence    = result.get("confidence")
    c.expected_move    = result.get("expected_move_pct")
    c.expected_window  = result.get("expected_window")
    c.key_evidence     = result.get("key_evidence") or []
    c.key_risks        = result.get("key_risks") or []
    return c


# ============================================================================
# Main pipeline
# ============================================================================

def is_excluded(symbol: str) -> bool:
    if not symbol or not symbol.isascii():
        return True
    if not symbol.endswith("USDT"):
        return True
    base = symbol[:-4]
    if not base or len(base) > 12:
        return True
    for p in EXCLUDE_PATTERNS:
        if p in symbol:
            return True
    return False


def run_pipeline(
    output_path: Path = DEFAULT_OUTPUT,
    use_ai: bool = True,
    verbose: bool = False,
) -> dict:
    cache = JsonCache(CACHE_DIR)

    # ---- Universe ----
    if verbose: print("[precog] [1/5] Pulling universe...", file=sys.stderr)
    universe = fetch_top_symbols(cache)
    universe = [s for s in universe if not is_excluded(s)]
    if not universe:
        print("[precog] FAILED: empty universe", file=sys.stderr)
        return {}
    if verbose: print(f"[precog] Universe: {len(universe)} symbols", file=sys.stderr)

    okx_set = fetch_okx_swap_set()
    if verbose: print(f"[precog] OKX SWAP whitelist: {len(okx_set)} bases", file=sys.stderr)

    # ---- Phase A: 蓄势 + 启动 双轨 indicators ----
    if verbose: print("[precog] [2/5] Computing indicators (3 timeframes)...", file=sys.stderr)
    candidates: List[Candidate] = []
    klines_cache: Dict[str, list] = {}

    for i, sym in enumerate(universe):
        if verbose and i % 30 == 0:
            print(f"[precog]   ... {i}/{len(universe)} {sym}", file=sys.stderr)
        klines_1h = fetch_klines(sym, cache, KLINE_INTERVAL_MAIN, KLINE_LIMIT_MAIN)
        if len(klines_1h) < 50:
            continue
        oi    = fetch_oi_hist(sym, cache)
        fund  = fetch_funding_rate(sym, cache)
        ind   = compute_indicators(sym, klines_1h, oi, fund)
        if not ind:
            continue

        # Phase A2: 启动指标 (5m + 15m) — 仅对蓄势分 OR 24h 涨跌足够大的标的拉
        setup_score = aggregate_setup_score(ind)
        prelim_change = 0.0
        try:
            prelim_change = (float(klines_1h[-1][4]) - float(klines_1h[-25][4])) / float(klines_1h[-25][4]) * 100
        except Exception:
            pass

        # 优化: 只有 setup 分够高 OR 价格已有动作时才拉 5m/15m (省 API 调用)
        need_ignition = setup_score >= 0.30 or abs(prelim_change) >= 3.0
        if need_ignition:
            klines_5m = fetch_klines(sym, cache, KLINE_INTERVAL_FAST, KLINE_LIMIT_FAST)
            klines_15m = fetch_klines(sym, cache, KLINE_INTERVAL_MID, KLINE_LIMIT_MID)
            compute_ignition_indicators(klines_5m, klines_15m, klines_1h, ind)
        ignition_score = aggregate_ignition_score(ind)

        breakout_score = max(setup_score, ignition_score * 1.1)
        if breakout_score < 0.30:  # 双轨都低 → 淘汰,省 AI
            continue

        # 量化阶段先推断方向
        dir_hint, dir_reasons = infer_direction(ind, klines_1h)

        # 价格结构
        closes = _close_prices(klines_1h)
        highs  = _high_prices(klines_1h)
        lows   = _low_prices(klines_1h)
        cur = closes[-1]
        h24 = max(highs[-24:]) if len(highs) >= 24 else cur
        l24 = min(lows[-24:])  if len(lows)  >= 24 else cur
        chg_24h = ((cur - closes[-25]) / closes[-25] * 100) if len(closes) >= 25 else 0
        dist_h = (cur - h24) / h24 * 100 if h24 > 0 else 0
        dist_l = (cur - l24) / l24 * 100 if l24 > 0 else 0

        base = sym[:-4].upper()
        c = Candidate(
            symbol=sym,
            base=base,
            price=cur,
            setup_score=round(setup_score, 4),
            ignition_score=round(ignition_score, 4),
            breakout_score=round(breakout_score, 4),
            direction_hint=dir_hint,
            direction_reasons=dir_reasons,
            indicators=ind,
            tradable_okx=base in okx_set,
            okx_inst_id=f"{base}-USDT-SWAP" if base in okx_set else None,
            price_change_24h_pct=round(chg_24h, 2),
            dist_24h_high_pct=round(dist_h, 2),
            dist_24h_low_pct=round(dist_l, 2),
        )
        candidates.append(c)
        klines_cache[sym] = klines_1h

    if not candidates:
        print("[precog] No candidates passed Phase A", file=sys.stderr)
        return _empty_payload()

    candidates.sort(key=lambda c: -c.breakout_score)
    candidates = candidates[:CANDIDATE_TOP_N]
    if verbose:
        ig = sum(1 for c in candidates if c.ignition_score > c.setup_score)
        st = len(candidates) - ig
        print(f"[precog] Phase A done: {len(candidates)} candidates ({st} 蓄势型 + {ig} 启动型)", file=sys.stderr)

    # ---- Phase B: AI analysis ----
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        # Fallback: try ~/cresus-bot/.env
        env_file = Path.home() / "cresus-bot" / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if api_key:
                            print(f"[precog] Loaded DEEPSEEK_API_KEY from {env_file}", file=sys.stderr)
                        break
            except Exception:
                pass
    ai_used = False
    if use_ai and api_key:
        if verbose: print(f"[precog] [3/5] Calling DeepSeek for {len(candidates)} candidates...", file=sys.stderr)
        for i, c in enumerate(candidates):
            klines = klines_cache.get(c.symbol, [])
            if not klines:
                continue
            analyze_candidate(c, klines, api_key)
            if verbose:
                pred = c.ai_prediction or "—"
                conf = c.ai_confidence if c.ai_confidence is not None else "—"
                print(f"[precog]   ... [{i+1}/{len(candidates)}] {c.symbol}: {pred} (conf={conf})", file=sys.stderr)
        ai_used = True
    else:
        if not api_key:
            print("[precog] WARNING: DEEPSEEK_API_KEY not set, skipping AI phase (degraded mode)", file=sys.stderr)

    # ---- Phase C: rank ----
    if verbose: print("[precog] [4/5] Ranking final Top 5 LONG / SHORT...", file=sys.stderr)
    if ai_used:
        # AI 可用: 用 AI 预测 + AI 置信度
        longs  = [c for c in candidates if c.ai_prediction == "BREAKOUT_LONG"  and (c.ai_confidence or 0) >= 65]
        shorts = [c for c in candidates if c.ai_prediction == "BREAKOUT_SHORT" and (c.ai_confidence or 0) >= 65]
        longs.sort(key=lambda c: -((c.ai_confidence or 0) * c.breakout_score))
        shorts.sort(key=lambda c: -((c.ai_confidence or 0) * c.breakout_score))
    else:
        # 降级: 用量化方向推断 + breakout_score
        longs  = [c for c in candidates if c.direction_hint == "LONG"]
        shorts = [c for c in candidates if c.direction_hint == "SHORT"]
        longs.sort(key=lambda c: -c.breakout_score)
        shorts.sort(key=lambda c: -c.breakout_score)
    longs  = longs[:FINAL_TOP_N]
    shorts = shorts[:FINAL_TOP_N]

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "universe":         len(universe),
            "phase_a_passed":   len(candidates),
            "phase_b_analyzed": len(candidates) if ai_used else 0,
            "longs":            len(longs),
            "shorts":           len(shorts),
            "ai_used":          ai_used,
        },
        "longs":      [_candidate_dict(c) for c in longs],
        "shorts":     [_candidate_dict(c) for c in shorts],
        "all_candidates": [_candidate_dict(c) for c in candidates],
    }

    if verbose: print(f"[precog] [5/5] Writing → {output_path}", file=sys.stderr)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(output_path)

    # ===== Phase 2: outcome tracking (放在主文件写入之后, 即使追踪失败也不影响主输出) =====
    _run_outcome_tracking(longs, shorts, ai_used, payload["updated_at"], verbose=verbose)

    return payload


# ============================================================================
# Phase 2: outcome tracking + win-rate (与 velocity 同 schema, 用于 A/B 评估)
# ============================================================================

def _load_precog_outcomes() -> dict:
    if not OUTCOMES_STATE.exists():
        return {"predictions": {}}
    try:
        data = json.loads(OUTCOMES_STATE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "predictions" not in data:
            return {"predictions": {}}
        return data
    except Exception:
        return {"predictions": {}}


def _save_precog_outcomes(state: dict) -> None:
    try:
        OUTCOMES_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUTCOMES_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUTCOMES_STATE)
    except Exception as e:
        print(f"[precog] save_outcomes failed: {e}", file=sys.stderr)


def _pred_type(c: Candidate) -> str:
    """ignition (启动) vs setup (蓄势), 与 dashboard 图标对应."""
    return "ignition" if (c.ignition_score or 0) > (c.setup_score or 0) else "setup"


def _pred_direction(c: Candidate) -> Optional[str]:
    """优先用 AI 预测, 其次量化 hint. NEUTRAL/None 跳过."""
    if c.ai_prediction == "BREAKOUT_LONG":
        return "LONG"
    if c.ai_prediction == "BREAKOUT_SHORT":
        return "SHORT"
    if c.direction_hint in ("LONG", "SHORT"):
        return c.direction_hint
    return None


def _prediction_id(c: Candidate, detected_at: str) -> str:
    direction = _pred_direction(c) or "NEUTRAL"
    return f"{c.symbol}|{_pred_type(c)}|{direction}|{detected_at}"


def _log_predictions_to_outcomes(state: dict,
                                 longs: List[Candidate],
                                 shorts: List[Candidate],
                                 ai_used: bool,
                                 detected_at: str) -> int:
    """Top LONG + Top SHORT (实际给出的入场建议) 落库为待结算.
    去重: 同 (symbol, pred_type, direction) 在 PRED_DEDUP_WINDOW_MIN 内只算 1 次.
    """
    try:
        now = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
    except Exception:
        now = datetime.now(timezone.utc)
    dedup_cutoff = now - timedelta(minutes=PRED_DEDUP_WINDOW_MIN)

    # 构建已存在 (symbol|pred_type|direction) → 最近 detected_at 的索引
    recent_by_natural_key: dict = {}
    for rec in state["predictions"].values():
        nat_key = f"{rec['symbol']}|{rec['pred_type']}|{rec['direction']}"
        try:
            ts = datetime.fromisoformat(rec["detected_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= dedup_cutoff:
            prev = recent_by_natural_key.get(nat_key)
            if prev is None or ts > prev:
                recent_by_natural_key[nat_key] = ts

    logged = 0
    for c in (longs + shorts):
        direction = _pred_direction(c)
        if not direction:
            continue
        nat_key = f"{c.symbol}|{_pred_type(c)}|{direction}"
        if nat_key in recent_by_natural_key:
            continue  # 窗口内已有, 跳过
        pid = _prediction_id(c, detected_at)
        if pid in state["predictions"]:
            continue  # 完全相同的 detected_at 不重复
        state["predictions"][pid] = {
            "symbol": c.symbol,
            "pred_type": _pred_type(c),
            "direction": direction,
            "ai_used": ai_used,
            "ai_confidence": c.ai_confidence,
            "breakout_score": round(c.breakout_score or 0, 3),
            "entry_price": c.price,
            "detected_at": detected_at,
            "outcomes": {f"{m}m": None for m in OUTCOME_STAGES_MIN},
        }
        logged += 1
    return logged


def _fetch_all_prices_now() -> dict:
    """1 次 bulk 拉所有 USDT-M 永续现价."""
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


def _resolve_matured_precog_outcomes(state: dict) -> int:
    now = datetime.now(timezone.utc)
    pending: List[tuple] = []
    for pid, rec in state["predictions"].items():
        try:
            det = datetime.fromisoformat(rec["detected_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        age_min = (now - det).total_seconds() / 60.0
        for m in OUTCOME_STAGES_MIN:
            key = f"{m}m"
            if rec["outcomes"].get(key) is None and age_min >= m:
                pending.append((pid, key, m))
    if not pending:
        return 0
    prices = _fetch_all_prices_now()
    if not prices:
        return 0
    resolved = 0
    for pid, key, _m in pending:
        rec = state["predictions"].get(pid)
        if not rec:
            continue
        cur = prices.get(rec["symbol"])
        if cur is None:
            continue
        entry = float(rec["entry_price"])
        if entry <= 0:
            continue
        raw_pct = (cur - entry) / entry * 100.0
        signed_pct = raw_pct if rec["direction"] == "LONG" else -raw_pct
        rec["outcomes"][key] = {
            "price": cur,
            "outcome_pct": round(signed_pct, 3),
            "resolved_at": now.isoformat(),
        }
        resolved += 1
    return resolved


def _prune_old_precog_outcomes(state: dict) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=OUTCOMES_RETENTION_DAYS)
    to_del = []
    for pid, rec in state["predictions"].items():
        try:
            det = datetime.fromisoformat(rec["detected_at"].replace("Z", "+00:00"))
            if det < cutoff:
                to_del.append(pid)
        except Exception:
            to_del.append(pid)
    for pid in to_del:
        del state["predictions"][pid]
    return len(to_del)


def _build_precog_winrate_summary(state: dict) -> dict:
    """聚合: by_key (per symbol+type+direction) + overall (AI vs quant-only A/B)."""
    by_key_buckets: dict = {}
    overall: dict = {
        "with_ai":    {f"{m}m": {"n": 0, "wins": 0, "sum_pct": 0.0} for m in OUTCOME_STAGES_MIN},
        "quant_only": {f"{m}m": {"n": 0, "wins": 0, "sum_pct": 0.0} for m in OUTCOME_STAGES_MIN},
    }
    for rec in state["predictions"].values():
        key = f"{rec['symbol']}|{rec['pred_type']}|{rec['direction']}"
        b = by_key_buckets.setdefault(key, {
            "symbol": rec["symbol"],
            "pred_type": rec["pred_type"],
            "direction": rec["direction"],
            "stages": {f"{m}m": {"n": 0, "wins": 0, "sum_pct": 0.0} for m in OUTCOME_STAGES_MIN},
        })
        ai_bucket = "with_ai" if rec.get("ai_used") else "quant_only"
        for m in OUTCOME_STAGES_MIN:
            stk = f"{m}m"
            oc = rec["outcomes"].get(stk)
            if not oc:
                continue
            pct = oc.get("outcome_pct", 0.0)
            is_win = pct >= OUTCOME_WIN_THRESHOLD_PCT
            b["stages"][stk]["n"] += 1
            b["stages"][stk]["sum_pct"] += pct
            if is_win:
                b["stages"][stk]["wins"] += 1
            overall[ai_bucket][stk]["n"] += 1
            overall[ai_bucket][stk]["sum_pct"] += pct
            if is_win:
                overall[ai_bucket][stk]["wins"] += 1

    out_by_key = {}
    for key, b in by_key_buckets.items():
        stages_out = {}
        for stk, s in b["stages"].items():
            if s["n"] >= WINRATE_MIN_SAMPLES:
                stages_out[stk] = {
                    "n": s["n"],
                    "win_rate": round(s["wins"] / s["n"], 3),
                    "avg_outcome_pct": round(s["sum_pct"] / s["n"], 2),
                }
        if stages_out:
            out_by_key[key] = {
                "symbol": b["symbol"],
                "pred_type": b["pred_type"],
                "direction": b["direction"],
                "stages": stages_out,
            }

    out_overall = {}
    for bucket, stages in overall.items():
        out_overall[bucket] = {}
        for stk, s in stages.items():
            if s["n"] >= WINRATE_MIN_SAMPLES:
                out_overall[bucket][stk] = {
                    "n": s["n"],
                    "win_rate": round(s["wins"] / s["n"], 3),
                    "avg_outcome_pct": round(s["sum_pct"] / s["n"], 2),
                }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "win_threshold_pct": OUTCOME_WIN_THRESHOLD_PCT,
        "min_samples": WINRATE_MIN_SAMPLES,
        "by_key": out_by_key,
        "overall": out_overall,
    }


def _save_precog_winrate(summary: dict) -> None:
    try:
        WINRATE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = WINRATE_OUTPUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(WINRATE_OUTPUT)
    except Exception as e:
        print(f"[precog] save_winrate failed: {e}", file=sys.stderr)


def _run_outcome_tracking(longs: List[Candidate],
                          shorts: List[Candidate],
                          ai_used: bool,
                          detected_at: str,
                          verbose: bool = True) -> None:
    """嵌入 run_pipeline 末尾: 落库 + 结算 + 汇总."""
    try:
        state = _load_precog_outcomes()
        new_n = _log_predictions_to_outcomes(state, longs, shorts, ai_used, detected_at)
        resolved_n = _resolve_matured_precog_outcomes(state)
        pruned_n = _prune_old_precog_outcomes(state)
        _save_precog_outcomes(state)
        summary = _build_precog_winrate_summary(state)
        _save_precog_winrate(summary)
        if verbose:
            print(f"[precog] 📊 outcomes: 新增 {new_n}, 结算 {resolved_n}, 清理 {pruned_n}, "
                  f"bucket={len(summary.get('by_key', {}))}", file=sys.stderr)
    except Exception as e:
        print(f"[precog] outcome tracking failed: {e}", file=sys.stderr)


# ============================================================================
# Payload helpers
# ============================================================================

def _empty_payload() -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {"universe": 0, "phase_a_passed": 0, "phase_b_analyzed": 0,
                  "longs": 0, "shorts": 0, "ai_used": False},
        "longs": [], "shorts": [], "all_candidates": [],
    }


def _candidate_dict(c: Candidate) -> dict:
    d = asdict(c)
    return d


# ============================================================================
# Self-test (synthetic data)
# ============================================================================

def cmd_test() -> int:
    """Test indicator math with synthetic klines."""
    print("=== Synthetic kline test (BB Squeeze) ===")
    # 100 candles, last 30 are tightly compressed
    closes = [100.0 + (i % 10) * 0.5 for i in range(70)] + [100.0 + (i % 3) * 0.1 for i in range(30)]
    bb = _bb_width(closes, window=20)
    valid = [b for b in bb if b is not None]
    last_bb = valid[-1]
    avg_bb = mean(valid[:-30])
    print(f"  last 30 candles bb_width: {last_bb:.4f}")
    print(f"  earlier avg bb_width: {avg_bb:.4f}")
    assert last_bb < avg_bb * 0.5, f"squeeze should be detected: {last_bb} vs {avg_bb}"
    print("  ✅ BB squeeze correctly detected")

    print("\n=== Setup score aggregation ===")
    ind = Indicators(
        bb_squeeze=0.8, oi_velocity=0.7, volume_spike=0.9,
        price_coiling=0.6, ma_convergence=0.7, funding_extremity=0.4,
        range_compression=0.7, time_in_squeeze=0.5,
    )
    setup = aggregate_setup_score(ind)
    print(f"  setup_score = {setup:.3f} (expected ~0.69)")
    assert 0.65 < setup < 0.75, f"unexpected setup score: {setup}"

    ind.candle_burst_5m = 0.9
    ind.volume_surge_5m = 0.85
    ind.range_breakout = 0.8
    ind.trend_acceleration = 0.7
    ignition = aggregate_ignition_score(ind)
    print(f"  ignition_score = {ignition:.3f} (expected ~0.81)")
    assert 0.78 < ignition < 0.85, f"unexpected ignition score: {ignition}"
    print("  ✅ Score aggregation OK")

    print("\n=== Direction inference ===")
    # 模拟启动型 LONG: 大阳线 + 上突破
    ind_long = Indicators()
    ind_long.raw = {
        "candle_burst_dir": 1, "candle_burst_ratio": 2.5,
        "range_breakout_dir": 1, "range_breakout_pct": 1.2,
        "trend_spread_now_pct": 0.0, "funding_z": 0,
    }
    fake_kl = [[0,100,101,99,100,0,0,1e6,0]] * 30  # flat klines for position calc
    direction, reasons = infer_direction(ind_long, fake_kl)
    print(f"  启动 LONG case: {direction} ({len(reasons)} reasons)")
    assert direction == "LONG", f"expected LONG, got {direction}"

    # 模拟蓄势 SHORT: 资金极正 + 接近 24h 高点
    ind_short = Indicators()
    ind_short.raw = {
        "funding_now": 0.001, "funding_z": 2.0,
    }
    high_kl = []
    for i in range(30):
        c = 100 + i * 0.1  # 上行至 102.9
        high_kl.append([0, c-0.5, c+0.5, c-1, c, 0, 0, 1e6, 0])
    direction, reasons = infer_direction(ind_short, high_kl)
    print(f"  蓄势 SHORT case: {direction} ({len(reasons)} reasons)")
    assert direction == "SHORT", f"expected SHORT, got {direction}"
    print("  ✅ Direction inference OK")

    print("\n=== Symbol exclusion ===")
    cases = [
        ("BTCUSDT", False),
        ("ETHUSDT", False),
        ("ONDOUSDT", False),
        ("BTCDOWNUSDT", True),
        ("USDCUSDT", True),
        ("FOOBUSD", True),
    ]
    for sym, expected in cases:
        actual = is_excluded(sym)
        marker = "✅" if actual == expected else "❌"
        print(f"  {marker} is_excluded({sym!r}) = {actual} (expected {expected})")
        assert actual == expected, f"mismatch for {sym}"

    print("\n✅ All self-tests pass")
    return 0


# ============================================================================
# CLI
# ============================================================================

def cmd_run(out_path: Path, use_ai: bool = True, verbose: bool = True) -> int:
    payload = run_pipeline(out_path, use_ai=use_ai, verbose=verbose)
    if not payload:
        return 1
    s = payload.get("stats", {})
    print(f"\n[precog] ✅ DONE")
    print(f"    universe={s.get('universe')}  passed_phase_A={s.get('phase_a_passed')}  ai_used={s.get('ai_used')}")
    print(f"    Top 5 LONG = {s.get('longs')} candidates")
    print(f"    Top 5 SHORT = {s.get('shorts')} candidates")

    longs = payload.get("longs", [])
    shorts = payload.get("shorts", [])
    src = "AI" if s.get("ai_used") else "量化方向推断 (无 AI)"

    def _print_candidate(c):
        mark = "✅" if c.get("tradable_okx") else "🟡"
        kind = "🚀 启动" if c.get("ignition_score", 0) > c.get("setup_score", 0) else "📦 蓄势"
        ai_conf = c.get("ai_confidence")
        conf_str = f"AI={ai_conf}" if ai_conf is not None else f"hint={c.get('direction_hint','—')}"
        print(f"  {mark} {kind} {c['symbol']:14} bo={c['breakout_score']:.3f} {conf_str:10} "
              f"24h {c.get('price_change_24h_pct',0):+.1f}%")
        for ev in (c.get('key_evidence') or c.get('direction_reasons') or [])[:2]:
            print(f"      · {ev}")

    if longs:
        print(f"\n📈 Top {len(longs)} LONG candidates ({src}):")
        for c in longs:
            _print_candidate(c)
    if shorts:
        print(f"\n📉 Top {len(shorts)} SHORT candidates ({src}):")
        for c in shorts:
            _print_candidate(c)

    if not longs and not shorts:
        print("\n[precog] No directional candidates")
        if not s.get("ai_used"):
            print("[precog] (DEEPSEEK_API_KEY not set, ran in quant-only mode)")
        all_c = payload.get("all_candidates", [])[:10]
        if all_c:
            print(f"\n   Top 10 by量化得分:")
            for c in all_c:
                _print_candidate(c)
    return 0


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="Crésus 预兆雷达 — AI-powered breakout predictor")
    p.add_argument("cmd", choices=["run", "test"])
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--no-ai", action="store_true", help="跳过 DeepSeek (仅量化前兆)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "run":
        return cmd_run(args.out, use_ai=not args.no_ai, verbose=not args.quiet)
    if args.cmd == "test":
        return cmd_test()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
