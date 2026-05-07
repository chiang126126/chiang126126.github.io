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

import dataclasses
import json
import re
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
# 中文翻译表 — 直译优先，关键词模板兜底
# ============================================================================

# 全名匹配（substring 不区分大小写更宽松，但会有歧义；这里用精确匹配 + 关键词模板组合）
EVENT_TRANSLATIONS = {
    # ===== Fed (USD) =====
    "FOMC Statement":                       "FOMC 利率决议声明",
    "FOMC Press Conference":                "FOMC 新闻发布会",
    "FOMC Meeting Minutes":                 "FOMC 会议纪要",
    "FOMC Economic Projections":            "FOMC 经济预测",
    "Federal Funds Rate":                   "联邦基金利率",
    "Fed Monetary Policy Report":           "美联储货币政策报告",
    "Beige Book":                           "美联储褐皮书",
    "Jackson Hole Symposium":               "Jackson Hole 央行年会",
    "JOLTS Job Openings":                   "JOLTS 职位空缺",

    # ===== Inflation (USD) =====
    "CPI m/m":                              "CPI 月率",
    "CPI y/y":                              "CPI 年率",
    "Core CPI m/m":                         "核心 CPI 月率",
    "Core CPI y/y":                         "核心 CPI 年率",
    "PPI m/m":                              "PPI 月率",
    "PPI y/y":                              "PPI 年率",
    "Core PPI m/m":                         "核心 PPI 月率",
    "PCE Price Index m/m":                  "PCE 物价指数月率",
    "PCE Price Index y/y":                  "PCE 物价指数年率",
    "Core PCE Price Index m/m":             "核心 PCE 物价指数月率",
    "Core PCE Price Index y/y":             "核心 PCE 物价指数年率",

    # ===== Jobs (USD) =====
    "Non-Farm Employment Change":           "非农就业人数变化",
    "Non-Farm Payrolls":                    "非农就业人口",
    "ADP Non-Farm Employment Change":       "ADP 私营部门就业",
    "Unemployment Rate":                    "失业率",
    "Average Hourly Earnings m/m":          "平均时薪月率",
    "Unemployment Claims":                  "首次申请失业救济金人数",
    "Initial Jobless Claims":               "首次申请失业救济金人数",
    "Continuing Jobless Claims":            "续请失业救济金人数",
    "Challenger Job Cuts y/y":              "Challenger 裁员年率",

    # ===== Growth (USD) =====
    "GDP m/m":                              "GDP 月率",
    "GDP q/q":                              "GDP 季率",
    "Advance GDP q/q":                      "GDP 初值季率",
    "Prelim GDP q/q":                       "GDP 修正值季率",
    "Final GDP q/q":                        "GDP 终值季率",
    "Industrial Production m/m":            "工业产出月率",
    "Capacity Utilization Rate":            "产能利用率",

    # ===== US Survey/PMI =====
    "ISM Manufacturing PMI":                "ISM 制造业 PMI",
    "ISM Services PMI":                     "ISM 服务业 PMI",
    "ISM Non-Manufacturing PMI":            "ISM 非制造业 PMI",
    "Manufacturing PMI":                    "制造业 PMI",
    "Services PMI":                         "服务业 PMI",
    "Final Manufacturing PMI":              "制造业 PMI 终值",
    "Final Services PMI":                   "服务业 PMI 终值",
    "Flash Manufacturing PMI":              "制造业 PMI 初值",
    "Flash Services PMI":                   "服务业 PMI 初值",
    "Empire State Manufacturing Index":     "纽约联储制造业指数",
    "Philly Fed Manufacturing Index":       "费城联储制造业指数",
    "Richmond Manufacturing Index":         "里士满联储制造业指数",
    "Chicago PMI":                          "芝加哥 PMI",
    "Consumer Confidence":                  "消费者信心指数",
    "Prelim UoM Consumer Sentiment":        "密大消费者信心指数初值",
    "Revised UoM Consumer Sentiment":       "密大消费者信心指数终值",
    "CB Consumer Confidence":               "经济咨商局消费者信心",

    # ===== US Spending =====
    "Retail Sales m/m":                     "零售销售月率",
    "Core Retail Sales m/m":                "核心零售销售月率",
    "Personal Spending m/m":                "个人消费支出月率",
    "Personal Income m/m":                  "个人收入月率",
    "Durable Goods Orders m/m":             "耐用品订单月率",
    "Core Durable Goods Orders m/m":        "核心耐用品订单月率",
    "Factory Orders m/m":                   "工厂订单月率",

    # ===== Housing =====
    "Existing Home Sales":                  "成屋销售",
    "Pending Home Sales m/m":               "成屋签约销售月率",
    "New Home Sales":                       "新屋销售",
    "Building Permits":                     "营建许可",
    "Housing Starts":                       "新屋开工",
    "S&P/CS Composite-20 HPI y/y":          "标普/凯斯-席勒 20 城房价指数",

    # ===== Trade & Foreign =====
    "Trade Balance":                        "贸易差额",
    "Balance of Trade":                     "贸易差额",
    "Goods Trade Balance":                  "商品贸易差额",
    "Current Account":                      "经常账户",

    # ===== ECB (EUR) =====
    "ECB Press Conference":                 "欧央行新闻发布会",
    "Main Refinancing Rate":                "欧央行再融资利率",
    "ECB Main Refinancing Rate":            "欧央行再融资利率",
    "ECB Monetary Policy Statement":        "欧央行货币政策声明",
    "ECB Economic Bulletin":                "欧央行经济公报",
    "ECB Financial Stability Review":       "欧央行金融稳定评估",

    # ===== EU/EZ data =====
    "Spanish Flash CPI y/y":                "西班牙 CPI 初值年率",
    "German Prelim CPI m/m":                "德国 CPI 初值月率",
    "French Flash CPI m/m":                 "法国 CPI 初值月率",
    "Italian Prelim CPI m/m":               "意大利 CPI 初值月率",
    "EU Flash CPI y/y":                     "欧元区 CPI 初值年率",
    "Core CPI Flash Estimate y/y":          "欧元区核心 CPI 初值年率",
    "German ZEW Economic Sentiment":        "德国 ZEW 经济景气指数",
    "ZEW Economic Sentiment":               "欧元区 ZEW 经济景气",
    "German Ifo Business Climate":          "德国 IFO 商业景气",
    "EU Industrial Production m/m":         "欧元区工业产出月率",

    # ===== BOE (GBP) =====
    "BOE Inflation Report":                 "英央行通胀报告",
    "Official Bank Rate":                   "英央行基准利率",
    "BOE Monetary Policy Summary":          "英央行货币政策纪要",
    "Asset Purchase Facility":              "英央行资产购买规模",
    "MPC Official Bank Rate Votes":         "英央行 MPC 利率决议投票",
    "MPC Asset Purchase Facility Votes":    "英央行 MPC 量化宽松投票",

    # ===== UK data =====
    "UK CPI y/y":                           "英国 CPI 年率",
    "UK GDP m/m":                           "英国 GDP 月率",
    "UK Retail Sales m/m":                  "英国零售销售月率",
    "Claimant Count Change":                "英国失业救济金申请人数",

    # ===== BOJ (JPY) =====
    "BOJ Outlook Report":                   "日央行展望报告",
    "BOJ Policy Rate":                      "日央行政策利率",
    "BOJ Monetary Policy Statement":        "日央行货币政策声明",
    "BOJ Press Conference":                 "日央行新闻发布会",
    "Monetary Policy Meeting Minutes":      "日央行政策会议纪要",
    "Tankan Manufacturing Index":           "日本短观制造业指数",
    "Tankan Non-Manufacturing Index":       "日本短观非制造业指数",

    # ===== China (CNY) =====
    "CB Leading Index m/m":                 "经济咨商局领先指数月率",
    "CNY GDP q/y":                          "中国 GDP 年率",
    "Caixin Manufacturing PMI":             "财新制造业 PMI",
    "Caixin Services PMI":                  "财新服务业 PMI",

    # ===== AUD/CAD/CHF（轻覆盖） =====
    "Cash Rate":                            "央行基准利率",
    "RBA Rate Statement":                   "澳联储利率声明",
    "BOC Rate Statement":                   "加央行利率声明",
    "Overnight Rate":                       "加央行隔夜利率",
    "SNB Policy Rate":                      "瑞央行政策利率",
    "Employment Change":                    "就业人数变化",
}

