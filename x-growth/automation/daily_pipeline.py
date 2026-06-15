#!/usr/bin/env python3
"""每日选题流水线 · 海外"离谱搞钱"案例

流程：抓 RSS 信源(过去 N 小时) -> 拼接喂给 Claude -> 用猎奇雷达筛出 3 个选题
-> 写成中文 Markdown 到 output/YYYY-MM-DD.md

本地运行：
    export ANTHROPIC_API_KEY=sk-...
    pip install -r requirements.txt
    python daily_pipeline.py

环境变量：
    ANTHROPIC_API_KEY  必填
    ANTHROPIC_MODEL    选填，默认 claude-opus-4-8（想省钱可设 claude-sonnet-4-6 / claude-haiku-4-5）
    LOOKBACK_HOURS     选填，默认 36
    MAX_ITEMS          选填，喂给模型的最多条目数，默认 60
"""

from __future__ import annotations

import os
import sys
import time
import datetime as dt
from pathlib import Path

import feedparser
import anthropic

HERE = Path(__file__).resolve().parent
FEEDS_FILE = HERE / "feeds.txt"
OUTPUT_DIR = HERE / "output"

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "36"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "60"))

# 与 prompts/01-signal-filter.md 同源：猎奇雷达 + 三柱 + 7 段结构
SYSTEM_PROMPT = """你是一个中文 X + 小红书账号的主编。账号招牌：扒海外"离谱但真实"的
搞钱案例（蠢生意为主打），用"卧槽这也行"的猎奇感吸引中文受众。
护城河是真实——每个案例必须有具体数字、原始来源、时间线。

绝对不要选：教科书式的"成功人士做对了三件事"、名气太大被讲烂的、平庸励志。
只要能让人瞪大眼睛的。

三个内容柱：
1. 猎奇案例 WTF (60%) — 离谱/反常/自带传播的真实赚钱案例
2. 信息差清单 LIST (25%) — "中文圈没人讲的N种赚钱方式"清单体
3. 情绪共鸣 HOOK (15%) — 经济焦虑/打工痛点/副业共鸣

猎奇类型雷达：🤪蠢生意 ⛏️卖铲子 💥暴起暴落 🕵️窥私账本 🚇地下赛道 ⚡极限荒诞"""

USER_TEMPLATE = """下面是过去 {hours} 小时抓到的海外原文（英文为主）。请：

第1步 去重，合并同一案例。
第2步 给每条按猎奇度(最高权重)/数字硬度/自带传播性/信息差价值/可拆解性打分(1-10)。
第3步 选出 TOP 3（尽量三柱均衡，优先猎奇度高的）。每个输出：
  - 中文标题（≤22字，制造"这也行？"反差或好奇缺口）
  - 猎奇类型 + 柱(WTF/LIST/HOOK)
  - 原始来源（标题 + 链接）
  - 核心数字（收入/售价/时间/起点）
  - 「最骚的一步/反转点」是什么（必须挖出来）
  - 3 个中文 Hook 候选（每个≤45字，先抛离谱事实再留悬念）
  - 故事化提纲（钩子→凭什么火→揭秘→反转→认知→行动呼吁）
  - 小红书封面标题（≤18字，可1个emoji）
第4步 标记并排除：编造/无法核实数字、荐股荐币、政治敏感（标 ⚠️）。

输出干净中文 Markdown，不要废话。英文数字/术语保留原文+中文解释。

--- 海外原文如下 ---

{corpus}
"""


def load_feeds() -> list[str]:
    urls = []
    for line in FEEDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def within_lookback(entry, cutoff: float) -> bool:
    for key in ("published_parsed", "updated_parsed"):
        ts = entry.get(key)
        if ts:
            return time.mktime(ts) >= cutoff
    # 没有时间戳就保留（宁可多喂，让模型去筛）
    return True


def fetch_corpus(urls: list[str]) -> tuple[str, int]:
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    items: list[str] = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # 单个源失败不影响整体
            print(f"[warn] 抓取失败 {url}: {e}", file=sys.stderr)
            continue
        source = feed.feed.get("title", url)
        for entry in feed.entries:
            if not within_lookback(entry, cutoff):
                continue
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "").strip()
            if len(summary) > 800:
                summary = summary[:800] + "…"
            if not title:
                continue
            items.append(f"[来源: {source}]\n标题: {title}\n链接: {link}\n摘要: {summary}\n")
            if len(items) >= MAX_ITEMS:
                break
        if len(items) >= MAX_ITEMS:
            break
    return "\n---\n".join(items), len(items)


def call_claude(corpus: str) -> str:
    client = anthropic.Anthropic()  # 从 ANTHROPIC_API_KEY 读取
    user = USER_TEMPLATE.format(hours=LOOKBACK_HOURS, corpus=corpus)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[error] 缺少 ANTHROPIC_API_KEY", file=sys.stderr)
        return 1

    urls = load_feeds()
    print(f"[info] 信源 {len(urls)} 个，回看 {LOOKBACK_HOURS}h，模型 {MODEL}")

    corpus, n = fetch_corpus(urls)
    if n == 0:
        print("[warn] 没抓到任何条目，跳过本次（可能是信源不稳定）", file=sys.stderr)
        return 0
    print(f"[info] 抓到 {n} 条，调用 Claude…")

    picks = call_claude(corpus)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    out = OUTPUT_DIR / f"{today}.md"
    header = (
        f"# 每日选题 · {today}\n\n"
        f"> 自动生成 · 信源 {len(urls)} 个 · 抓到 {n} 条 · 模型 {MODEL}\n"
        f"> ⚠️ 发布前务必回原始来源核对所有数字。用 `prompts/02-case-breakdown.md` 把选中的选题展开成成品。\n\n"
        f"---\n\n"
    )
    out.write_text(header + picks + "\n", encoding="utf-8")
    print(f"[done] 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
