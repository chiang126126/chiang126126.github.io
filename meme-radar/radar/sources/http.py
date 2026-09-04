# -*- coding: utf-8 -*-
"""http.py — 零依赖 HTTP 客户端：限速、重试、磁盘缓存、离线模式、调用计数。

用法：
    http = HttpClient("geckoterminal", rps=0.45, headers={...})
    data = http.get_json("https://.../networks/robinhood/new_pools", params={"page": 1}, ttl=120)

环境变量：
    RADAR_OFFLINE=1        只读缓存，不发网络请求（沙箱 / 单测）
    RADAR_HTTP_TIMEOUT     超时秒数（默认 20）
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from ..util import CACHE_DIR, load_json, save_json

UA = "meme-radar/0.1 (+https://github.com/chiang126126/chiang126126.github.io)"


class HttpError(Exception):
    def __init__(self, msg: str, status: int = 0, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


class Offline(HttpError):
    pass


class HttpClient:
    def __init__(self, name: str, rps: float = 2.0, headers: Optional[Dict[str, str]] = None,
                 timeout: Optional[float] = None, retries: int = 3, cache_dir: Optional[Path] = None):
        self.name = name
        self.min_interval = 1.0 / rps if rps and rps > 0 else 0.0
        self.headers = {"User-Agent": UA, "Accept": "application/json"}
        if headers:
            self.headers.update(headers)
        self.timeout = timeout or float(os.environ.get("RADAR_HTTP_TIMEOUT", "20"))
        self.retries = retries
        self.cache_dir = (cache_dir or CACHE_DIR) / "http" / name
        self.offline = os.environ.get("RADAR_OFFLINE", "") == "1"
        self._last = 0.0
        self.stats = {"calls": 0, "cache_hits": 0, "errors": 0, "tripped": False}
        self._mem: Dict[str, Any] = {}
        self.consecutive_errors = 0
        self.trip_after = int(os.environ.get("RADAR_HTTP_TRIP_AFTER", "12"))   # 连续 N 次失败后熔断本源

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _url(url: str, params: Optional[dict]) -> str:
        if not params:
            return url
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        return url + ("&" if "?" in url else "?") + q

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _throttle(self):
        if self.min_interval <= 0:
            return
        wait = self._last + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    # ---------------------------------------------------------------- public
    def get_json(self, url: str, params: Optional[dict] = None, ttl: int = 0,
                 headers: Optional[Dict[str, str]] = None) -> Any:
        full = self._url(url, params)
        return self._request("GET", full, None, ttl, headers)

    def post_json(self, url: str, body: Any, ttl: int = 0, headers: Optional[Dict[str, str]] = None) -> Any:
        payload = json.dumps(body, sort_keys=True).encode()
        return self._request("POST", url, payload, ttl, headers, cache_key=url + "|" + payload.decode())

    def _request(self, method: str, url: str, payload: Optional[bytes], ttl: int,
                 headers: Optional[Dict[str, str]], cache_key: Optional[str] = None) -> Any:
        key = cache_key or url
        if key in self._mem:
            self.stats["cache_hits"] += 1
            return self._mem[key]
        cpath = self._cache_path(key)
        cached = load_json(cpath) if (ttl > 0 or self.offline) else None
        if isinstance(cached, dict) and "body" in cached:
            age = time.time() - float(cached.get("ts", 0))
            if self.offline or age <= ttl:
                self.stats["cache_hits"] += 1
                self._mem[key] = cached["body"]
                return cached["body"]
        if self.offline:
            raise Offline(f"[{self.name}] offline & no cache for {url}")
        if self.trip_after > 0 and self.consecutive_errors >= self.trip_after:
            self.stats["tripped"] = True
            raise HttpError(f"[{self.name}] circuit tripped after {self.consecutive_errors} consecutive errors", 599)

        hdrs = dict(self.headers)
        if headers:
            hdrs.update(headers)
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
            try:
                self.stats["calls"] += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                body = json.loads(raw.decode("utf-8")) if raw else None
                if ttl > 0:
                    try:
                        save_json(cpath, {"ts": time.time(), "url": url, "body": body})
                    except OSError:
                        pass
                self._mem[key] = body
                self.consecutive_errors = 0
                return body
            except urllib.error.HTTPError as e:
                text = ""
                try:
                    text = e.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                last_err = HttpError(f"[{self.name}] HTTP {e.code} {url}", e.code, text)
                if e.code in (400, 401, 403, 404, 422):
                    break  # 不会因为重试而好转
                retry_after = e.headers.get("Retry-After") if e.headers else None
                delay = min(10.0, float(retry_after)) if retry_after and retry_after.isdigit() else min(6.0, 1.5 * (2 ** attempt))
                if attempt >= 1 and e.code == 429:
                    last_err = HttpError(f"[{self.name}] HTTP 429 rate-limited {url}", 429, text)
                    break
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
                last_err = HttpError(f"[{self.name}] {type(e).__name__}: {e} {url}")
                time.sleep(1.5 * (2 ** attempt))
        self.stats["errors"] += 1
        self.consecutive_errors += 1
        raise last_err or HttpError(f"[{self.name}] unknown error {url}")


class FakeHttp:
    """离线测试用：url(含 query) → payload。缺失时抛 HttpError 404。"""

    def __init__(self, routes: Optional[Dict[str, Any]] = None, name: str = "fake"):
        self.routes = routes or {}
        self.name = name
        self.calls: list = []
        self.stats = {"calls": 0, "cache_hits": 0, "errors": 0}

    def route(self, url: str, payload: Any):
        self.routes[url] = payload

    def _lookup(self, key: str) -> Any:
        self.calls.append(key)
        self.stats["calls"] += 1
        if key in self.routes:
            v = self.routes[key]
            return v() if callable(v) else v
        # 允许按前缀匹配（忽略 query 顺序等小差异）
        for k, v in self.routes.items():
            if key.startswith(k):
                return v() if callable(v) else v
        self.stats["errors"] += 1
        raise HttpError(f"[fake] 404 {key}", 404)

    def get_json(self, url: str, params: Optional[dict] = None, ttl: int = 0, headers=None) -> Any:
        return self._lookup(HttpClient._url(url, params))

    def post_json(self, url: str, body: Any, ttl: int = 0, headers=None) -> Any:
        method = body.get("method") if isinstance(body, dict) else ""
        key = url + "#" + str(method)
        if key in self.routes:
            v = self.routes[key]
            self.calls.append(key)
            return v(body) if callable(v) else v
        return self._lookup(url)
