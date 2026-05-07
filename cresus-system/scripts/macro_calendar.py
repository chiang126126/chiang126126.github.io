"""macro_calendar.py — Crésus 宏观事件日历 + 黑名单决策器

目的:
  在 FOMC / CPI / NFP 等高波动事件前后阻止开仓，避免被瞬时波动止损扫掉。

架构:
  - MacroSource (ABC): 数据源抽象，可插拔
  - ForexFactoryWeekSource: 本周/下周经济日历，免费 JSON feed
  - classify_tier(): 事件分级 CORE / OBSERVE / INFO
  - compute_blackout(): 给定当前时间 + 事件列表，返回当前黑名单状态
  - get_blackout_decision(): bot signal_router 直接调用入口

文件输出:
  ~/cresus-bot/macro_events.json  — 事件列表（含分级），由 cron 每小时刷新

CLI:
  python3 macro_calendar.py refresh    # 拉新数据
  python3 macro_calendar.py show       # 显示当前黑名单状态
  python3 macro_calendar.py test       # 自检
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# ============================================================================
# Configuration
# ============================================================================

WINDOWS = {
    "CORE":    {"before_min": 60, "after_min": 120},
    "OBSERVE": {"before_min": 30, "after_min": 60},
}

# Hard CORE triggers (uppercase substring match)
CORE_KEYWORDS = (
    "FOMC",
    "FED FUNDS RATE",
    "FEDERAL FUNDS RATE",
    "POWELL",
    "CPI",
    "CORE CPI",
    "PCE",
    "PPI",
    "NON-FARM",
    "NONFARM",
    "NFP",
    "GDP",
    "UNEMPLOYMENT RATE",
    "ECB MAIN REFINANCING",
    "ECB RATE DECISION",
    "ECB PRESS CONFERENCE",
    "BOE INTEREST RATE",
    "BOJ POLICY RATE",
    "BOJ INTEREST RATE",
)

# OBSERVE-eligible keywords for Medium impact promotion
OBSERVE_KEYWORDS = (
    "RETAIL SALES",
    "MANUFACTURING PMI",
    "SERVICES PMI",
    "ISM",
    "ECB",
    "FED ",
    "LAGARDE",
    "JOBLESS CLAIMS",
    "CONSUMER CONFIDENCE",
    "JACKSON HOLE",
    "BEIGE BOOK",
)

# ForexFactory uses currency codes; we filter to G7 + China
RELEVANT_COUNTRIES = {"USD", "EUR", "GBP", "JPY", "CNY", "CHF", "AUD", "CAD"}

DEFAULT_EVENTS_PATH = Path.home() / "cresus-bot" / "macro_events.json"

# ============================================================================
# Data classes
# ============================================================================

@dataclass
class MacroEvent:
    title: str
    country: str
    ts: str          # ISO 8601 with TZ
    impact: str      # raw: High / Medium / Low / Holiday
    tier: str        # CORE / OBSERVE / INFO
    forecast: Optional[str] = None
    previous: Optional[str] = None


@dataclass
class BlackoutState:
    tier: str            # CORE | OBSERVE
    title: str
    country: str
    event_ts: str
    minutes_to_event: int   # negative = past
    minutes_until_clear: int
    reason: str


# ============================================================================
# Source abstraction
# ============================================================================

class MacroSource(ABC):
    name: str = "abstract"

    @abstractmethod
    def fetch(self) -> List[dict]:
        """Return list of normalized raw event dicts:
        {title, country, ts (ISO with TZ), impact, forecast, previous}
        """
        ...


class ForexFactoryWeekSource(MacroSource):
    """ForexFactory 经济日历 JSON feed (faireconomy.media).
    免费、无 API key、覆盖主要货币事件、内置 High/Medium/Low impact 评级。
    """
    name = "forexfactory"
    URLS = (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    )
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 cresus-bot"

    def fetch(self) -> List[dict]:
        events: List[dict] = []
        seen = set()
        for url in self.URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.UA})
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = json.loads(r.read().decode())
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
                print(f"[macro_calendar] {url} failed: {e}", file=sys.stderr)
                continue

            if not isinstance(raw, list):
                print(f"[macro_calendar] {url}: unexpected response shape", file=sys.stderr)
                continue

            for item in raw:
                try:
                    norm = self._normalize(item)
                except Exception:
                    continue
                # de-dup across this/next week boundary
                key = (norm["title"], norm["country"], norm["ts"])
                if key in seen:
                    continue
                seen.add(key)
                events.append(norm)
        return events

    @staticmethod
    def _normalize(raw: dict) -> dict:
        # ForexFactory schema: {title, country, date (ISO with -04:00), impact, forecast, previous, url}
        if not raw.get("title") or not raw.get("date"):
            raise ValueError("missing title/date")
        return {
            "title": raw["title"].strip(),
            "country": (raw.get("country") or "").strip().upper(),
            "ts": raw["date"],
            "impact": (raw.get("impact") or "Low").strip(),
            "forecast": (raw.get("forecast") or "").strip() or None,
            "previous": (raw.get("previous") or "").strip() or None,
        }


# ============================================================================
# Classification
# ============================================================================

def classify_tier(title: str, impact: str) -> str:
    """事件分级 → CORE / OBSERVE / INFO."""
    t = (title or "").upper()
    imp = (impact or "").strip()

    # 核心硬触发
    for kw in CORE_KEYWORDS:
        if kw in t:
            return "CORE"

    # High impact 默认 OBSERVE，但 Press Conference / Rate Decision 提级 CORE
    if imp == "High":
        if "PRESS CONFERENCE" in t or "RATE DECISION" in t or "INTEREST RATE" in t:
            return "CORE"
        return "OBSERVE"

    # Medium 仅在白名单时升级为 OBSERVE
    if imp == "Medium":
        for kw in OBSERVE_KEYWORDS:
            if kw in t:
                return "OBSERVE"
        return "INFO"

    return "INFO"


# ============================================================================
# Event refresh (called by cron / launchd)
# ============================================================================

def build_events(source: MacroSource) -> List[MacroEvent]:
    raw_events = source.fetch()
    events: List[MacroEvent] = []
    for raw in raw_events:
        country = raw.get("country", "")
        if RELEVANT_COUNTRIES and country and country not in RELEVANT_COUNTRIES:
            continue
        title = raw["title"]
        impact = raw.get("impact", "Low")
        tier = classify_tier(title, impact)
        events.append(MacroEvent(
            title=title,
            country=country,
            ts=raw["ts"],
            impact=impact,
            tier=tier,
            forecast=raw.get("forecast"),
            previous=raw.get("previous"),
        ))
    events.sort(key=lambda e: e.ts)
    return events


def write_events_file(path: Path, events: List[MacroEvent], source_name: str) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": source_name,
        "tier_counts": {
            "CORE":    sum(1 for e in events if e.tier == "CORE"),
            "OBSERVE": sum(1 for e in events if e.tier == "OBSERVE"),
            "INFO":    sum(1 for e in events if e.tier == "INFO"),
        },
        "events": [asdict(e) for e in events],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ============================================================================
# Blackout calculation
# ============================================================================

def parse_iso(ts: str) -> datetime:
    """支持 ForexFactory '2026-05-08T14:00:00-04:00' / 'Z' / 无tz 三种格式."""
    if not ts:
        raise ValueError("empty ts")
    s = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # 假定 UTC（兜底，正常 ForexFactory 都带 tz）
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_blackout(events: List[MacroEvent], now: datetime) -> Optional[BlackoutState]:
    """返回当前最严格的活跃黑名单状态，没有则 None."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    candidates = []
    for ev in events:
        win = WINDOWS.get(ev.tier)
        if not win:
            continue
        try:
            ev_ts = parse_iso(ev.ts)
        except Exception:
            continue
        win_start = ev_ts - timedelta(minutes=win["before_min"])
        win_end   = ev_ts + timedelta(minutes=win["after_min"])
        if not (win_start <= now <= win_end):
            continue
        mins_to    = int((ev_ts - now).total_seconds() // 60)
        mins_left  = int((win_end - now).total_seconds() // 60)
        priority   = {"CORE": 0, "OBSERVE": 1}[ev.tier]
        candidates.append((priority, abs(mins_to), ev, mins_to, mins_left))

    if not candidates:
        return None

    # 优先级最高 → 距事件最近
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, ev, mins_to, mins_left = candidates[0]
    return BlackoutState(
        tier=ev.tier,
        title=ev.title,
        country=ev.country,
        event_ts=ev.ts,
        minutes_to_event=mins_to,
        minutes_until_clear=mins_left,
        reason=_format_reason(ev.tier, ev.title, mins_to, mins_left),
    )


def _format_reason(tier: str, title: str, mins_to: int, mins_left: int) -> str:
    when = "事件前" if mins_to > 0 else "事件后"
    abs_to = abs(mins_to)
    label = "核心事件" if tier == "CORE" else "观察事件"
    return f"{label}[{title}]: {when} {abs_to}min, 窗口剩余 {mins_left}min"


# ============================================================================
# Public bot API
# ============================================================================

def load_events(events_path: Path = DEFAULT_EVENTS_PATH) -> List[MacroEvent]:
    if not events_path.exists():
        return []
    try:
        data = json.loads(events_path.read_text(encoding="utf-8"))
        return [MacroEvent(**e) for e in data.get("events", [])]
    except Exception as e:
        print(f"[macro_calendar] load_events: {e}", file=sys.stderr)
        return []


def get_blackout_decision(
    events_path: Path = DEFAULT_EVENTS_PATH,
    now: Optional[datetime] = None,
) -> dict:
    """供 signal_router 调用. 返回:
        {
          "blocked":           bool,            # True → 阻止开仓
          "tier":              str | None,      # CORE / OBSERVE / None
          "threshold_bonus":   int,              # +10 for OBSERVE, 0 otherwise
          "reason":            str | None,
        }
    """
    if now is None:
        now = datetime.now(timezone.utc)
    events = load_events(events_path)
    state = compute_blackout(events, now)
    if not state:
        return {"blocked": False, "tier": None, "threshold_bonus": 0, "reason": None}
    return {
        "blocked": state.tier == "CORE",
        "tier": state.tier,
        "threshold_bonus": 10 if state.tier == "OBSERVE" else 0,
        "reason": state.reason,
    }


# ============================================================================
# CLI
# ============================================================================

def cmd_refresh(events_path: Path) -> int:
    src = ForexFactoryWeekSource()
    events = build_events(src)
    if not events:
        print("[macro_calendar] WARNING: 0 events fetched (network issue?)", file=sys.stderr)
        return 1
    write_events_file(events_path, events, source_name=src.name)
    counts = {tier: sum(1 for e in events if e.tier == tier) for tier in ("CORE", "OBSERVE", "INFO")}
    print(f"[macro_calendar] ✅ {len(events)} events → {events_path}")
    print(f"[macro_calendar]    CORE={counts['CORE']}  OBSERVE={counts['OBSERVE']}  INFO={counts['INFO']}")
    return 0


def cmd_show(events_path: Path) -> int:
    decision = get_blackout_decision(events_path)
    events = load_events(events_path)
    now = datetime.now(timezone.utc)
    print(f"=== Macro Blackout · {now.isoformat()} ===")
    print(f"Loaded {len(events)} events from {events_path}")
    if decision["blocked"]:
        print(f"\n🚫 BLOCKED — tier={decision['tier']}")
        print(f"   reason: {decision['reason']}")
    elif decision["tier"]:
        print(f"\n⚠️  OBSERVE — threshold +{decision['threshold_bonus']}")
        print(f"   reason: {decision['reason']}")
    else:
        print(f"\n✅ Clear — no active blackout")

    upcoming = sorted(
        (e for e in events if parse_iso(e.ts) > now and e.tier in ("CORE", "OBSERVE")),
        key=lambda e: e.ts,
    )[:5]
    if upcoming:
        print(f"\nNext {len(upcoming)} CORE/OBSERVE events:")
        for e in upcoming:
            ts = parse_iso(e.ts).astimezone()
            mins = int((parse_iso(e.ts) - now).total_seconds() // 60)
            h, m = divmod(mins, 60)
            print(f"  [{e.tier:7}] {ts.strftime('%m-%d %H:%M %Z'):24} ({h}h{m:02d}m later) {e.country} {e.title}")
    return 0


def cmd_test() -> int:
    """Self-test classification rules."""
    cases = [
        ("FOMC Statement",                "High",   "CORE"),
        ("FOMC Meeting Minutes",          "High",   "CORE"),
        ("Fed Chair Powell Speech",       "High",   "CORE"),
        ("CPI m/m",                       "High",   "CORE"),
        ("Core CPI y/y",                  "High",   "CORE"),
        ("Non-Farm Employment Change",    "High",   "CORE"),
        ("Unemployment Rate",             "High",   "CORE"),
        ("ECB Press Conference",          "High",   "CORE"),
        ("ECB Main Refinancing Rate",     "High",   "CORE"),
        ("BOE Interest Rate Decision",    "High",   "CORE"),
        ("Retail Sales m/m",              "High",   "OBSERVE"),
        ("ISM Manufacturing PMI",         "High",   "OBSERVE"),
        ("Trade Balance",                 "Medium", "INFO"),
        ("ECB de Guindos Speech",         "Medium", "OBSERVE"),  # ECB substring
        ("Existing Home Sales",           "Medium", "INFO"),
        ("Some Random Indicator",         "Low",    "INFO"),
        ("Bank Holiday",                  "Holiday","INFO"),
    ]
    fails = []
    for title, impact, expected in cases:
        actual = classify_tier(title, impact)
        ok = actual == expected
        marker = "✅" if ok else "❌"
        print(f"  {marker} {title!r:40} {impact:10} → {actual:8} (expected {expected})")
        if not ok:
            fails.append((title, impact, expected, actual))
    if fails:
        print(f"\n❌ {len(fails)}/{len(cases)} failed")
        return 1
    print(f"\n✅ All {len(cases)} classification cases pass")

    # Blackout window test
    print("\n--- Blackout window test ---")
    now = datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)
    fake_events = [
        MacroEvent("FOMC", "USD", "2026-05-08T18:30:00+00:00", "High", "CORE"),
        MacroEvent("Trade Balance", "USD", "2026-05-08T20:00:00+00:00", "Medium", "INFO"),
    ]
    state = compute_blackout(fake_events, now)
    assert state is not None and state.tier == "CORE", f"Expected CORE blackout, got {state}"
    assert state.minutes_to_event == 30, f"Expected 30 min, got {state.minutes_to_event}"
    print(f"  ✅ CORE blackout 30min before FOMC: {state.reason}")

    state = compute_blackout(fake_events, now + timedelta(hours=3))
    assert state is None, f"Expected no blackout 3h after FOMC, got {state}"
    print(f"  ✅ Clear 3h after FOMC")
    return 0


def main(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Crésus macro event calendar")
    p.add_argument("cmd", choices=["refresh", "show", "test"])
    p.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH,
                   help=f"events JSON path (default: {DEFAULT_EVENTS_PATH})")
    args = p.parse_args(argv)

    if args.cmd == "refresh":
        return cmd_refresh(args.events)
    if args.cmd == "show":
        return cmd_show(args.events)
    if args.cmd == "test":
        return cmd_test()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
