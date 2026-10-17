# -*- coding: utf-8 -*-
"""config.py — 读取 rules.json / chains/*.json，并集中管理环境变量。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .util import CONFIG_DIR, load_json


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (CONFIG_DIR / "rules.json")
    rules = load_json(p)
    if not isinstance(rules, dict):
        raise RuntimeError(f"rules.json 缺失或损坏: {p}")
    return rules


def load_chain(chain_id: str = "robinhood") -> Dict[str, Any]:
    p = CONFIG_DIR / "chains" / f"{chain_id}.json"
    cfg = load_json(p)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"链配置缺失: {p}")
    return cfg


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class Env:
    """所有密钥都是可选的；缺失只会降级功能，不会让流水线失败。"""

    @property
    def blockscout_api_key(self) -> str:
        return env("BLOCKSCOUT_API_KEY")

    @property
    def coingecko_api_key(self) -> str:
        return env("COINGECKO_API_KEY")

    @property
    def gmgn_api_key(self) -> str:
        return env("GMGN_API_KEY")

    @property
    def gmgn_base_url(self) -> str:
        return env("GMGN_BASE_URL")

    @property
    def llm_provider(self) -> str:
        return env("LLM_PROVIDER", "none").lower()

    @property
    def llm_api_key(self) -> str:
        return env("LLM_API_KEY")

    @property
    def llm_model(self) -> str:
        return env("LLM_MODEL")

    @property
    def llm_base_url(self) -> str:
        return env("LLM_BASE_URL")

    @property
    def rpc_url(self) -> str:
        return env("ROBINHOOD_RPC_URL")

    @property
    def offline(self) -> bool:
        return env("RADAR_OFFLINE") == "1"

    @property
    def dry_run(self) -> bool:
        return env("RADAR_DRY_RUN") == "1"


ENV = Env()
