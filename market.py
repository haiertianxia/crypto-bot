"""
Market data — simulated engine (offline fallback).
Generates realistic fake price data using a random walk with drift + volatility.
"""

import random
import math
import time
from datetime import datetime, timedelta

# ─── Price Simulator ──────────────────────────────────────────────────────────

class PriceSimulator:
    """
    Generates realistic OHLCV-like data via Geometric Brownian Motion.
    Works fully offline — no internet required.
    """
    def __init__(self, symbol="BTCUSDT", start_price=67000, annual_vol=0.8, annual_drift=0.05):
        self.symbol = symbol
        self.price = start_price
        self.vol = annual_vol
        self.drift = annual_drift
        self.open_time = datetime(2026, 1, 1)

    def tick(self) -> float:
        """Advance price by one minute using GBM."""
        dt = 1 / (252 * 1440)  # 1 minute in trading-year units
        z = random.gauss(0, 1)
        self.price *= math.exp((self.drift - 0.5 * self.vol**2) * dt + self.vol * math.sqrt(dt) * z)
        return round(self.price, 2)

    def get_klines(self, interval="1h", limit=100):
        """
        Return simulated OHLCV klines.
        interval: 1m, 5m, 15m, 1h, 4h, 1d (all treated as 1h ticks for simplicity)
        """
        interval_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        ticks = interval_map.get(interval, 60)

        klines = []
        p = self.price
        now = datetime.now()

        for i in range(limit, 0, -1):
            open_t = now - timedelta(minutes=i * ticks)
            o = p
            highs, lows, closes = [o], [o], []
            for _ in range(ticks):
                z = random.gauss(0, 1)
                dt = 1 / (252 * 1440)
                p = p * math.exp((self.drift - 0.5 * self.vol**2) * dt + self.vol * math.sqrt(dt) * z)
                highs.append(p)
                lows.append(p)
                closes.append(p)
            c = closes[-1] if closes else p
            h = max(highs)
            l = min(lows)
            v = random.uniform(500, 2000)
            klines.append([
                open_t.timestamp() * 1000,
                round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 2),
            ])
        self.price = p  # sync
        return klines

    def get_price(self) -> float:
        return round(self.price, 2)

    def get_24hr_stats(self):
        klines = self.get_klines("1h", 24)
        prices = [k[4] for k in klines]
        current = self.price
        open_p = prices[0]
        high = max(prices)
        low = min(prices)
        change = current - open_p
        change_pct = (change / open_p) * 100 if open_p else 0
        return {
            "price": round(current, 2),
            "priceChange": round(change, 2),
            "priceChangePct": round(change_pct, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "volume": round(random.uniform(1e9, 3e9), 0),
            "quoteVolume": round(random.uniform(2e4, 8e4), 0),
        }


# ─── Singleton simulator ─────────────────────────────────────────────────────
_sim: PriceSimulator | None = None

def get_simulator(symbol="BTCUSDT"):
    global _sim
    if _sim is None:
        _sim = PriceSimulator(symbol=symbol, start_price=67000)
    return _sim


# ─── Public API (same interface as Binance one) ────────────────────────────────

def get_symbol_price(symbol: str = "BTCUSDT") -> float:
    return get_simulator(symbol).get_price()


def get_24hr_ticker(symbol: str = "BTCUSDT") -> dict:
    return get_simulator(symbol).get_24hr_stats()


def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 100):
    return get_simulator(symbol).get_klines(interval, limit)


# ─── Technical Indicators ────────────────────────────────────────────────────

def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI from closing prices (Wilder's method)."""
    if len(closes) < period + 1:
        return None
    period = int(period)
    closes = [float(c) for c in closes]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 4)


def calculate_ema(closes: list[float], period: int = 20) -> float | None:
    if len(closes) < period:
        return None
    closes = [float(c) for c in closes]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def calculate_macd(closes: list[float], fast=12, slow=26, signal=9) -> tuple | None:
    if len(closes) < slow + signal:
        return None
    def ema_slice(data, n):
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        for v in data[n:]:
            e = v * k + e * (1 - k)
        return e
    closes = [float(c) for c in closes]
    macd = ema_slice(closes, fast) - ema_slice(closes, slow)
    macd_hist = [ema_slice(closes[:i], fast) - ema_slice(closes[:i], slow)
                 for i in range(slow, len(closes))]
    sig = ema_slice(macd_hist, signal)
    return (round(macd, 4), round(sig, 4), round(macd - sig, 4))


def get_market_snapshot(symbol: str = "BTCUSDT", rsi_period: int = 14):
    """All-in-one snapshot for dashboard."""
    sim = get_simulator(symbol)
    ticker = sim.get_24hr_stats()
    closes = [k[4] for k in sim.get_klines("1h", 100)]
    rsi = calculate_rsi(closes, rsi_period)
    return {
        "symbol": symbol,
        "price": ticker["price"],
        "rsi": rsi,
        "high_24h": ticker["high"],
        "low_24h": ticker["low"],
        "volume_24h": ticker["quoteVolume"],
        "price_change_pct": ticker["priceChangePct"],
        "rsi_period": rsi_period,
        "simulated": True,
    }
