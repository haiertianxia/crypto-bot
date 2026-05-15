"""
Binance 真实交易接口 — 签名认证 + 现货下单。
支持两种模式：
  SIMULATE_MODE=1  → 模拟成交（记录在DB，不发真实委托）
  SIMULATE_MODE=0  → 真实下单（需要 BINAKE_API_KEY / BINAKE_API_SECRET）

使用说明：
  1. 注册 Binance 账号，开启「只读 API Key」（行情），
     或「启用现货交易」的 Key（实盘，强烈建议设置 IP 白名单）
  2. 设置环境变量（或在 config.py 中填写）：
       export BINAKE_API_KEY="your_key"
       export BINAKE_API_SECRET="your_secret"
       export SIMULATE_MODE=0   # 1=模拟 0=实盘
  3. 平台有 IP 限制？先用 SIMULATE_MODE=1 跑，验证通过后再切实盘

安全提示：
  ⚠️  API Secret 不要提交到代码仓库
  ⚠️  建议为 Bot 新建只读/现货 Key，不要开启提币权限
  ⚠️  生产环境务必设置 IP 白名单
"""

import os
import time
import hmac
import hashlib
import requests
import logging
from typing import Literal

log = logging.getLogger("exchange")

# ─── Config ───────────────────────────────────────────────────────────────────

API_KEY     = os.environ.get("BINAKE_API_KEY", "")
API_SECRET  = os.environ.get("BINAKE_API_SECRET", "")
SIMULATE   = os.environ.get("SIMULATE_MODE", "1") == "1"

# 现货 REST API 基地址
REST_URL    = "https://api.binance.com"
TEST_URL    = "https://testnet.binance.vision"   # Testnet（无需 Key）

BASE_URL    = TEST_URL if SIMULATE else REST_URL

# ─── 签名 ─────────────────────────────────────────────────────────────────────

def _sign(params: dict) -> dict:
    """对请求参数进行 HMAC SHA256 签名。"""
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    params["signature"] = signature
    return params


def _headers() -> dict:
    return {"X-MBX-APIKEY": API_KEY} if API_KEY else {}


def _get(endpoint: str, params: dict = None, signed: bool = False) -> dict:
    url = f"{BASE_URL}{endpoint}"
    p = dict(params) if params else {}
    if signed:
        p = _sign(p)
    resp = requests.get(url, params=p, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, params: dict = None, signed: bool = False) -> dict:
    url = f"{BASE_URL}{endpoint}"
    p = dict(params) if params else {}
    if signed:
        p = _sign(p)
    resp = requests.post(url, params=p, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


# ─── Market Data ──────────────────────────────────────────────────────────────

def get_price(symbol: str = "BTCUSDT") -> float:
    """当前最新价格（不需要签名）。"""
    if SIMULATE:
        from market import get_simulator
        return get_simulator(symbol).get_price()
    data = _get("/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])


def get_ticker_24hr(symbol: str = "BTCUSDT") -> dict:
    if SIMULATE:
        from market import get_simulator
        return get_simulator(symbol).get_24hr_stats()
    data = _get("/api/v3/ticker/24hr", {"symbol": symbol})
    return {
        "price":          float(data["lastPrice"]),
        "priceChange":    float(data["priceChange"]),
        "priceChangePct":float(data["priceChangePercent"]),
        "high":           float(data["highPrice"]),
        "low":            float(data["lowPrice"]),
        "volume":         float(data["volume"]),
        "quoteVolume":    float(data["quoteVolume"]),
    }


def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 100):
    """K线数据（不需要签名）。"""
    if SIMULATE:
        from market import get_simulator
        return get_simulator(symbol).get_klines(interval, limit)
    data = _get("/api/v3/klines", {
        "symbol": symbol, "interval": interval, "limit": limit,
    })
    return [[float(v) for v in row[:6]] for row in data]


# ─── Account ──────────────────────────────────────────────────────────────────

def get_account() -> dict:
    """账户余额（需要签名）。"""
    if SIMULATE:
        return {"balances": []}  # bot.py 的 state 管理
    return _get("/api/v3/account", signed=True)


# ─── Orders ───────────────────────────────────────────────────────────────────

OrderType  = Literal["MARKET", "LIMIT"]
OrderSide  = Literal["BUY", "SELL"]


def place_order(
    symbol: str,
    side: OrderSide,
    order_type: OrderType = "MARKET",
    quantity: float | None = None,
    price: float | None = None,
    quote_quantity: float | None = None,  # 市价单按金额下单
) -> dict:
    """
    市价单 / 限价单。
    - MARKET  + quantity         → 市价成交
    - MARKET  + quoteQty          → 市价按金额（币币通用）
    - LIMIT   + price + quantity  → 限价挂单
    返回订单结果 dict；失败抛异常。
    """
    params = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
    }
    if order_type == "MARKET":
        if quote_quantity:
            params["quoteOrderQty"] = str(quote_quantity)
        elif quantity:
            params["quantity"] = str(quantity)
        else:
            raise ValueError("MARKET order requires quantity or quoteQty")
    elif order_type == "LIMIT":
        if not price or not quantity:
            raise ValueError("LIMIT order requires price and quantity")
        params["price"] = str(price)
        params["quantity"] = str(quantity)
        params["timeInForce"] = "GTC"
    else:
        raise ValueError(f"Unsupported order type: {order_type}")

    if SIMULATE:
        log.info("🧪 [SIMULATE] place_order: %s %s %s qty=%s quote=%s",
                 side, order_type, symbol, quantity, quote_quantity)
        return {
            "orderId":        999999,
            "symbol":         symbol,
            "side":           side,
            "type":           order_type,
            "price":          price or 0,
            "origQty":        str(quantity or quote_quantity),
            "executedQty":    str(quantity or quote_quantity),
            "status":         "FILLED",
            "transactTime":   int(time.time() * 1000),
            "isSimulated":    True,
        }

    result = _post("/api/v3/order", params, signed=True)
    log.info("📡 [BINANCE] place_order: %s", result)
    return result


def get_order(symbol: str, order_id: int) -> dict:
    """查询订单状态（签名）。"""
    if SIMULATE:
        return {"orderId": order_id, "status": "FILLED", "isSimulated": True}
    return _get("/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)


def cancel_order(symbol: str, order_id: int) -> dict:
    """取消挂单（签名）。"""
    if SIMULATE:
        log.info("🧪 [SIMULATE] cancel_order: %s %s", symbol, order_id)
        return {"orderId": order_id, "status": "CANCELED", "isSimulated": True}
    return _post("/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)


# ─── 工具 ─────────────────────────────────────────────────────────────────────

def format_order_msg(r: dict, side: str) -> str:
    sym   = r.get("symbol", "")
    qty   = r.get("executedQty", r.get("origQty", "—"))
    price = r.get("price", r.get("avgPrice", "市价"))
    oid   = r.get("orderId", "")
    status = r.get("status", "")
    sim   = "[模拟] " if r.get("isSimulated") else ""
    return (
        f"{sim}订单已成交 #{oid}\n"
        f"  {side} {qty} {sym}\n"
        f"  执行价: {price}\n"
        f"  状态: {status}"
    )
