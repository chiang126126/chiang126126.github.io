"""
Discord Webhook 通知器。

两个 webhook：
  DISCORD_WEBHOOK_URL   — 信号通知（每个 TradeIntent 推一条）
  DISCORD_ALERT_WEBHOOK — 系统告警（风控熔断、API 连续失败）

消息格式：Embed，颜色区分多/空/告警。
"""

from __future__ import annotations

import os
from datetime import datetime

import requests

from sprite_catcher.models import Side, TradeIntent

_TIMEOUT = 5

_COLOR_LONG = 0x2ECC71   # 绿
_COLOR_SHORT = 0xE74C3C  # 红
_COLOR_ALERT = 0xF39C12  # 橙


def _post(webhook_url: str, payload: dict) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json=payload, timeout=_TIMEOUT)
    except Exception:
        pass  # 通知失败不影响主流程


def notify_signal(intent: TradeIntent, pool: str, rejection_reasons: list[str] | None = None) -> None:
    """推送一个交易信号 Embed。"""
    url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        return

    color = _COLOR_LONG if intent.side == Side.LONG else _COLOR_SHORT
    direction = "🟢 做多" if intent.side == Side.LONG else "🔴 做空"

    fields = [
        {"name": "策略", "value": intent.strategy_id, "inline": True},
        {"name": "方向", "value": direction, "inline": True},
        {"name": "币种", "value": intent.symbol, "inline": True},
        {"name": "入场价", "value": f"{intent.entry_price:.6g}", "inline": True},
        {"name": "止损", "value": f"{intent.stop_loss_price:.6g}", "inline": True},
        {"name": "止盈", "value": str(intent.take_profit_price or "Trailing"), "inline": True},
        {"name": "仓位(USD)", "value": f"{intent.sizing.qty_quote_usd:.2f}", "inline": True},
        {"name": "Pool", "value": pool, "inline": True},
    ]

    if rejection_reasons:
        fields.append({"name": "⚠️ 被拒理由", "value": ", ".join(rejection_reasons), "inline": False})

    _post(url, {
        "embeds": [{
            "title": f"精灵捕手 · 信号触发 {intent.symbol}",
            "color": color,
            "fields": fields,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": "sprite-bot · paper mode" if os.getenv("RUN_MODE") == "paper" else "sprite-bot · LIVE"},
        }]
    })


def notify_scan_summary(total: int, candidates: int, intents: int, errors: int) -> None:
    """每轮扫描结束后推送摘要。"""
    url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    _post(url, {
        "embeds": [{
            "title": "扫描完成",
            "color": 0x95A5A6,
            "fields": [
                {"name": "总合约数", "value": str(total), "inline": True},
                {"name": "候选数", "value": str(candidates), "inline": True},
                {"name": "触发信号", "value": str(intents), "inline": True},
                {"name": "错误数", "value": str(errors), "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }]
    })


def alert(message: str) -> None:
    """推送系统告警（风控熔断、API 连续失败等）。"""
    url = os.getenv("DISCORD_ALERT_WEBHOOK", os.getenv("DISCORD_WEBHOOK_URL", ""))
    if not url:
        return
    _post(url, {
        "embeds": [{
            "title": "⚠️ sprite-bot 告警",
            "description": message,
            "color": _COLOR_ALERT,
            "timestamp": datetime.utcnow().isoformat(),
        }]
    })
