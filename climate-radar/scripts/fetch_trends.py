#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_trends.py — Google Trends 数据管道（供 climate-radar 看板读取）
------------------------------------------------------------------
Google Trends 没有官方浏览器可调 API（CORS 阻断），因此改由 GitHub Action
定时运行本脚本：用 pytrends 抓取法国（geo=FR）关键词的搜索热度，写出
climate-radar/data/trends.json，前端只读该快照。

设计原则（务必遵守）：
  • 尽力而为、绝不失败：pytrends 受 Google 严格限流（常见 429），任何单个
    关键词失败都不应让整个流程崩溃或退出非零——保留旧值即可（读-改-写）。
  • 逐关键词单独 build_payload：每个词各自归一化到自身 0–100，idx=最新值。
  • rising = 最新值 > 之前窗口（除最后一点外）的均值。
  • 确定性写出（sort_keys + 末尾换行），以便 Action 的“有变化才提交”生效。

输出 JSON 形状（与前端 live-trends.js._parse 契约一致）：
  {
    "geo": "FR",
    "timeframe": "now 7-d",
    "source": "Google Trends (pytrends)",
    "updated": "YYYY-MM-DD",
    "keywords": [ {"kw": "...", "idx": 0-100, "rising": true/false}, ... ]
  }
"""

import json
import os
import sys
import time
import datetime

# 关键词顺序即前端展示顺序（法国降温家电市场）
KEYWORDS = [
    "climatiseur mobile",
    "ventilateur",
    "canicule",
    "rafraîchir appartement",
    "ventilateur de cou",
]

GEO = "FR"
TIMEFRAME = "now 7-d"      # 近 7 天；如需更平滑可改 "today 1-m"
SOURCE = "Google Trends (pytrends)"

# 路径：脚本在 climate-radar/scripts/，数据在 climate-radar/data/trends.json
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.normpath(os.path.join(_HERE, "..", "data", "trends.json"))


def today_iso():
    return datetime.date.today().isoformat()


def load_existing():
    """读取已有快照（做读-改-写的基线）。缺失/损坏则返回空骨架。"""
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("keywords"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 现有 trends.json 无法解析，将重建：{e}")
    return {"geo": GEO, "timeframe": TIMEFRAME, "source": SOURCE,
            "updated": today_iso(), "keywords": []}


def prev_map(existing):
    """kw -> {'idx','rising'}，用于单词抓取失败时回退旧值。"""
    m = {}
    for it in existing.get("keywords", []):
        if isinstance(it, dict) and "kw" in it:
            m[it["kw"]] = {
                "idx": int(it.get("idx", 0) or 0),
                "rising": bool(it.get("rising", False)),
            }
    return m


def compute_idx_rising(series):
    """
    series: 该关键词按时间排序的整数热度列表（0–100，已归一化到自身峰值）。
    返回 (idx, rising)：idx=最新值；rising=最新值 > 之前窗口均值。
    """
    vals = [int(round(float(v))) for v in series if v is not None]
    if not vals:
        return None
    idx = max(0, min(100, vals[-1]))
    if len(vals) >= 2:
        prior = vals[:-1]
        mean_prior = sum(prior) / len(prior)
        rising = vals[-1] > mean_prior
    else:
        rising = False
    return idx, bool(rising)


def fetch_one(pytrends, kw):
    """抓取单个关键词，返回 (idx, rising)；失败抛异常由上层捕获。"""
    pytrends.build_payload([kw], geo=GEO, timeframe=TIMEFRAME)
    df = pytrends.interest_over_time()
    if df is None or df.empty or kw not in df.columns:
        raise RuntimeError("空结果（可能限流或无数据）")
    # pytrends 会带一列 isPartial，取关键词列的原始序列
    series = list(df[kw].values)
    res = compute_idx_rising(series)
    if res is None:
        raise RuntimeError("热度序列为空")
    return res


def main():
    existing = load_existing()
    prev = prev_map(existing)

    pytrends = None
    try:
        from pytrends.request import TrendReq
        # hl 界面语言、tz 时区（法国 UTC+2≈-120 分，pytrends 用分钟且符号相反，取 -120）
        pytrends = TrendReq(hl="fr-FR", tz=-120, timeout=(10, 25), retries=2, backoff_factor=0.5)
    except Exception as e:  # noqa: BLE001
        # 连库都装不上/初始化失败：整体退化为保留旧值，仍写出（不失败）
        print(f"[warn] pytrends 初始化失败，保留现有快照：{e}")

    out_keywords = []
    ok_count = 0
    for i, kw in enumerate(KEYWORDS):
        got = None
        if pytrends is not None:
            for attempt in range(3):
                try:
                    idx, rising = fetch_one(pytrends, kw)
                    got = {"kw": kw, "idx": idx, "rising": rising}
                    ok_count += 1
                    print(f"[ok]  {kw!r}: idx={idx} rising={rising}")
                    break
                except Exception as e:  # noqa: BLE001 — 尽力而为，逐词兜底
                    wait = 2.0 * (attempt + 1)
                    print(f"[retry] {kw!r} 第{attempt + 1}次失败：{e} —— {wait:.0f}s 后重试")
                    time.sleep(wait)
        if got is None:
            # 回退旧值；旧值也没有则给一个保守默认（不影响 UI，仅占位）
            p = prev.get(kw, {"idx": 50, "rising": False})
            got = {"kw": kw, "idx": int(p["idx"]), "rising": bool(p["rising"])}
            print(f"[keep] {kw!r}: 保留旧值 idx={got['idx']} rising={got['rising']}")
        out_keywords.append(got)
        # 轻微间隔，降低被限流概率
        time.sleep(1.5)

    data = {
        "geo": GEO,
        "timeframe": TIMEFRAME,
        "source": SOURCE,
        "updated": today_iso(),
        "keywords": out_keywords,
    }

    # 确定性写出：sort_keys + 末尾换行，保证“有变化才提交”准确
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")

    print(f"\n[done] {ok_count}/{len(KEYWORDS)} 关键词实时获取成功；已写出 {DATA_PATH}")
    # 无论部分失败与否，均以 0 退出——数据管道不应因 Trends 抖动而标红
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — 终极兜底，绝不非零退出
        print(f"[fatal-caught] 未预期异常，保留现有快照：{e}")
        sys.exit(0)
