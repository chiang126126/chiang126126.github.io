"""行情与执行：公开行情 + 币安 Spot Testnet（模拟盘）签名下单。"""
import hashlib
import hmac
import math
import time
from urllib.parse import urlencode

import requests

PUBLIC = "https://data-api.binance.vision"        # 公开行情（K线/价格），无需 key
TESTNET = "https://testnet.binance.vision"        # 现货模拟盘（旧，保留）
FUTURES_TESTNET = "https://testnet.binancefuture.com"  # USDT 本位合约模拟盘（当前使用）

TIMEOUT = 20


def klines(symbol, interval="1h", limit=200):
    r = requests.get(f"{PUBLIC}/api/v3/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "v": float(k[5])} for k in r.json()]


def last_price(symbol):
    r = requests.get(f"{PUBLIC}/api/v3/ticker/price", params={"symbol": symbol}, timeout=TIMEOUT)
    r.raise_for_status()
    return float(r.json()["price"])


def funding_rate(symbol):
    """永续资金费率（参考用，失败返回 None）。"""
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                         params={"symbol": symbol}, timeout=TIMEOUT)
        r.raise_for_status()
        return float(r.json()["lastFundingRate"]) * 100
    except Exception:
        return None


def fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()["data"][0]
        return int(d["value"]), d["value_classification"]
    except Exception:
        return None, None


_FILTERS = {}


def symbol_filters(symbol):
    """读取交易对的下单精度/最小量/最小名义（带缓存）。"""
    if symbol in _FILTERS:
        return _FILTERS[symbol]
    r = requests.get(f"{TESTNET}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=TIMEOUT)
    r.raise_for_status()
    flt = r.json()["symbols"][0]["filters"]
    step, min_qty, min_notional = None, 0.0, 0.0
    for x in flt:
        if x["filterType"] == "LOT_SIZE":
            step = float(x["stepSize"]); min_qty = float(x["minQty"])
        if x["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(x.get("minNotional") or x.get("notional") or 0)
    _FILTERS[symbol] = {"step": step, "min_qty": min_qty, "min_notional": min_notional}
    return _FILTERS[symbol]


def round_step(qty, step):
    if not step:
        return qty
    return math.floor(qty / step + 1e-9) * step


def fmt_qty(qty, step):
    d = max(0, int(round(-math.log10(step)))) if step and step < 1 else 0
    return f"{qty:.{d}f}"


class Testnet:
    """币安现货模拟盘客户端（仅在 MODE=testnet 时使用）。"""

    def __init__(self, key, secret):
        self.key = key
        self.secret = secret

    def free_balance(self, asset):
        for b in self.account().get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def _signed(self, method, path, params):
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{TESTNET}{path}?{query}&signature={sig}"
        r = requests.request(method, url, headers={"X-MBX-APIKEY": self.key}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def market_buy_quote(self, symbol, quote_usdt):
        """用 quoteOrderQty 花指定 USDT 市价买入。"""
        return self._signed("POST", "/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quoteOrderQty": round(quote_usdt, 2)})

    def market_sell_qty(self, symbol, qty):
        return self._signed("POST", "/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET",
            "quantity": qty})

    def account(self):
        return self._signed("GET", "/api/v3/account", {})


_FFILTERS = {}


def futures_filters(symbol):
    """USDT 本位合约交易对的下单精度/最小量/最小名义（一次性拉全表后缓存）。"""
    if not _FFILTERS:
        r = requests.get(f"{FUTURES_TESTNET}/fapi/v1/exchangeInfo", timeout=TIMEOUT)
        r.raise_for_status()
        for s in r.json()["symbols"]:
            step, min_qty, min_notional = 0.0, 0.0, 0.0
            for x in s["filters"]:
                if x["filterType"] == "LOT_SIZE":
                    step = float(x["stepSize"]); min_qty = float(x["minQty"])
                if x["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(x.get("notional") or 0)
            _FFILTERS[s["symbol"]] = {"step": step, "min_qty": min_qty, "min_notional": min_notional}
    return _FFILTERS.get(symbol, {"step": 0.001, "min_qty": 0.0, "min_notional": 0.0})


class Futures:
    """币安 USDT 本位合约模拟盘客户端（testnet.binancefuture.com）。
    现阶段固定 1 倍杠杆，可做多(LONG=BUY)/做空(SHORT=SELL)，平仓走 reduceOnly。
    注意：合约模拟盘的 API key 与现货模拟盘【不同】，需在 testnet.binancefuture.com 单独申请。"""

    def __init__(self, key, secret):
        self.key = key
        self.secret = secret
        self._lev_set = set()

    def _signed(self, method, path, params):
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{FUTURES_TESTNET}{path}?{query}&signature={sig}"
        r = requests.request(method, url, headers={"X-MBX-APIKEY": self.key}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def free_balance(self, asset="USDT"):
        for b in self._signed("GET", "/fapi/v2/balance", {}):
            if b["asset"] == asset:
                return float(b["availableBalance"])
        return 0.0

    def set_leverage(self, symbol, lev=1):
        if symbol in self._lev_set:
            return
        try:
            self._signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(lev)})
        except Exception as e:
            print(f"[warn] 设置杠杆失败 {symbol}: {e}")
        self._lev_set.add(symbol)

    def market_open(self, symbol, side, qty):
        """开仓：LONG→BUY，SHORT→SELL。qty 为合约张数（已按 step 格式化的字符串）。"""
        s = "BUY" if side == "LONG" else "SELL"
        return self._signed("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": s, "type": "MARKET",
            "quantity": qty, "newOrderRespType": "RESULT"})

    def market_close(self, symbol, side, qty):
        """平仓（reduceOnly）：平 LONG→SELL，平 SHORT→BUY。"""
        s = "SELL" if side == "LONG" else "BUY"
        return self._signed("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": s, "type": "MARKET",
            "quantity": qty, "reduceOnly": "true", "newOrderRespType": "RESULT"})
