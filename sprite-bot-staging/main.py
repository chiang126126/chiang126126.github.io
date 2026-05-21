"""
sprite-bot 主入口。

模式：
  paper  — 只记录信号，不下单（默认，安全）
  live   — 真实 OTOCO 下单（L6，后续版本实现）

调度：每 N 分钟运行一次完整扫描。
"""

from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("sprite-bot")

from sprite.notifier.discord import alert, notify_scan_summary, notify_signal
from sprite.scanner.pipeline import PipelineResult, run as run_pipeline
from sprite.scanner.screener import screen_candidates
from sprite.state.store import init_db, open_position, save_signal
from sprite import registry

_SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_MINUTES", "15")) * 60
_RUN_MODE = os.getenv("RUN_MODE", "paper")


def run_scan() -> None:
    log.info("=== 扫描开始 (mode=%s) ===", _RUN_MODE)

    # 1. 预筛候选
    try:
        candidates = screen_candidates()
    except Exception as e:
        log.error("预筛失败: %s", e)
        alert(f"预筛失败，扫描中止：{e}")
        return

    log.info("候选数: %d", len(candidates))

    # 2. 逐币深度分析
    results: list[PipelineResult] = []
    errors = 0
    open_intents = []  # TODO: 从 store 加载当前持仓对应的 intent

    for c in candidates:
        sym = c["symbol"]
        token_info = registry.lookup(sym)
        if not token_info:
            log.debug("%s: 未在 registry 中，跳过链上分析", sym)
            # 没有链上信息时，只能跑 OI 分析（L2 OI-only 模式，后续实现）
            continue

        try:
            result = run_pipeline(
                futures_symbol=sym,
                token_id=token_info.token_id,
                daily_pump_pct=c["price_change_pct"],
                open_intents=open_intents,
            )
            results.append(result)
            save_signal(result)

            if result.intent:
                log.info("✅ 信号: %s %s @ %s", sym, result.intent.side.value, result.intent.entry_price)
                notify_signal(result.intent, pool=result.pool or "")
                if _RUN_MODE == "paper":
                    open_position(result.intent, mode="paper")
                    log.info("   paper 仓位已记录")
                elif _RUN_MODE == "live":
                    log.warning("   LIVE 模式 L6 尚未实现，跳过下单")
                    # TODO: 调用 L6 OTOCO 执行
            else:
                log.debug("%s: 无信号 (%s)", sym, result.rejection_reason)

        except Exception as e:
            log.error("%s: 管道异常: %s", sym, e)
            errors += 1

    intents = sum(1 for r in results if r.intent)
    notify_scan_summary(
        total=len(candidates),
        candidates=len(results),
        intents=intents,
        errors=errors,
    )
    log.info("=== 扫描结束: %d 候选, %d 信号, %d 错误 ===", len(results), intents, errors)


def main() -> None:
    log.info("sprite-bot 启动 (mode=%s, interval=%ds)", _RUN_MODE, _SCAN_INTERVAL)
    init_db()

    if _RUN_MODE == "live" and not os.getenv("BINANCE_API_KEY"):
        raise SystemExit("LIVE 模式需要 BINANCE_API_KEY，请检查 .env")

    while True:
        try:
            run_scan()
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，退出")
            break
        except Exception as e:
            log.error("扫描异常（将在下一轮重试）: %s", e)
            alert(f"扫描主循环异常：{e}")

        log.info("等待 %d 秒后下一轮...", _SCAN_INTERVAL)
        time.sleep(_SCAN_INTERVAL)


if __name__ == "__main__":
    main()
