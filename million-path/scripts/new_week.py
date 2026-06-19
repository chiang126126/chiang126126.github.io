#!/usr/bin/env python3
"""
MP500 / Million Path — 周复盘自动化脚本

自动完成「填数」中机械的部分：
  1) 读取 dashboard/data/ledger.json（不存在则从 ledger.sample.json 初始化）
  2) 计算下一周的 周次 / 日期区间 / 阶段，并结转上周 equity_end -> 本周 equity_start
  3) 在 ledger.json 追加一条新记录（待手填字段留空）
  4) 用 docs/weekly_review_TEMPLATE.md 生成 reviews/week-NN.md（已自动填好周次/日期/阶段/结转权益）

之后你只需在 reviews/week-NN.md 与 ledger.json 里填【需手填】字段，看板会自动重算。

用法：
  python scripts/new_week.py                # 生成下一周
  python scripts/new_week.py --regime risk-on   # 同时预填本周行情判断
"""
from __future__ import annotations  # 兼容 Python 3.7+（避免 `str | None` 在旧版报错）

import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "ledger.json"
SAMPLE = ROOT / "data" / "ledger.sample.json"
TEMPLATE = ROOT / "docs" / "weekly_review_TEMPLATE.md"
REVIEWS = ROOT / "reviews"

# 行情 -> 本周目标收益率(%)，对应 MP500 §4 收益预期表
REGIME_TARGET = {"risk-on": 4, "neutral": 1, "risk-off": 0}


def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    if SAMPLE.exists():
        print(f"[init] {LEDGER.name} 不存在，从 {SAMPLE.name} 初始化")
        data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        return data
    # 全新起步
    return {
        "meta": {
            "strategy": "MP500 / Million Path",
            "start_date": "2026-06-19",
            "initial_capital": 500,
            "usdt_cny": 6.74,
            "target_rmb": 1000000,
            "target_usd": 1000000,
        },
        "weeks": [],
    }


def next_week_entry(data: dict, regime: str | None) -> dict:
    weeks = data["weeks"]
    meta = data["meta"]
    if weeks:
        prev = weeks[-1]
        wk = prev["week"] + 1
        start = date.fromisoformat(prev["end_date"]) + timedelta(days=1)
        equity_start = prev["equity_end"]
        stage = prev["stage"]
    else:
        wk = 1
        start = date.fromisoformat(meta["start_date"])
        equity_start = meta["initial_capital"]
        stage = "S0"
    end = start + timedelta(days=6)
    target = REGIME_TARGET.get(regime, None) if regime else None
    return {
        "week": wk,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "stage": stage,
        "regime": regime,                 # 需手填（若未传 --regime）
        "equity_start": equity_start,      # 自动结转
        "equity_end": equity_start,        # 占位，待手填（默认=持平）
        "target_return_pct": target,       # 由行情自动推断
        "trades": None,                    # 需手填
        "win_rate_pct": None,              # 需手填
        "profit_factor": None,             # 需手填
        "max_drawdown_pct": None,          # 需手填
        "fees_funding_pct": None,          # 需手填
        "violations": 0,                   # 需手填（目标恒为 0）
        "notes": "",
    }


def write_review_md(entry: dict) -> Path:
    REVIEWS.mkdir(exist_ok=True)
    out = REVIEWS / f"week-{entry['week']:02d}.md"
    if out.exists():
        print(f"[skip] {out.relative_to(ROOT)} 已存在，未覆盖")
        return out
    tpl = TEMPLATE.read_text(encoding="utf-8")
    filled = (
        tpl.replace("{{WEEK}}", str(entry["week"]))
        .replace("{{START_DATE}}", entry["start_date"])
        .replace("{{END_DATE}}", entry["end_date"])
        .replace("{{STAGE}}", entry["stage"])
        .replace("{{EQUITY_START}}", str(entry["equity_start"]))
    )
    out.write_text(filled, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 MP500 下一周复盘")
    ap.add_argument("--regime", choices=list(REGIME_TARGET), help="本周 BTC 行情判断")
    args = ap.parse_args()

    data = load_ledger()
    entry = next_week_entry(data, args.regime)

    # 防重复：若该周已在 ledger 中则不追加
    if any(w["week"] == entry["week"] for w in data["weeks"]):
        print(f"[skip] Week {entry['week']} 已存在于 ledger.json，未追加")
    else:
        data["weeks"].append(entry)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] ledger.json 追加 Week {entry['week']} "
              f"({entry['start_date']} ~ {entry['end_date']}, 结转权益 {entry['equity_start']}U)")

    md = write_review_md(entry)
    print(f"[ok] 复盘文件就绪：{md.relative_to(ROOT)}")
    print("\n下一步：在上面两个文件里填【需手填】字段（equity_end / 笔数 / 胜率 / 盈亏比 / 回撤 / 费用 / 违规），"
          "看板会自动重算周收益与权益曲线。")


if __name__ == "__main__":
    main()
