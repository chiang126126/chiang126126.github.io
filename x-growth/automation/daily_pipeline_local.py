#!/usr/bin/env python3
"""每日选题流水线（本地版）· 调 Claude Code CLI，消耗 Max 订阅，不用 API Key

流程：抓 RSS 信源(过去 N 小时) -> 拼成 prompt -> 调 `claude -p` CLI -> 写结果到 output/YYYY-MM-DD.md

使用前提：
    1. 已安装 Claude Code CLI 并登录（`claude` 命令可用，已用 Max 订阅 OAuth）
    2. pip install feedparser（不再需要 anthropic 包）

本地手动跑：
    cd x-growth/automation
    pip install feedparser
    python3 daily_pipeline_local.py

环境变量（可选）：
    LOOKBACK_HOURS   回看多少小时的条目，默认 36
    MAX_ITEMS        最多喂给模型多少条，默认 60
    CLAUDE_BIN       claude 可执行路径，默认 "claude"（在 PATH 里即可）
    OUTPUT_DIR       结果目录，默认 automation/output/
"""

from __future__ import annotations

import os
import sys
import time
import datetime as dt
import subprocess
import shutil
from pathlib import Path

import feedparser

HERE = Path(__file__).resolve().parent
FEEDS_FILE = HERE / "feeds.txt"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(HERE / "output")))

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "36"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "60"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

# ── 与 daily_pipeline.py / prompts/01-signal-filter.md 同源 ────────────────
FULL_PROMPT_TEMPLATE = """你是一个中文 X + 小红书账号的主编。账号招牌：扒海外"离谱但真实"的搞钱案例（蠢生意为主打），用"卧槽这也行"的猎奇感吸引中文受众。护城河是真实——每个案例必须有具体数字、原始来源、时间线。

绝对不要选：教科书式的"成功人士做对了三件事"、名气太大被讲烂的、平庸励志。只要能让人瞪大眼睛的。

三个内容柱：
1. 猎奇案例 WTF (60%) — 离谱/反常/自带传播的真实赚钱案例
2. 信息差清单 LIST (25%) — "中文圈没人讲的N种赚钱方式"清单体
3. 情绪共鸣 HOOK (15%) — 经济焦虑/打工痛点/副业共鸣

猎奇类型雷达：🤪蠢生意 ⛏️卖铲子 💥暴起暴落 🕵️窥私账本 🚇地下赛道 ⚡极限荒诞

---

下面是过去 {hours} 小时抓到的海外原文（英文为主）。请：

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


def check_claude_cli() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


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
    return True  # 没有时间戳就保留


def fetch_corpus(urls: list[str]) -> tuple[str, int]:
    cutoff = time.time() - LOOKBACK_HOURS * 3600
    items: list[str] = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
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


def call_claude_code(prompt: str) -> str:
    """调用本地 claude CLI（非交互模式 -p），消耗 Max 订阅配额。"""
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,  # Claude 思考可能需要较长时间
    )
    if result.returncode != 0:
        err = result.stderr.strip() or "(no stderr)"
        raise RuntimeError(f"claude 命令失败（exit {result.returncode}）: {err}")
    return result.stdout.strip()


def main() -> int:
    if not check_claude_cli():
        print(
            f"[error] 找不到 '{CLAUDE_BIN}' 命令。\n"
            "请先安装 Claude Code CLI 并登录：https://claude.ai/code",
            file=sys.stderr,
        )
        return 1

    urls = load_feeds()
    print(f"[info] 信源 {len(urls)} 个，回看 {LOOKBACK_HOURS}h，使用 Claude Code CLI（Max 订阅）")

    corpus, n = fetch_corpus(urls)
    if n == 0:
        print("[warn] 没抓到任何条目，跳过本次（可能信源不稳定或网络问题）", file=sys.stderr)
        return 0
    print(f"[info] 抓到 {n} 条，调用 claude CLI…（预计 30-90 秒）")

    prompt = FULL_PROMPT_TEMPLATE.format(hours=LOOKBACK_HOURS, corpus=corpus)
    picks = call_claude_code(prompt)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")
    out = OUTPUT_DIR / f"{today}.md"
    header = (
        f"# 每日选题 · {today}\n\n"
        f"> 本地 Claude Code CLI 生成 · 信源 {len(urls)} 个 · 抓到 {n} 条\n"
        f"> ⚠️ 发布前务必回原始来源核对所有数字。用 `prompts/02-case-breakdown.md` 把选中的选题展开成成品。\n\n"
        f"---\n\n"
    )
    out.write_text(header + picks + "\n", encoding="utf-8")
    print(f"[done] 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