# 政策制定者姓名翻译
NAME_TRANSLATIONS = {
    # Fed
    "Powell":     "鲍威尔",
    "Williams":   "威廉姆斯",
    "Cook":       "库克",
    "Bowman":     "鲍曼",
    "Goolsbee":   "古尔斯比",
    "Waller":     "沃勒",
    "Jefferson":  "杰斐逊",
    "Bostic":     "博斯蒂克",
    "Daly":       "戴利",
    "Mester":     "梅斯特",
    "Bullard":    "布拉德",
    "Logan":      "洛根",
    "Schmid":     "施密德",
    "Kashkari":   "卡什卡利",
    "Kugler":     "库格勒",
    "Barr":       "巴尔",
    "Barkin":     "巴尔金",
    "Harker":     "哈克",
    "Collins":    "柯林斯",
    "Musalem":    "穆萨利姆",
    "Hammack":    "哈马克",
    # ECB
    "Lagarde":    "拉加德",
    "Schnabel":   "施纳贝尔",
    "Lane":       "莱恩",
    "Cipollone":  "奇波洛内",
    "Elderson":   "埃尔德森",
    "Panetta":    "帕内塔",
    "Guindos":    "德金多斯",
    # BOE
    "Bailey":     "贝利",
    "Pill":       "皮尔",
    "Ramsden":    "拉姆斯登",
    "Mann":       "曼恩",
    "Dhingra":    "丁格拉",
    # BOJ
    "Ueda":       "植田",
    "Uchida":     "内田",
    "Himino":     "冰见野",
    "Adachi":     "足达",
}

def _translate_name(en: str) -> str:
    return NAME_TRANSLATIONS.get(en, en)


