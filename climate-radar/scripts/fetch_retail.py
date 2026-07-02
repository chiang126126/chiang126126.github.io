#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_retail.py — 零售库存/价格「尽力」采集器（climate-radar 零售信号数据管道）

⚠️ 免责声明 / DISCLAIMER
    • 这是一个【尽力而为（best-effort）】的采集器，不是可靠保证。电商站点
      （Amazon / Darty / Carrefour 等）普遍有反爬与法律/ToS 限制，浏览器
      无法直连，服务器抓取也经常失败或被拒。
    • 本脚本【尊重 robots.txt / 各站点服务条款】：只做低频、轻量、公开页面的
      读取；不登录、不绕过验证码/风控、不高并发。若你部署它，请自行确认目标
      站点的 robots.txt 与 ToS 允许该访问，并对合规负责。
    • 因此本脚本以【透传手工维护的 retail.json】为主：读-改-写现有文件，
      仅在“确信”探测到信号时才覆盖对应条目；抓取失败一律保留原值。
    • 库存/价格仅供参考，不构成任何采购或经营建议。

用法：
    python3 fetch_retail.py
    读取并原地更新同目录上一级 data/retail.json。永不因部分失败而非零退出。
"""

import json
import os
import sys
import time
import datetime

# requests 为可选依赖：缺失时脚本仍能优雅地透传现有 JSON
try:
    import requests
except Exception:  # pragma: no cover
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.normpath(os.path.join(HERE, "..", "data", "retail.json"))

# 礼貌的默认 UA + 请求间隔（秒）；低频、单线程
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
POLITE_DELAY = 3.0     # 每个目标之间的最小间隔
HTTP_TIMEOUT = 12.0

VALID_STATUS = {"out", "tight", "ok"}


def log(msg):
    print("[fetch_retail] " + str(msg), flush=True)


def load_json(path):
    """读取现有 retail.json；不存在或损坏时返回一个最小可用结构。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("根节点不是对象")
        data.setdefault("items", [])
        return data
    except FileNotFoundError:
        log("未找到现有 retail.json，创建最小结构")
        return {"items": [], "source": "", "note": "", "updated": ""}
    except Exception as e:
        log("读取现有 retail.json 失败，改为透传空结构：%s" % e)
        return {"items": [], "source": "", "note": "", "updated": ""}


def probe_item(session, item):
    """
    对单个条目做“尽力”探测。返回 (new_status_or_None, new_price_or_None)。

    说明（诚实地）：可靠的库存判定需要针对每个站点写解析逻辑，且极易被反爬
    阻断。这里刻意【不做激进抓取】：仅当条目带有 probe_url 且页面可无障碍取回、
    且能高置信匹配到明确的缺货/有货措辞时，才返回一个新状态；否则返回
    (None, None) 表示“无把握”，调用方将保留手工维护的原值。
    """
    url = item.get("probe_url")
    if not url or session is None:
        return None, None

    try:
        resp = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except Exception as e:
        log("  探测失败（保留原值）：%s -> %s" % (item.get("match"), e))
        return None, None

    if resp.status_code != 200:
        log("  非 200（保留原值）：%s HTTP %s" % (item.get("match"), resp.status_code))
        return None, None

    text = (resp.text or "").lower()

    # 高置信、保守的措辞启发式（法/英）。只有明确命中才判定；否则不动。
    out_markers = [
        "actuellement indisponible", "rupture de stock", "épuisé",
        "currently unavailable", "out of stock", "temporairement en rupture",
    ]
    in_markers = [
        "en stock", "in stock", "ajouter au panier", "add to cart",
        "disponible", "livraison",
    ]

    if any(m in text for m in out_markers):
        return "out", None
    if any(m in text for m in in_markers):
        return "ok", None

    # 措辞不明确 —— 诚实地放弃，保留人工值
    return None, None


def main():
    data = load_json(DATA_PATH)
    items = data.get("items", [])
    if not isinstance(items, list):
        log("items 非数组，重置为空")
        items = []
        data["items"] = items

    session = None
    if requests is not None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        })
    else:
        log("未安装 requests，跳过所有网络探测，纯透传现有值")

    updated_count = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            new_status, new_price = probe_item(session, item)
        except Exception as e:  # 每条目独立 try/except，绝不因单条崩溃
            log("  未预期异常（保留原值）：%s -> %s" % (item.get("match"), e))
            new_status, new_price = None, None

        if new_status in VALID_STATUS and new_status != item.get("status"):
            log("  更新：%s  %s -> %s" % (item.get("match"), item.get("status"), new_status))
            item["status"] = new_status
            item["_auto"] = True  # 标记：由采集器覆盖
            updated_count += 1
        if isinstance(new_price, (int, float)):
            item["price"] = new_price

        # 礼貌间隔（仅在确有网络探测时）
        if session is not None and item.get("probe_url") and i < len(items) - 1:
            time.sleep(POLITE_DELAY)

    # 仅当确有自动更新时才刷新 updated；否则保留手工日期，避免制造空 diff
    if updated_count > 0:
        data["updated"] = datetime.date.today().isoformat()

    # 确定性输出：sorted keys + 尾随换行
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.write("\n")
    except Exception as e:
        log("写入 retail.json 失败：%s" % e)
        # 写失败也不硬失败（保留仓库中原文件）
        return 0

    log("完成：%d 条自动更新 / 共 %d 条快照 -> %s" % (updated_count, len(items), DATA_PATH))
    return 0


if __name__ == "__main__":
    # 永不因部分失败而非零退出：任何未捕获异常都吞掉并返回 0
    try:
        sys.exit(main())
    except Exception as e:  # pragma: no cover
        log("顶层异常（不硬失败）：%s" % e)
        sys.exit(0)
