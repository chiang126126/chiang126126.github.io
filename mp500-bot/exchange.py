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
FUTURES_LIVE = "https://fapi.binance.com"         # USDT 本位合约【主网实盘】（MODE=live 才用）

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


def ticker_24h(symbol):
    """24h 涨跌%（现货）。失败返回 None。"""
    try:
        r = requests.get(f"{PUBLIC}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=TIMEOUT)
        r.raise_for_status()
        return float(r.json()["priceChangePercent"])
    except Exception:
        return None


def yahoo_quote(symbol):
    """Yahoo 行情：返回 (最新价, 相对昨收涨跌%)，失败 (None, None)。
    用于纳指期货 NQ=F、美债10Y ^TNX、美元 DX=F、MSTR/NVDA/COIN 等跨市场领先信息。"""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                         params={"interval": "1d", "range": "2d"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        last = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        chg = (last / prev - 1) * 100 if prev else None
        return last, (round(chg, 2) if chg is not None else None)
    except Exception:
        return None, None


def cross_market():
    """跨市场领先信息一揽子（每项独立失败降级为 None，绝不让 Evidence 构建崩溃）。"""
    q = yahoo_quote
    nq, nq_chg = q("NQ=F")          # 纳指期货
    tnx, tnx_chg = q("^TNX")        # 美债10Y收益率指数(值≈收益率%×10)
    dxy, dxy_chg = q("DX=F")        # 美元指数期货
    if dxy is None:
        dxy, dxy_chg = q("DX-Y.NYB")  # 备用: ICE 美元指数现货
    mstr, mstr_chg = q("MSTR")
    nvda, nvda_chg = q("NVDA")
    coin, coin_chg = q("COIN")
    return {"nq": nq, "nq_chg": nq_chg,
            "tnx": (round(tnx / 10, 2) if tnx else None), "tnx_chg": tnx_chg,
            "dxy": dxy, "dxy_chg": dxy_chg,
            "mstr_chg": mstr_chg, "nvda_chg": nvda_chg, "coin_chg": coin_chg,
            "btc_chg": ticker_24h("BTCUSDT"),
            "ethbtc_chg": ticker_24h("ETHBTC"),      # ETH/BTC 相对强弱
            "solbtc_chg": ticker_24h("SOLBTC")}      # SOL/BTC 相对强弱


def oi_change_24h(symbol):
    """合约持仓量(OI) 24小时变化%。判断下跌是新空进场(OI升)还是杠杆清算(OI降)。"""
    try:
        r = requests.get("https://fapi.binance.com/futures/data/openInterestHist",
                         params={"symbol": symbol, "period": "1h", "limit": 25}, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if len(d) < 2:
            return None
        first, last = float(d[0]["sumOpenInterest"]), float(d[-1]["sumOpenInterest"])
        return round((last / first - 1) * 100, 2) if first else None
    except Exception:
        return None


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


_FFILTERS = {}   # 缓存按 (base, symbol) 区分：testnet 与主网的精度表互不串用


def futures_filters(symbol, base=FUTURES_TESTNET):
    """USDT 本位合约交易对的下单精度/最小量/最小名义（按 base 一次性拉全表后缓存）。"""
    if base not in _FFILTERS:
        r = requests.get(f"{base}/fapi/v1/exchangeInfo", timeout=TIMEOUT)
        r.raise_for_status()
        table = {}
        for s in r.json()["symbols"]:
            step, min_qty, min_notional = 0.0, 0.0, 0.0
            for x in s["filters"]:
                if x["filterType"] == "LOT_SIZE":
                    step = float(x["stepSize"]); min_qty = float(x["minQty"])
                if x["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(x.get("notional") or 0)
            table[s["symbol"]] = {"step": step, "min_qty": min_qty, "min_notional": min_notional}
        _FFILTERS[base] = table
    return _FFILTERS[base].get(symbol, {"step": 0.001, "min_qty": 0.0, "min_notional": 0.0})


class Futures:
    """币安 USDT 本位合约客户端。base 决定环境：
    - FUTURES_TESTNET（默认，模拟盘）：key 从 testnet.binancefuture.com 申请；
    - FUTURES_LIVE（主网实盘，MODE=live 才用）：key 只勾『允许合约』、禁提现、设 IP 白名单。
    现阶段固定 1 倍杠杆，可做多(LONG=BUY)/做空(SHORT=SELL)，平仓走 reduceOnly。"""

    def __init__(self, key, secret, base=FUTURES_TESTNET):
        self.key = key
        self.secret = secret
        self.base = base
        self._lev_set = set()

    def _signed(self, method, path, params):
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base}{path}?{query}&signature={sig}"
        r = requests.request(method, url, headers={"X-MBX-APIKEY": self.key}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def free_balance(self, asset="USDT"):
        for b in self._signed("GET", "/fapi/v2/balance", {}):
            if b["asset"] == asset:
                return float(b["availableBalance"])
        return 0.0

    def wallet_balance(self, asset="USDT"):
        """钱包余额（含被持仓占用的保证金，不含未实现盈亏）——live 用它做权益真值。"""
        for b in self._signed("GET", "/fapi/v2/balance", {}):
            if b["asset"] == asset:
                return float(b["balance"])
        return 0.0

    def position_mode_dual(self):
        """True=双向持仓(hedge)。本系统按单向持仓设计，live 检测到双向会拒绝交易。"""
        return bool(self._signed("GET", "/fapi/v1/positionSide/dual", {}).get("dualSidePosition"))

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
