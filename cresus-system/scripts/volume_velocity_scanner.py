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

BINANCE_FAPI = "https://fapi.binance.com"
UA = "Mozilla/5.0 (Macintosh) cresus-velocity-scanner"
HTTP_TIMEOUT = 12

SCAN_TOP_N             = 200      # 24h 成交额 Top N 标的
KLINE_LIMIT            = 30       # 1m 数据条数 (30min 窗口)

# ---- 路径 A: 启动检测 (1m spike vs 30m 基线) ----
VOLUME_BURST_RATIO     = 5.0      # 1m 量 > 30m 均 × 此倍数
PRICE_MOVE_THRESHOLD   = 0.005    # 1m 价格变化 ≥ 0.5%

# ---- 路径 B: 持续动能 (1m vs 24h 全天分钟均 + 10m 累计趋势) ----
# 解决"BILLUSDT 已经飞了 2 小时,30m 均量也飞了,vol_ratio 跌回 3x"问题
SUSTAINED_VOL_24H_RATIO     = 5.0    # 1m 量 ≥ X × (24h_quoteVol / 1440)
SUSTAINED_PRICE_10M_THRESHOLD = 0.015  # 10m 累计变化 ≥ 1.5%

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
    volume_baseline_usdt: float  # burst=30m_avg, sustained=24h_avg/min
    volume_ratio: float     # burst=vs 30m_avg, sustained=vs 24h_avg/min
    detected_at: str        # ISO timestamp
    intensity: int          # 1-3 (基于 volume_ratio 和 price_change 综合)


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
    """30 根 1m K 线 (Binance 永续)."""
    url = f"{BINANCE_FAPI}/fapi/v1/klines?symbol={symbol}&interval=1m&limit={KLINE_LIMIT}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    return data


# ============================================================================
# Detection logic
# ============================================================================

def analyze_symbol(symbol: str, quote_vol_24h: Optional[float] = None) -> Optional[VelocityAlert]:
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
        )
    except (ValueError, TypeError, IndexError):
        return None


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
# Main scan
# ============================================================================

def run_scan() -> List[VelocityAlert]:
    universe = fetch_universe()
    if not universe:
        _log("empty universe, abort")
        return []

    dedup = _load_dedup()
    new_alerts: List[VelocityAlert] = []

    # 串行扫描 (避免触发 Binance rate limit)
    # 200 标的 × ~80ms/请求 ≈ 16 秒, 留余量
    for i, (sym, qv) in enumerate(universe):
        alert = analyze_symbol(sym, quote_vol_24h=qv)
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

    # 合并已有 alerts (近 60min 窗口) + 新增
    existing = _load_alerts()
    combined = _trim_old_alerts(existing) + [asdict(a) for a in new_alerts]
    # 按 detected_at 降序
    combined.sort(key=lambda a: a.get("detected_at", ""), reverse=True)
    _save_alerts(combined)

    if new_alerts:
        long_n  = sum(1 for a in new_alerts if a.direction == "LONG")
        short_n = sum(1 for a in new_alerts if a.direction == "SHORT")
        if long_n >= 30:
            _log(f"🌊 全市场上涨事件: {long_n} 个 LONG (市场级,非个股 alpha)")
        elif short_n >= 30:
            _log(f"🌊 全市场下跌事件: {short_n} 个 SHORT (市场级,非个股 alpha)")
        else:
            _log(f"✅ {len(new_alerts)} alerts (LONG={long_n} SHORT={short_n})")
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
        # 单标的快速测试 (拉单只 ticker 拿 24h vol 用于 sustained 路径)
        sym = argv[1] if len(argv) >= 2 else "BTCUSDT"
        tdata = _http_get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr?symbol={sym}")
        qv = None
        if isinstance(tdata, dict):
            try:
                qv = float(tdata.get("quoteVolume", 0))
            except (ValueError, TypeError):
                qv = None
        result = analyze_symbol(sym, quote_vol_24h=qv)
        if result:
            print(f"✅ {sym} 触发 ({result.alert_type}): {result}")
        else:
            print(f"❌ {sym} 未触发 (启动 + 持续 都不达标)")
            if qv:
                print(f"   参考: 24h_vol=${qv:,.0f} → avg_1m=${qv/1440:,.0f}")
        return 0
    new = run_scan()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
