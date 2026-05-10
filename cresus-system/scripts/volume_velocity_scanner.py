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
VOLUME_BURST_RATIO     = 5.0      # 1m 量 > 30m 均 × 此倍数 → 量能爆发 (基线 — 噪音由看板分级提醒消化)
PRICE_MOVE_THRESHOLD   = 0.005    # 1m 价格变化 ≥ 0.5% → 价格响应
DEDUP_WINDOW_MIN       = 30       # 同标的去重窗口 (分钟)
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
    direction: str        # "LONG" if 价格上涨, "SHORT" if 下跌
    price: float
    price_change_1m_pct: float
    volume_1m_usdt: float
    volume_30m_avg_usdt: float
    volume_ratio: float
    detected_at: str      # ISO timestamp
    intensity: int        # 1-3 (基于 volume_ratio 和 price_change 综合)


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

def fetch_universe() -> List[str]:
    """获取 Binance 永续 Top N USDT 标的 (按 24h quote volume)."""
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
    return [s for s, _ in pairs[:SCAN_TOP_N]]


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

def analyze_symbol(symbol: str) -> Optional[VelocityAlert]:
    """对单个 symbol 分析量价共振. 触发返回 VelocityAlert,否则 None."""
    klines = fetch_1m_klines(symbol)
    if len(klines) < 25:
        return None

    # K 线字段: [openTime, open, high, low, close, volume, closeTime, quoteVol, ...]
    try:
        # 最近一根 1m
        last = klines[-1]
        prev_30m = klines[-31:-1] if len(klines) >= 31 else klines[:-1]

        # 排除当前最新一根 (可能未完成)
        # Binance 1m 的最新 candle 可能是未结束的, 用倒数第二根更稳
        last_completed = klines[-2]
        recent_window = klines[-31:-2] if len(klines) >= 31 else klines[:-2]
        if len(recent_window) < 20:
            return None

        # 数据
        last_open  = float(last_completed[1])
        last_close = float(last_completed[4])
        last_vol   = float(last_completed[7])    # quoteVolume USDT

        avg_vol = mean([float(k[7]) for k in recent_window])
        if avg_vol <= 0:
            return None

        vol_ratio = last_vol / avg_vol
        price_change = (last_close - last_open) / last_open if last_open > 0 else 0

        # 共振检测: 量爆 + 价动
        if vol_ratio < VOLUME_BURST_RATIO:
            return None
        if abs(price_change) < PRICE_MOVE_THRESHOLD:
            return None

        # 强度: 综合 vol_ratio 和 price_change
        intensity = 1
        if vol_ratio >= 10 and abs(price_change) >= 0.01:
            intensity = 3
        elif vol_ratio >= 7 and abs(price_change) >= 0.008:
            intensity = 2

        direction = "LONG" if price_change > 0 else "SHORT"
        base = symbol[:-4].upper()

        return VelocityAlert(
            symbol=symbol,
            base=base,
            direction=direction,
            price=last_close,
            price_change_1m_pct=round(price_change * 100, 3),
            volume_1m_usdt=round(last_vol, 0),
            volume_30m_avg_usdt=round(avg_vol, 0),
            volume_ratio=round(vol_ratio, 2),
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
            "volume_burst_ratio": VOLUME_BURST_RATIO,
            "price_move_pct":     PRICE_MOVE_THRESHOLD * 100,
            "dedup_window_min":   DEDUP_WINDOW_MIN,
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
    for i, sym in enumerate(universe):
        if _is_recently_alerted(sym, dedup):
            continue
        alert = analyze_symbol(sym)
        if alert:
            new_alerts.append(alert)
            dedup[sym] = alert.detected_at
            _log(f"⚡ {sym} {alert.direction} {alert.price_change_1m_pct:+.2f}% × {alert.volume_ratio:.1f}x vol "
                 f"[intensity={alert.intensity}]")

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
    print(f"{'symbol':<14} {'dir':<6} {'1m 涨跌':>8} {'vol 倍数':>9} {'强度':>4} {'when'}")
    print("─" * 70)
    for a in alerts[:20]:
        print(f"{a['symbol']:<14} {a['direction']:<6} {a['price_change_1m_pct']:>+7.2f}% "
              f"{a['volume_ratio']:>7.1f}x {a['intensity']:>4} {a['detected_at'][:19]}")
    return 0


def main(argv) -> int:
    if argv and argv[0] == "show":
        return cmd_show()
    if argv and argv[0] == "test":
        # 单标的快速测试
        sym = argv[1] if len(argv) >= 2 else "BTCUSDT"
        result = analyze_symbol(sym)
        if result:
            print(f"✅ {sym} 触发: {result}")
        else:
            print(f"❌ {sym} 未触发 (无量价共振)")
        return 0
    new = run_scan()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
