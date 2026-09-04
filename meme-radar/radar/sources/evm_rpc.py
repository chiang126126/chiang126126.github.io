# -*- coding: utf-8 -*-
"""evm_rpc.py — 直连 Robinhood Chain JSON-RPC（Arbitrum Orbit，标准 EVM）。

用途：ERC-20 元数据 / 余额 / owner()、合约字节码、事件日志（Pons 发行事件）、区块时间。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..util import keccak256, norm_addr, selector

SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"
SEL_DECIMALS = "0x313ce567"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_BALANCE_OF = "0x70a08231"
SEL_OWNER = "0x8da5cb5b"
ZERO = "0x0000000000000000000000000000000000000000"


def _pad_addr(addr: str) -> str:
    return norm_addr(addr)[2:].rjust(64, "0")


def decode_uint(hexstr: str) -> Optional[int]:
    try:
        h = (hexstr or "0x")[2:]
        return int(h, 16) if h else 0
    except ValueError:
        return None


def decode_string(hexstr: str) -> str:
    """兼容动态 string 与 bytes32 两种返回。"""
    try:
        raw = bytes.fromhex((hexstr or "0x")[2:])
    except ValueError:
        return ""
    if not raw:
        return ""
    if len(raw) >= 64:
        try:
            off = int.from_bytes(raw[0:32], "big")
            ln = int.from_bytes(raw[off:off + 32], "big")
            return raw[off + 32: off + 32 + ln].decode("utf-8", "replace").strip("\x00").strip()
        except Exception:
            pass
    return raw.rstrip(b"\x00").decode("utf-8", "replace").strip()


class EvmRpc:
    def __init__(self, http, url: str):
        self.http = http
        self.url = url
        self._id = 0

    def call(self, method: str, params: list, ttl: int = 0) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        resp = self.http.post_json(self.url, payload, ttl=ttl)
        if isinstance(resp, dict) and resp.get("error"):
            raise RuntimeError(f"rpc {method}: {resp['error']}")
        return (resp or {}).get("result")

    # ---------------------------------------------------------------- basics
    def block_number(self) -> int:
        return decode_uint(self.call("eth_blockNumber", [])) or 0

    def get_block(self, number: Any = "latest", full: bool = False) -> Dict[str, Any]:
        tag = number if isinstance(number, str) else hex(int(number))
        return self.call("eth_getBlockByNumber", [tag, full], ttl=0 if tag == "latest" else 86400) or {}

    def block_timestamp(self, number: int) -> Optional[int]:
        b = self.get_block(number)
        return decode_uint(b.get("timestamp")) if b else None

    def get_code(self, addr: str) -> str:
        return self.call("eth_getCode", [norm_addr(addr), "latest"], ttl=86400) or "0x"

    def is_contract(self, addr: str) -> bool:
        return len(self.get_code(addr)) > 2

    def eth_call(self, to: str, data: str, ttl: int = 0) -> str:
        return self.call("eth_call", [{"to": norm_addr(to), "data": data}, "latest"], ttl=ttl) or "0x"

    def get_logs(self, address: Optional[str], topics: List[Optional[str]], from_block: int, to_block: Any = "latest") -> List[dict]:
        flt: Dict[str, Any] = {"fromBlock": hex(int(from_block)),
                               "toBlock": to_block if isinstance(to_block, str) else hex(int(to_block))}
        if address:
            flt["address"] = norm_addr(address)
        if topics:
            flt["topics"] = topics
        return self.call("eth_getLogs", [flt]) or []

    def get_tx_receipt(self, tx_hash: str) -> Dict[str, Any]:
        return self.call("eth_getTransactionReceipt", [tx_hash], ttl=86400) or {}

    # ---------------------------------------------------------------- erc20
    def erc20_meta(self, token: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"address": norm_addr(token)}
        try:
            out["name"] = decode_string(self.eth_call(token, SEL_NAME, ttl=86400))
            out["symbol"] = decode_string(self.eth_call(token, SEL_SYMBOL, ttl=86400))
            out["decimals"] = decode_uint(self.eth_call(token, SEL_DECIMALS, ttl=86400))
            out["total_supply"] = decode_uint(self.eth_call(token, SEL_TOTAL_SUPPLY, ttl=300))
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
        return out

    def erc20_balance(self, token: str, holder: str) -> Optional[int]:
        try:
            return decode_uint(self.eth_call(token, SEL_BALANCE_OF + _pad_addr(holder)))
        except Exception:
            return None

    def owner(self, token: str) -> Optional[str]:
        """有 owner() 说明存在特权地址；返回 None 表示没有该函数或调用失败。"""
        try:
            res = self.eth_call(token, SEL_OWNER, ttl=3600)
            if not res or len(res) < 66:
                return None
            return "0x" + res[-40:]
        except Exception:
            return None

    @staticmethod
    def topic(signature: str) -> str:
        return "0x" + keccak256(signature.encode()).hex()

    @staticmethod
    def sel(signature: str) -> str:
        return selector(signature)
