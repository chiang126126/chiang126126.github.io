"""
轻量级状态持久化（SQLite）。

存储：
  - signals  : 每次管道运行的结果（无论是否产生 TradeIntent）
  - positions: 当前持仓（paper 和 live 模式共用）

刻意保持极简：不用 ORM，直接 SQL。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from sprite_catcher.models import Side, TradeIntent

_DB_PATH = Path("sprite_bot.db")


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at  TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            token_id    TEXT NOT NULL,
            pool        TEXT,
            has_intent  INTEGER NOT NULL DEFAULT 0,
            strategy_id TEXT,
            side        TEXT,
            entry_price REAL,
            stop_loss   REAL,
            take_profit REAL,
            qty_usd     REAL,
            rejection   TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at   TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            side        TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss   REAL NOT NULL,
            take_profit REAL,
            qty_usd     REAL NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',  -- open | closed | sl_hit | tp_hit
            closed_at   TEXT,
            exit_price  REAL,
            pnl_usd     REAL,
            mode        TEXT NOT NULL DEFAULT 'paper'  -- paper | live
        );
        """)


def save_signal(result) -> None:
    """保存一次管道运行结果。result 是 pipeline.PipelineResult。"""
    intent = result.intent
    with _conn() as con:
        con.execute(
            """INSERT INTO signals
               (scanned_at, symbol, token_id, pool, has_intent, strategy_id,
                side, entry_price, stop_loss, take_profit, qty_usd, rejection)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                result.scanned_at.isoformat(),
                result.symbol,
                result.token_id,
                result.pool,
                1 if intent else 0,
                intent.strategy_id if intent else None,
                intent.side.value if intent else None,
                intent.entry_price if intent else None,
                intent.stop_loss_price if intent else None,
                intent.take_profit_price if intent else None,
                intent.sizing.qty_quote_usd if intent else None,
                result.rejection_reason,
            ),
        )


def open_position(intent: TradeIntent, mode: str = "paper") -> int:
    """记录开仓，返回 position id。"""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO positions
               (opened_at, symbol, strategy_id, side, entry_price, stop_loss,
                take_profit, qty_usd, mode)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                datetime.utcnow().isoformat(),
                intent.symbol,
                intent.strategy_id,
                intent.side.value,
                intent.entry_price,
                intent.stop_loss_price,
                intent.take_profit_price,
                intent.sizing.qty_quote_usd,
                mode,
            ),
        )
        return cur.lastrowid


def close_position(position_id: int, exit_price: float, status: str) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        row = con.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if not row:
            return
        qty = row["qty_usd"]
        entry = row["entry_price"]
        side = row["side"]
        pnl = (exit_price - entry) / entry * qty * (1 if side == "long" else -1)
        con.execute(
            "UPDATE positions SET status=?, closed_at=?, exit_price=?, pnl_usd=? WHERE id=?",
            (status, now, exit_price, pnl, position_id),
        )


def get_open_positions() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM positions WHERE status='open'").fetchall()
