# -*- coding: utf-8 -*-
"""gmgn.py — GMGN 聪明钱数据（可选）。

GMGN 官方 OpenAPI 需要 API key（见 github.com/GMGNAI/gmgn-skills，支持 robinhood 链），接口形态以官方为准。
这里提供两条路：
  1) 手动导入：把 GMGN 页面上的聪明钱地址整理成 JSON/CSV，放到 data/smart_wallets.manual.json；
  2) 自动拉取：配置 GMGN_API_KEY + GMGN_BASE_URL 后，通过下面的可插拔函数拉取（默认关闭，避免猜接口）。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from ..util import is_address, norm_addr


def import_manual(path: Path) -> List[Dict[str, Any]]:
    """支持 .json（[{address,label,score?}] 或 {wallets:[...]}）和 .csv（address,label 两列）。"""
    p = Path(path)
    if not p.exists():
        return []
    rows: List[Dict[str, Any]] = []
    if p.suffix.lower() == ".csv":
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({"address": r.get("address") or r.get("wallet") or "", "label": r.get("label") or "",
                             "score": r.get("score")})
    else:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return []
        if isinstance(data, dict):
            data = data.get("wallets") or data.get("addresses") or []
        for r in data:
            if isinstance(r, str):
                rows.append({"address": r, "label": "manual"})
            elif isinstance(r, dict):
                rows.append(r)
    out = []
    for r in rows:
        a = norm_addr(r.get("address") or "")
        if is_address(a):
            out.append({"address": a, "label": (r.get("label") or "manual")[:40],
                        "score": float(r["score"]) if r.get("score") not in (None, "") else None,
                        "source": "manual"})
    return out


class Gmgn:
    def __init__(self, http, api_key: str = "", base_url: str = "", chain: str = "robinhood"):
        self.http, self.api_key, self.base_url, self.chain = http, api_key, base_url.rstrip("/"), chain

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url)

    def smart_wallets(self) -> List[Dict[str, Any]]:
        """占位：接口路径通过 GMGN_BASE_URL 指定（例如自建的 gmgn-cli 导出服务）。失败返回 []。"""
        if not self.enabled:
            return []
        try:
            payload = self.http.get_json(f"{self.base_url}/smart_wallets", {"chain": self.chain}, ttl=1800,
                                         headers={"Authorization": f"Bearer {self.api_key}"})
        except Exception:
            return []
        rows = payload.get("data") if isinstance(payload, dict) else payload
        out = []
        for r in rows or []:
            a = norm_addr((r or {}).get("address") or "")
            if is_address(a):
                out.append({"address": a, "label": (r.get("label") or "gmgn")[:40], "score": r.get("score"),
                            "source": "gmgn"})
        return out
