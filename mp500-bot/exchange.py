"""行情与执行：公开行情 + 币安 Spot Testnet（模拟盘）签名下单。"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

PUBLIC = "https://data-api.binance.vision"   # 公开行情（K线/价格），无需 key
TESTNET = "https://testnet.binance.vision"   # 现货模拟盘，下单需 key

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


class Testnet:
    """币安现货模拟盘客户端（仅在 MODE=testnet 时使用）。"""

    def __init__(self, key, secret):
        self.key = key
        self.secret = secret

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