# 模板翻译: 优先级匹配。每个 (regex, template_fn) 组合
PATTERN_TRANSLATIONS = [
    # FOMC Member XXX Speaks / Speech
    (re.compile(r"^FOMC Member ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"FOMC 委员 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^Fed Chair ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"美联储主席 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^Fed Vice Chair ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"美联储副主席 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^Fed (\w+) Speaks?$", re.I),
        lambda m: f"美联储 {_translate_name(m.group(1).strip())} 讲话"),

    # ECB
    (re.compile(r"^ECB President ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"欧央行行长 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^ECB ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"欧央行 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^ECB de Guindos Speaks?$", re.I),
        lambda m: "欧央行副行长德金多斯讲话"),

    # BOE
    (re.compile(r"^BOE Gov ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"英央行行长 {_translate_name(m.group(1).strip())} 讲话"),
    (re.compile(r"^BOE Member ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"英央行 MPC 委员 {_translate_name(m.group(1).strip())} 讲话"),

    # BOJ
    (re.compile(r"^BOJ Gov ([\w\s\-']+?) Speaks?$", re.I),
        lambda m: f"日央行行长 {_translate_name(m.group(1).strip())} 讲话"),

    # 通用 m/m, q/q, y/y 数据：未匹配的兜底（最低优先级）
    # 例如 "Foo Index m/m" → "Foo Index 月率"
    (re.compile(r"^(.+?)\s+m/m$", re.I), lambda m: f"{m.group(1)} 月率"),
    (re.compile(r"^(.+?)\s+q/q$", re.I), lambda m: f"{m.group(1)} 季率"),
    (re.compile(r"^(.+?)\s+y/y$", re.I), lambda m: f"{m.group(1)} 年率"),
]


def translate_title(title: str) -> Optional[str]:
    """Return Chinese translation, or None if no translation available."""
    if not title:
        return None
    t = title.strip()

    # 1. Exact full-name match
    if t in EVENT_TRANSLATIONS:
        return EVENT_TRANSLATIONS[t]

    # 2. Pattern templates
    for pat, fn in PATTERN_TRANSLATIONS:
        m = pat.match(t)
        if m:
            try:
                return fn(m)
            except Exception:
                continue

    return None  # No translation, dashboard falls back to English

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
    title_zh: Optional[str] = None    # Chinese name (None = no translation)


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
            title_zh=translate_title(title),
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
    display_title = ev.title_zh or ev.title
    return BlackoutState(
        tier=ev.tier,
        title=ev.title,
        country=ev.country,
        event_ts=ev.ts,
        minutes_to_event=mins_to,
        minutes_until_clear=mins_left,
        reason=_format_reason(ev.tier, display_title, mins_to, mins_left),
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
        valid_keys = {f.name for f in dataclasses.fields(MacroEvent)}
        events = []
        for e in data.get("events", []):
            filtered = {k: v for k, v in e.items() if k in valid_keys}
            events.append(MacroEvent(**filtered))
        return events
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
            zh = f" / {e.title_zh}" if e.title_zh else ""
            print(f"  [{e.tier:7}] {ts.strftime('%m-%d %H:%M %Z'):24} ({h}h{m:02d}m later) {e.country} {e.title}{zh}")
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

    # Translation tests
    print("\n--- Translation test ---")
    trans_cases = [
        ("FOMC Statement",                "FOMC 利率决议声明"),
        ("FOMC Meeting Minutes",          "FOMC 会议纪要"),
        ("FOMC Member Williams Speaks",   "FOMC 委员 威廉姆斯 讲话"),
        ("Fed Chair Powell Speaks",       "美联储主席 鲍威尔 讲话"),
        ("ECB President Lagarde Speaks",  "欧央行行长 拉加德 讲话"),
        ("BOE Gov Bailey Speaks",         "英央行行长 贝利 讲话"),
        ("CPI m/m",                       "CPI 月率"),
        ("Core CPI y/y",                  "核心 CPI 年率"),
        ("Non-Farm Employment Change",    "非农就业人数变化"),
        ("Unemployment Rate",             "失业率"),
        ("ECB Press Conference",          "欧央行新闻发布会"),
        ("Retail Sales m/m",              "零售销售月率"),
        ("Trade Balance",                 "贸易差额"),
        ("Balance of Trade",              "贸易差额"),
        ("Some Random Indicator m/m",     "Some Random Indicator 月率"),  # 兜底通用模板
        ("Totally Unknown Event",         None),                          # 无翻译，返 None
    ]
    trans_fails = []
    for src, expected in trans_cases:
        actual = translate_title(src)
        ok = actual == expected
        marker = "✅" if ok else "❌"
        print(f"  {marker} {src!r:42} → {actual!r}")
        if not ok:
            trans_fails.append((src, expected, actual))
    if trans_fails:
        print(f"\n❌ {len(trans_fails)}/{len(trans_cases)} translation cases failed")
        for src, exp, act in trans_fails:
            print(f"    {src!r}: expected {exp!r}, got {act!r}")
        return 1
    print(f"\n✅ All {len(trans_cases)} translation cases pass")
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
