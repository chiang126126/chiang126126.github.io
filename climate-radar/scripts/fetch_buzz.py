#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_buzz.py — 社媒 & 新闻舆情采集（best-effort 数据管道）

来源：
  • Reddit 公共 JSON 搜索（无需 key，低频、带 User-Agent；受限流影响）
  • Google News RSS（无需 key，稳定；法国本地新闻 + 缺货/抢购话题）
  • X / Instagram：无 keyless 接口（X 关闭免费 API、IG 反爬登录墙），
    默认关闭。如自备 API/token，可在 X_BEARER / 未来扩展点接入。

产物：climate-radar/data/buzz.json —— 按主题（heat/shortage/ac_rush/
office_heat/buying_need）归类的舆情条目 + 计数，前端只读该快照。

设计原则：逐源 try/except、任何失败保留旧值、绝不非零退出、确定性输出
（sort_keys + trailing newline，便于"仅变化才提交"）。
免责：仅抓取公开可无障碍获取的内容，尊重各平台 robots/ToS；best-effort，
不构成保证。
"""
import json
import os
import re
import sys
import time
import html
from datetime import datetime, timezone
from urllib.parse import quote_plus

try:
    import requests
except Exception:
    requests = None

import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.normpath(os.path.join(HERE, "..", "data", "buzz.json"))

UA = "climate-radar/1.0 (opportunity radar; contact via repo)"
HTTP_TIMEOUT = 12.0
MAX_ITEMS = 48  # 快照最多保留的条目数

# ---- 主题关键词（法/英），优先级从高到低分类 ----
THEME_RULES = [
    ("shortage", re.compile(
        r"rupture|en rupture|plus de stock|épuis|pénurie|indisponible|"
        r"sold\s?out|out of stock|no stock|stock épuisé", re.I)),
    ("ac_rush", re.compile(
        r"ru[ée]e|s'arrachent|arrache|climatiseur|ventilateur.*(vend|achet|arrach)|"
        r"snap(ped|ping) up|fans? (sell|sold) out|buy(ing)? up (fans|ac)", re.I)),
    ("buying_need", re.compile(
        r"où acheter|je cherche|recommand|quel climatiseur|conseil|besoin d'?un|"
        r"looking for|which (fan|ac|air con)|recommend|any suggestion", re.I)),
    ("office_heat", re.compile(
        r"bureau|open space|au travail|au boulot|trop chaud.*(bureau|travail)|"
        r"office|at work|workplace", re.I)),
    ("heat", re.compile(
        r"canicule|vague de chaleur|forte chaleur|il fait (très )?chaud|"
        r"heat\s?wave|heatwave|scorching|\d{2}\s?°?c|degrees", re.I)),
]

# ---- Reddit 搜索词（覆盖高温 / 缺货 / 抢购 / 办公 / 求购）----
REDDIT_QUERIES = [
    "climatiseur rupture", "climatiseur mobile stock", "canicule bureau",
    "ventilateur rupture", "clim appartement recommandation",
    "heatwave no air conditioning office", "portable air conditioner sold out uk",
]
# ---- Google News RSS 查询（法国本地新闻 + 渠道 + 缺货/抢购）----
GNEWS_QUERIES = [
    "canicule climatiseur rupture", "ventilateur rupture Carrefour OR Darty OR Fnac",
    "climatiseur mobile vente canicule", "Leroy Merlin OR Boulanger climatiseur stock",
    "Amazon France climatiseur rupture canicule",
]


def log(msg):
    print(msg, flush=True)


def today_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def classify(text):
    for theme, rx in THEME_RULES:
        if rx.search(text or ""):
            return theme
    return None


def clean(s, n=180):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:n]


def load_existing():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"items": [], "themes": {}, "sources": {}, "updated": today_iso(), "note": ""}


# ------------------------------------------------------------------ Reddit
def fetch_reddit(items):
    if requests is None:
        log("[reddit] requests 不可用，跳过"); return "skipped"
    ok = 0
    for q in REDDIT_QUERIES:
        url = ("https://www.reddit.com/search.json?q=%s&sort=new&limit=15&t=week"
               % quote_plus(q))
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                log("[reddit] HTTP %s（%s）" % (r.status_code, q)); continue
            data = r.json()
            for ch in (data.get("data", {}) or {}).get("children", []) or []:
                d = ch.get("data", {}) or {}
                if d.get("over_18"):
                    continue
                title = d.get("title", "")
                body = d.get("selftext", "")
                theme = classify(title + " " + body)
                if not theme:
                    continue
                created = d.get("created_utc")
                ts = today_iso()
                try:
                    ts = datetime.fromtimestamp(float(created), timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    pass
                items.append({
                    "source": "reddit",
                    "sub": "r/" + str(d.get("subreddit", "")),
                    "theme": theme,
                    "title": clean(title, 140),
                    "snippet": clean(body, 160),
                    "url": "https://www.reddit.com" + str(d.get("permalink", "")),
                    "ts": ts,
                    "lang": "fr" if re.search(r"[àâçéèêëîïôûùü]", title + body) else "en",
                })
                ok += 1
            time.sleep(1.2)  # 礼貌节流
        except Exception as e:
            log("[reddit] 失败（%s）：%s" % (q, e))
    log("[reddit] 命中 %d 条" % ok)
    return "ok" if ok else "empty"


# --------------------------------------------------------------- Google News
def fetch_gnews(items):
    if requests is None:
        log("[gnews] requests 不可用，跳过"); return "skipped"
    ok = 0
    for q in GNEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q=%s&hl=fr&gl=FR&ceid=FR:fr"
               % quote_plus(q))
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                log("[gnews] HTTP %s（%s）" % (r.status_code, q)); continue
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "")
                link = (it.findtext("link") or "")
                desc = (it.findtext("description") or "")
                theme = classify(title + " " + desc)
                if not theme:
                    continue
                pub = it.findtext("pubDate") or ""
                ts = today_iso()
                for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
                    try:
                        ts = datetime.strptime(pub, fmt).strftime("%Y-%m-%d"); break
                    except Exception:
                        continue
                src_el = it.find("source")
                publisher = clean(src_el.text, 40) if src_el is not None else ""
                items.append({
                    "source": "googlenews",
                    "publisher": publisher,
                    "theme": theme,
                    "title": clean(title, 140),
                    "snippet": clean(desc, 160),
                    "url": link.strip(),
                    "ts": ts,
                    "lang": "fr",
                })
                ok += 1
            time.sleep(0.6)
        except Exception as e:
            log("[gnews] 失败（%s）：%s" % (q, e))
    log("[gnews] 命中 %d 条" % ok)
    return "ok" if ok else "empty"


# ---------------------------------------------------------------- X / Instagram
def fetch_x(items):
    # X 免费 API 已关闭；如自备 Bearer Token 可在此扩展（v2 recent search）。
    if os.environ.get("X_BEARER"):
        log("[x] 检测到 X_BEARER，但采集器未实现付费 API 调用（预留扩展点）")
    return "disabled"


def fetch_instagram(items):
    # Instagram 登录墙 + 反爬，无 keyless 稳定方案；预留（需官方 Graph API + 商业账号）。
    return "disabled"


# ------------------------------------------------------------------ main
def dedup(items):
    seen, out = set(), []
    for it in items:
        key = (it.get("url") or "") or (it.get("title") or "")
        key = key.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key); out.append(it)
    return out


def main():
    existing = load_existing()
    items = []
    sources = {}

    try:
        sources["reddit"] = fetch_reddit(items)
    except Exception as e:
        log("[reddit] 顶层异常：%s" % e); sources["reddit"] = "error"
    try:
        sources["googlenews"] = fetch_gnews(items)
    except Exception as e:
        log("[gnews] 顶层异常：%s" % e); sources["googlenews"] = "error"
    sources["x"] = fetch_x(items)
    sources["instagram"] = fetch_instagram(items)

    items = dedup(items)

    # 若本轮啥都没抓到（限流/网络）——保留旧快照，避免看板变空
    if not items:
        log("[buzz] 本轮无新条目，保留既有快照")
        existing.setdefault("updated", today_iso())
        existing["sources"] = {**existing.get("sources", {}), **sources}
        write(existing)
        return

    # 按时间倒序 + 标题，确定性排序，截断
    items.sort(key=lambda x: (x.get("ts", ""), x.get("title", "")), reverse=True)
    items = items[:MAX_ITEMS]

    themes = {}
    for it in items:
        themes[it["theme"]] = themes.get(it["theme"], 0) + 1

    out = {
        "updated": today_iso(),
        "sources": sources,
        "themes": themes,
        "items": items,
        "note": existing.get("note", "社媒 & 新闻舆情快照（Reddit + Google News）。"),
    }
    write(out)
    log("[done] 舆情 %d 条；主题 %s" % (len(items), themes))


def write(obj):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("[fatal] 顶层兜底：%s" % e)
    sys.exit(0)  # 永不因抓取失败而让 Action 标红
