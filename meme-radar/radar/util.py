# -*- coding: utf-8 -*-
"""util.py — 路径、JSON、时间、Keccak 等零依赖小工具。"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent                      # meme-radar/
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("RADAR_DATA_DIR", str(ROOT / "data")))
CACHE_DIR = DATA_DIR / "cache"

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# --------------------------------------------------------------------------- time
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    dt = dt or now_utc()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: Any) -> Optional[datetime]:
    """容错解析 ISO8601 / 秒级或毫秒级时间戳。"""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return (s if s.tzinfo else s.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if isinstance(s, (int, float)):
        ts = float(s)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(s, str):
        s2 = s.strip()
        if re.fullmatch(r"\d{10,13}(\.\d+)?", s2):
            return parse_iso(float(s2))
        s2 = s2.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s2)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def hours_between(a: Any, b: Any) -> Optional[float]:
    da, db = parse_iso(a), parse_iso(b)
    if not da or not db:
        return None
    return (db - da).total_seconds() / 3600.0


def day_key(dt: Optional[datetime] = None) -> str:
    return (dt or now_utc()).astimezone(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- json
def load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj: Any) -> None:
    """确定性输出（sort_keys + 换行结尾），方便『有变化才提交』。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


# --------------------------------------------------------------------------- numbers
def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None or x == "":
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1.0) * 100.0


def ema(values: list, period: int) -> Optional[float]:
    if not values or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def median(values: list) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def is_address(s: Any) -> bool:
    return isinstance(s, str) and bool(ADDR_RE.match(s))


def norm_addr(s: Any) -> str:
    return s.lower().strip() if isinstance(s, str) else ""


def short_addr(s: str) -> str:
    s = s or ""
    return s[:6] + "…" + s[-4:] if len(s) > 12 else s


# --------------------------------------------------------------------------- keccak-256 (纯 Python，用于事件 topic / 函数选择器)
_MASK = (1 << 64) - 1
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rol(v: int, n: int) -> int:
    n %= 64
    if n == 0:
        return v
    return ((v << n) | (v >> (64 - n))) & _MASK


def _keccak_f(st: list) -> list:
    for rc in _RC:
        c = [st[x] ^ st[x + 5] ^ st[x + 10] ^ st[x + 15] ^ st[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        st = [st[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(st[x + 5 * y], _ROT[x][y])
        st = [b[i] ^ ((~b[(i % 5 + 1) % 5 + 5 * (i // 5)] & _MASK) & b[(i % 5 + 2) % 5 + 5 * (i // 5)])
              for i in range(25)]
        st[0] ^= rc
    return st


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data)
    msg.append(0x01)
    while len(msg) % rate:
        msg.append(0x00)
    msg[-1] |= 0x80
    st = [0] * 25
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            st[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        st = _keccak_f(st)
    out = b"".join(st[i].to_bytes(8, "little") for i in range(4))
    return out[:32]


def selector(signature: str) -> str:
    """'transfer(address,uint256)' -> '0xa9059cbb'"""
    return "0x" + keccak256(signature.encode()).hex()[:8]


def topic0(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()
