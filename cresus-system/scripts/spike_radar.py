"""spike_radar.py — 妖币雷达

实时扫描 Binance 永续 + OKX SWAP 全部标的,找出 24h 涨幅/跌幅榜,
并标记每个标的的可交易性 (在 OKX SWAP 白名单 = bot 可自动开仓)。

输出: ~/cresus-bot/spike_radar.json (供 dashboard + bot 消费)

设计原则:
- 纯 stdlib,无依赖
- 多源对照 (Binance perp 数据 + OKX SWAP 白名单)
- 排除杠杆代币 / 稳定币 / 太低成交量噪音
- 支持手动 refresh + cron 5min 自动刷新
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_OUTPUT = Path.home() / "cresus-bot" / "spike_radar.json"

BINANCE_FAPI_24HR = "https://fapi.binance.com/fapi/v1/ticker/24hr"
OKX_SWAP_TICKERS  = "https://www.okx.com/api/v5/market/tickers?instType=SWAP"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) cresus-spike-radar"
HTTP_TIMEOUT = 15

# 过滤阈值
MIN_24H_CHANGE_PCT = 5.0          # 涨/跌幅绝对值 < 5% 不收录
MIN_24H_VOLUME_USDT = 1_000_000   # 24h 成交额 < 100 万 USDT 视为流动性不足
MAX_RESULTS = 60                  # 涨/跌总和最多保留 60 条 (前端再切)

# 排除杠杆代币 / 稳定币 / 衍生符号
EXCLUDE_PATTERNS = (
    "DOWN", "UP",   # 杠杆代币 BTCDOWN, ETHUP
    "BEAR", "BULL",
    "USDC", "BUSD", "TUSD", "DAI", "FDUSD",  # 稳定币对稳定币
    "_PERP", "_PRE",                          # 期货前缀
)

# 强度等级 (24h 涨跌幅绝对值)
INTENSITY_THRESHOLDS = [
    (30, 3, "🔥🔥🔥", "PARABOLIC"),  # 抛物线级别
    (15, 2, "🔥🔥",   "HOT"),          # 强势
    (7,  1, "🔥",      "WARM"),        # 温热
    (0,  0, "",         "MILD"),       # 轻度
]


# ============================================================================
# Data class
# ============================================================================

@dataclass
class MoverEntry:
    symbol: str                          # 显示符号 (如 ONDOUSDT)
    base: str                            # 基础币 (ONDO)
    price: float
    change_24h_pct: float
    volume_usdt_24h: float
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    okx_inst_id: Optional[str] = None    # OKX SWAP instId (None = 不可在 OKX 永续交易)
    binance_symbol: Optional[str] = None # Binance perp symbol
    tradable: str = "binance"            # "okx" / "binance" / "spot_only"
    intensity: int = 0                   # 0-3 火焰等级
    intensity_label: str = "MILD"
    fire: str = ""                       # 🔥🔥🔥 / 🔥🔥 / 🔥
    tags: List[str] = field(default_factory=list)


# ============================================================================
# HTTP helpers
# ============================================================================

def _http_get_json(url: str) -> Optional[object]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[radar] {url} failed: {e}", file=sys.stderr)
        return None


# ============================================================================
# Source fetchers
# ============================================================================

def fetch_binance_perp() -> List[dict]:
    data = _http_get_json(BINANCE_FAPI_24HR)
    if not isinstance(data, list):
        return []
    return data


def fetch_okx_swap_map() -> Dict[str, dict]:
    """Return {BASE_UPPER: ticker_dict} for all USDT-SWAP pairs."""
    resp = _http_get_json(OKX_SWAP_TICKERS)
    if not resp or not isinstance(resp, dict):
        return {}
    out = {}
    for t in resp.get("data", []):
        inst = t.get("instId", "")
        if not inst.endswith("-USDT-SWAP"):
            continue
        base = inst.replace("-USDT-SWAP", "").upper()
        out[base] = t
    return out


# ============================================================================
# Classification
# ============================================================================

def compute_intensity(change_24h: float) -> tuple:
    """Return (level int 0-3, fire emoji, label)."""
    abs_c = abs(change_24h)
    for thr, level, fire, label in INTENSITY_THRESHOLDS:
        if abs_c >= thr:
            return level, fire, label
    return 0, "", "MILD"


def is_excluded(symbol: str) -> bool:
    s = symbol.upper()
    if not s.endswith("USDT"):
        return True
    base = s[:-4]
    if not base or len(base) > 10:
        return True
    for pat in EXCLUDE_PATTERNS:
        if pat in s:
            return True
    return False


# ============================================================================
# Main builder
# ============================================================================

def build_movers() -> List[MoverEntry]:
    binance_data = fetch_binance_perp()
    okx_map = fetch_okx_swap_map()

    if not binance_data:
        print("[radar] Binance returned 0 entries — abort", file=sys.stderr)
        return []

    entries: List[MoverEntry] = []
    for t in binance_data:
        sym = t.get("symbol", "")
        if is_excluded(sym):
            continue
        try:
            change_24h = float(t.get("priceChangePercent", 0))
            price      = float(t.get("lastPrice", 0))
            vol_usdt   = float(t.get("quoteVolume", 0))
            high       = float(t.get("highPrice", 0)) or None
            low        = float(t.get("lowPrice", 0)) or None
        except (TypeError, ValueError):
            continue

        if abs(change_24h) < MIN_24H_CHANGE_PCT:
            continue
        if vol_usdt < MIN_24H_VOLUME_USDT:
            continue

        base = sym[:-4].upper()  # ONDO from ONDOUSDT
        okx_t = okx_map.get(base)

        tradable = "okx" if okx_t else "binance"
        level, fire, label = compute_intensity(change_24h)

        tags = []
        if change_24h > 0:
            tags.append("gainer")
        else:
            tags.append("loser")
        if vol_usdt >= 100_000_000:
            tags.append("high_volume")
        if vol_usdt >= 500_000_000:
            tags.append("mega_volume")
        if level >= 3:
            tags.append("parabolic")

        entries.append(MoverEntry(
            symbol=sym,
            base=base,
            price=price,
            change_24h_pct=round(change_24h, 2),
            volume_usdt_24h=round(vol_usdt, 0),
            high_24h=high,
            low_24h=low,
            okx_inst_id=okx_t.get("instId") if okx_t else None,
            binance_symbol=sym,
            tradable=tradable,
            intensity=level,
            intensity_label=label,
            fire=fire,
            tags=tags,
        ))

    # 按 24h 涨跌幅绝对值降序
    entries.sort(key=lambda e: abs(e.change_24h_pct), reverse=True)
    return entries[:MAX_RESULTS]


# ============================================================================
# Output
# ============================================================================

def write_output(entries: List[MoverEntry], path: Path) -> None:
    gainers = [e for e in entries if e.change_24h_pct > 0]
    losers  = [e for e in entries if e.change_24h_pct < 0]
    okx_ok  = [e for e in entries if e.tradable == "okx"]

    # Top 3 hero cards = 涨幅最猛的 3 个
    top_hero_gainers = sorted(gainers, key=lambda e: e.change_24h_pct, reverse=True)[:3]

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total_movers":     len(entries),
            "gainers_count":    len(gainers),
            "losers_count":     len(losers),
            "tradable_okx":     len(okx_ok),
            "tradable_binance": len(entries) - len(okx_ok),
        },
        "hero":   [asdict(e) for e in top_hero_gainers],
        "movers": [asdict(e) for e in entries],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ============================================================================
# CLI
# ============================================================================

def cmd_refresh(out_path: Path) -> int:
    entries = build_movers()
    if not entries:
        print("[radar] WARNING: 0 movers detected", file=sys.stderr)
        return 1
    write_output(entries, out_path)

    g = [e for e in entries if e.change_24h_pct > 0]
    l = [e for e in entries if e.change_24h_pct < 0]
    okx = [e for e in entries if e.tradable == "okx"]

    print(f"[radar] ✅ {len(entries)} movers → {out_path}")
    print(f"        Gainers={len(g)}  Losers={len(l)}  OKX-tradable={len(okx)}")
    print(f"\n📈 Top 8 gainers:")
    for e in sorted(g, key=lambda x: x.change_24h_pct, reverse=True)[:8]:
        mark = "✅" if e.tradable == "okx" else "🟡"
        vol_m = e.volume_usdt_24h / 1e6
        print(f"  {mark} {e.fire:6} {e.symbol:14} {e.change_24h_pct:+7.2f}%   ${e.price:<14.6g}  vol ${vol_m:7.1f}M")
    if l:
        print(f"\n📉 Top 5 losers:")
        for e in sorted(l, key=lambda x: x.change_24h_pct)[:5]:
            mark = "✅" if e.tradable == "okx" else "🟡"
            vol_m = e.volume_usdt_24h / 1e6
            print(f"  {mark} {e.fire:6} {e.symbol:14} {e.change_24h_pct:+7.2f}%   ${e.price:<14.6g}  vol ${vol_m:7.1f}M")
    return 0


def cmd_test() -> int:
    """Self-test classification logic."""
    cases = [
        (35.0,  3, "PARABOLIC"),
        (28.0,  2, "HOT"),
        (15.0,  2, "HOT"),
        (10.0,  1, "WARM"),
        (5.0,   0, "MILD"),
        (-32.0, 3, "PARABOLIC"),
        (-8.0,  1, "WARM"),
        (3.0,   0, "MILD"),
    ]
    fails = []
    for change, exp_level, exp_label in cases:
        level, fire, label = compute_intensity(change)
        ok = level == exp_level and label == exp_label
        marker = "✅" if ok else "❌"
        print(f"  {marker} {change:+7.2f}% → level={level} ({label}) {fire}")
        if not ok:
            fails.append((change, exp_level, exp_label, level, label))

    excl_cases = [
        ("BTCUSDT",     False),
        ("ETHUSDT",     False),
        ("BTCDOWNUSDT", True),    # 杠杆
        ("ETHUPUSDT",   True),
        ("USDCUSDT",    True),    # 稳定币
        ("FOOUSDC",     True),    # 不是 USDT 计价
        ("BTCBUSD",     True),
        ("THISTOOLONGTOKENUSDT", True),  # base len > 10
    ]
    print()
    for sym, exp in excl_cases:
        actual = is_excluded(sym)
        ok = actual == exp
        marker = "✅" if ok else "❌"
        print(f"  {marker} is_excluded({sym!r}) = {actual} (expected {exp})")
        if not ok:
            fails.append((sym, exp, actual))

    if fails:
        print(f"\n❌ {len(fails)} cases failed")
        return 1
    print(f"\n✅ All cases pass")
    return 0


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="妖币雷达 — Binance perp + OKX SWAP 联合扫描")
    p.add_argument("cmd", choices=["refresh", "test"])
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args(argv)

    if args.cmd == "refresh":
        return cmd_refresh(args.out)
    if args.cmd == "test":
        return cmd_test()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
