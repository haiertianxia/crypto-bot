"""
Backtesting engine for crypto trading strategies.
Supports multiple strategy classes and comparative analysis.

Strategy modes:
  - RSI (default):           classic RSI mean-reversion
  - RSI_MACD:               RSI signal + MACD histogram cross confirmation
  - RSI_BB:                  RSI signal + Bollinger Band touch confirmation
"""

import argparse
import json
import logging
import math
import random
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Callable, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backtest")


# ─── Data / Price Generator ───────────────────────────────────────────────────

def generate_price_series(
    start_price: float = 67000.0,
    length: int = 1000,
    annual_vol: float = 0.6,
    annual_drift: float = 0.03,
    seed: Optional[int] = None,
) -> list[float]:
    """
    Generate a synthetic price series (random walk with drift).
    Each entry = closing price of one 1-hour candle.
    """
    if seed is not None:
        random.seed(seed)
    prices = [start_price]
    for _ in range(length - 1):
        dt = 1 / (252 * 24)  # 1 hour in trading-year units
        z = random.gauss(0, 1)
        p = prices[-1] * math.exp(
            (annual_drift - 0.5 * annual_vol ** 2) * dt
            + annual_vol * math.sqrt(dt) * z
        )
        prices.append(round(p, 2))
    return prices


def load_historical_klines(path: str) -> list[float]:
    """
    Load closing prices from a CSV file (one price per line, or exchange export).
    Supports: plain newline-separated prices, or CSV with header (column 4 = close).
    """
    import csv
    prices = []
    with open(path, "r") as f:
        first = f.read(1024)
        f.seek(0)
        has_header = first.strip() and not first.strip().split("\n")[0].replace(
            ".", ""
        ).replace("-", "").isdigit()
        reader = csv.reader(f)
        for row in reader:
            if has_header and reader.line_num == 1:
                continue
            try:
                # Try column 4 (close) if multiple columns, else first
                p = float(row[4]) if len(row) >= 5 else float(row[0])
                prices.append(p)
            except (ValueError, IndexError):
                continue
    return prices


# ─── Trade Record ─────────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: int
    entry_price: float
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    side: str = "LONG"
    quantity: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    strategy_name: str = "RSI"
    symbol: str = "BTCUSDT"
    start_capital: float = 10000.0
    end_capital: float = 10000.0
    total_return: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    num_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 56,
            f"  {self.strategy_name} — {self.symbol}",
            "=" * 56,
            f"  Initial Capital:   ${self.start_capital:>10,.2f}",
            f"  Final Capital:     ${self.end_capital:>10,.2f}",
            f"  Total Return:      ${self.total_return:>+10,.2f}  ({self.total_return_pct:+.2f}%)",
            f"  Max Drawdown:      ${self.max_drawdown:>10,.2f}  ({self.max_drawdown_pct:.2f}%)",
            f"  Sharpe Ratio:      {self.sharpe_ratio:>10.4f}",
            f"  Sortino Ratio:     {self.sortino_ratio:>10.4f}",
            "-" * 56,
            f"  Total Trades:      {self.num_trades:>10d}",
            f"  Wins:              {self.winning_trades:>10d}",
            f"  Losses:            {self.losing_trades:>10d}",
            f"  Win Rate:          {self.win_rate:>10.2f}%",
            f"  Avg Win:           ${self.avg_win:>+10,.2f}",
            f"  Avg Loss:          ${self.avg_loss:>+10,.2f}",
            f"  Profit Factor:     {self.profit_factor:>10.4f}",
            "=" * 56,
        ]
        return "\n".join(lines)

    def to_json(self, path: str):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2, default=str)
        log.info("Results saved to %s", path)


# ─── Base Strategy Class ──────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """Abstract base for all backtesting strategies."""

    def __init__(
        self,
        stop_loss_pct: float = 5.0,
        position_size_usdt: float = 1000.0,
    ):
        self.stop_loss_pct = stop_loss_pct
        self.position_size_usdt = position_size_usdt

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        pass

    @abstractmethod
    def calculate_indicators(self, closes: list[float]) -> dict:
        """
        Calculate strategy-specific indicators from close prices.
        Returns a dict that will be passed to should_buy / should_sell.
        """
        pass

    @abstractmethod
    def should_buy(self, indicators: dict, price: float, prev_indicators: dict | None) -> tuple[bool, str]:
        """
        Return (buy_signal: bool, reason: str).
        prev_indicators is the previous tick's indicators (for cross detection), may be None.
        """
        pass

    @abstractmethod
    def should_sell(self, indicators: dict, price: float, entry_price: float,
                    prev_indicators: dict | None) -> tuple[bool, str]:
        """
        Return (sell_signal: bool, reason: str).
        Checks stop-loss AND strategy-specific exit.
        """
        pass


# ─── Indicator Calculations (shared helpers) ──────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
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


def calc_ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def calc_macd(closes: list[float], fast=12, slow=26, signal=9) -> tuple | None:
    if len(closes) < slow + signal:
        return None
    def ema_slice(data, n):
        k = 2 / (n + 1)
        e = sum(data[:n]) / n
        for v in data[n:]:
            e = v * k + e * (1 - k)
        return e
    macd_line = ema_slice(closes, fast) - ema_slice(closes, slow)
    macd_hist = [ema_slice(closes[:i], fast) - ema_slice(closes[:i], slow)
                 for i in range(slow, len(closes))]
    sig_line = ema_slice(macd_hist, signal)
    return (round(macd_line, 4), round(sig_line, 4), round(macd_line - sig_line, 4))


def calc_bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> tuple | None:
    import statistics
    if len(closes) < period:
        return None
    lookback = closes[-period:]
    middle = statistics.mean(lookback)
    std = statistics.stdev(lookback)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return (round(upper, 4), round(middle, 4), round(lower, 4))


# ─── Strategy: RSI (classic) ─────────────────────────────────────────────────

class RSIStrategy(BaseStrategy):
    def __init__(
        self,
        period: int = 14,
        buy_threshold: float = 30.0,
        sell_threshold: float = 70.0,
        stop_loss_pct: float = 5.0,
        position_size_usdt: float = 1000.0,
    ):
        super().__init__(stop_loss_pct, position_size_usdt)
        self.period = period
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    @property
    def name(self) -> str:
        return f"RSI({self.period})"

    def calculate_indicators(self, closes: list[float]) -> dict:
        return {"rsi": calc_rsi(closes, self.period)}

    def should_buy(self, ind: dict, price: float, prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        if rsi and rsi < self.buy_threshold:
            return True, f"RSI={rsi:.2f} < {self.buy_threshold}"
        return False, ""

    def should_sell(self, ind: dict, price: float, entry: float,
                    prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        # Stop loss
        if self.stop_loss_pct > 0:
            loss_pct = (price - entry) / entry * 100
            if loss_pct <= -self.stop_loss_pct:
                return True, f"Stop loss ({loss_pct:.2f}%)"
        # RSI overbought
        if rsi and rsi > self.sell_threshold:
            return True, f"RSI={rsi:.2f} > {self.sell_threshold}"
        return False, ""


# ─── Strategy: RSI + MACD (dual confirmation) ────────────────────────────────

class MACD:
    @staticmethod
    def cross(prev_h: float | None, curr_h: float) -> str | None:
        if prev_h is None:
            return None
        if prev_h < 0 and curr_h >= 0:
            return "bullish"
        if prev_h > 0 and curr_h <= 0:
            return "bearish"
        return None


class RSI_MACD_Strategy(BaseStrategy):
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_buy: float = 30.0,
        rsi_sell: float = 70.0,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        stop_loss_pct: float = 5.0,
        position_size_usdt: float = 1000.0,
    ):
        super().__init__(stop_loss_pct, position_size_usdt)
        self.rsi_period = rsi_period
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

    @property
    def name(self) -> str:
        return f"RSI_MACD({self.rsi_period},{self.macd_fast},{self.macd_slow},{self.macd_signal})"

    def calculate_indicators(self, closes: list[float]) -> dict:
        return {
            "rsi": calc_rsi(closes, self.rsi_period),
            "macd": calc_macd(closes, self.macd_fast, self.macd_slow, self.macd_signal),
        }

    def should_buy(self, ind: dict, price: float, prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        macd = ind.get("macd")
        prev_macd = prev.get("macd") if prev else None

        if not (rsi and rsi < self.rsi_buy):
            return False, ""
        if not macd:
            return False, ""
        curr_hist = macd[2]
        prev_hist = prev_macd[2] if prev_macd else None
        cross = MACD.cross(prev_hist, curr_hist)
        if cross == "bullish":
            return True, f"RSI={rsi:.2f} + MACD bullish cross (hist={curr_hist:+.4f})"
        return False, f"RSI ok ({rsi:.2f}) no MACD cross (hist={curr_hist:+.4f})"

    def should_sell(self, ind: dict, price: float, entry: float,
                    prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        macd = ind.get("macd")
        prev_macd = prev.get("macd") if prev else None

        if self.stop_loss_pct > 0:
            loss_pct = (price - entry) / entry * 100
            if loss_pct <= -self.stop_loss_pct:
                return True, f"Stop loss ({loss_pct:.2f}%)"
        if not (rsi and rsi > self.rsi_sell):
            return False, ""
        if not macd:
            return False, ""
        curr_hist = macd[2]
        prev_hist = prev_macd[2] if prev_macd else None
        cross = MACD.cross(prev_hist, curr_hist)
        if cross == "bearish":
            return True, f"RSI={rsi:.2f} + MACD bearish cross (hist={curr_hist:+.4f})"
        return False, f"RSI ok ({rsi:.2f}) no MACD cross (hist={curr_hist:+.4f})"


# ─── Strategy: RSI + Bollinger Bands (dual confirmation) ─────────────────────

class RSI_BB_Strategy(BaseStrategy):
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_buy: float = 30.0,
        rsi_sell: float = 70.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        stop_loss_pct: float = 5.0,
        position_size_usdt: float = 1000.0,
    ):
        super().__init__(stop_loss_pct, position_size_usdt)
        self.rsi_period = rsi_period
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.bb_period = bb_period
        self.bb_std = bb_std

    @property
    def name(self) -> str:
        return f"RSI_BB({self.rsi_period},{self.bb_period},{self.bb_std})"

    def calculate_indicators(self, closes: list[float]) -> dict:
        return {
            "rsi": calc_rsi(closes, self.rsi_period),
            "bb": calc_bollinger(closes, self.bb_period, self.bb_std),
        }

    def should_buy(self, ind: dict, price: float, prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        bb = ind.get("bb")
        if not (rsi and rsi < self.rsi_buy):
            return False, ""
        if not bb:
            return False, ""
        lower = bb[2]
        if price <= lower:
            return True, f"RSI={rsi:.2f} + price <= BB lower ({price:.2f} <= {lower:.2f})"
        return False, f"RSI ok ({rsi:.2f}) price above BB lower ({price:.2f} > {lower:.2f})"

    def should_sell(self, ind: dict, price: float, entry: float,
                    prev: dict | None) -> tuple[bool, str]:
        rsi = ind.get("rsi")
        bb = ind.get("bb")
        if self.stop_loss_pct > 0:
            loss_pct = (price - entry) / entry * 100
            if loss_pct <= -self.stop_loss_pct:
                return True, f"Stop loss ({loss_pct:.2f}%)"
        if not (rsi and rsi > self.rsi_sell):
            return False, ""
        if not bb:
            return False, ""
        upper = bb[0]
        if price >= upper:
            return True, f"RSI={rsi:.2f} + price >= BB upper ({price:.2f} >= {upper:.2f})"
        return False, f"RSI ok ({rsi:.2f}) price below BB upper ({price:.2f} < {upper:.2f})"


# ─── Backtest Engine ──────────────────────────────────────────────────────────

def run_backtest(
    prices: list[float],
    strategy: BaseStrategy,
    initial_capital: float = 10000.0,
    symbol: str = "BTCUSDT",
) -> BacktestResult:
    """
    Run a full backtest over price data with the given strategy.
    Returns a detailed BacktestResult.
    """
    result = BacktestResult(
        strategy_name=strategy.name,
        symbol=symbol,
        start_capital=initial_capital,
        end_capital=initial_capital,
    )

    cash = initial_capital
    position = 0.0
    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    equity_curve: list[dict] = []
    peak = initial_capital

    # Track previous tick indicators (for cross detection)
    prev_indicators: dict | None = None
    entry_price_for_strategy = 0.0

    for i in range(len(prices)):
        price = prices[i]
        lookback = prices[: i + 1]

        indicators = strategy.calculate_indicators(lookback)

        if open_trade is None:
            # Check BUY
            should_buy, reason = strategy.should_buy(indicators, price, prev_indicators)
            if should_buy:
                qty = strategy.position_size_usdt / price
                cost = qty * price
                if cost <= cash:
                    cash -= cost
                    position += qty
                    entry_price_for_strategy = price
                    open_trade = Trade(
                        entry_time=i,
                        entry_price=price,
                        side="LONG",
                        quantity=qty,
                        reason=f"[{strategy.name}] {reason}",
                    )
        else:
            # Check SELL
            should_sell, reason = strategy.should_sell(
                indicators, price, open_trade.entry_price, prev_indicators,
            )
            if should_sell:
                qty = open_trade.quantity
                sell_value = qty * price
                pnl = sell_value - (qty * open_trade.entry_price)
                pnl_pct = (price - open_trade.entry_price) / open_trade.entry_price * 100
                cash += sell_value
                position = 0.0
                open_trade.exit_time = i
                open_trade.exit_price = price
                open_trade.pnl = round(pnl, 2)
                open_trade.pnl_pct = round(pnl_pct, 4)
                trades.append(open_trade)
                open_trade = None

        # Record equity
        equity = cash + position * price
        equity_curve.append({
            "step": i,
            "equity": round(equity, 2),
            "price": price,
        })
        if equity > peak:
            peak = equity

        prev_indicators = indicators

    # Force close at end
    if open_trade is not None and len(equity_curve) > 0:
        final_price = prices[-1]
        qty = open_trade.quantity
        cash += qty * final_price
        position = 0.0
        pnl = (final_price - open_trade.entry_price) * qty
        open_trade.exit_time = len(prices) - 1
        open_trade.exit_price = final_price
        open_trade.pnl = round(pnl, 2)
        open_trade.pnl_pct = round((final_price - open_trade.entry_price) / open_trade.entry_price * 100, 4)
        trades.append(open_trade)

    # Finalize
    equity_final = cash + position * prices[-1]
    result.end_capital = round(equity_final, 2)
    result.total_return = round(result.end_capital - result.start_capital, 2)
    result.total_return_pct = round(
        (result.end_capital - result.start_capital) / result.start_capital * 100, 4
    )

    # Trades analysis
    result.num_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = round(len(wins) / len(trades) * 100, 2) if trades else 0.0
    result.avg_win = round(sum(t.pnl for t in wins) / len(wins), 2) if wins else 0.0
    result.avg_loss = round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    result.profit_factor = round(gross_profit / gross_loss, 4) if gross_loss else float("inf")

    # Max drawdown
    peak_equity = initial_capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for point in equity_curve:
        if point["equity"] > peak_equity:
            peak_equity = point["equity"]
        dd = peak_equity - point["equity"]
        dd_pct = dd / peak_equity * 100 if peak_equity else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct
    result.max_drawdown = round(max_dd, 2)
    result.max_drawdown_pct = round(max_dd_pct, 2)

    # Sharpe + Sortino (annualized, 1h bars = 8760/year)
    if len(equity_curve) > 1:
        returns = []
        down_returns = []
        for j in range(1, len(equity_curve)):
            r = (equity_curve[j]["equity"] - equity_curve[j - 1]["equity"]) / equity_curve[j - 1]["equity"]
            returns.append(r)
            if r < 0:
                down_returns.append(r)
        avg_r = sum(returns) / len(returns)
        var_r = sum((r - avg_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.0001
        result.sharpe_ratio = round((avg_r / std_r) * math.sqrt(8760), 4) if std_r > 0 else 0.0
        # Sortino: downside deviation
        down_std = math.sqrt(sum(r ** 2 for r in down_returns) / len(down_returns)) if down_returns else 0.0001
        result.sortino_ratio = round((avg_r / down_std) * math.sqrt(8760), 4) if down_std > 0 else 0.0

    result.trades = [
        {
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "quantity": t.quantity,
            "reason": t.reason,
        }
        for t in trades
    ]
    result.equity_curve = equity_curve

    return result


# ─── Multi-Strategy Compare ───────────────────────────────────────────────────

def compare_strategies(
    prices: list[float],
    initial_capital: float = 10000.0,
    symbol: str = "BTCUSDT",
    stop_loss_pct: float = 5.0,
    position_size_usdt: float = 1000.0,
) -> list[BacktestResult]:
    """
    Run all three strategy variants on the same price data and return
    a sorted list of BacktestResults (best Sharpe first).
    """
    strategies = [
        RSIStrategy(stop_loss_pct=stop_loss_pct, position_size_usdt=position_size_usdt),
        RSI_MACD_Strategy(stop_loss_pct=stop_loss_pct, position_size_usdt=position_size_usdt),
        RSI_BB_Strategy(stop_loss_pct=stop_loss_pct, position_size_usdt=position_size_usdt),
    ]
    results = []
    for s in strategies:
        r = run_backtest(prices, s, initial_capital=initial_capital, symbol=symbol)
        results.append(r)
    results.sort(key=lambda x: x.sharpe_ratio, reverse=True)
    return results


# ─── Parameter Sweep (Optimization) ──────────────────────────────────────────

def param_sweep(
    prices: list[float],
    strategy_cls=RSIStrategy,
    param_grid: Optional[dict] = None,
    initial_capital: float = 10000.0,
    top_n: int = 5,
) -> list[tuple[float, str, dict, BacktestResult]]:
    """
    Grid search over parameter combinations.
    Returns top N by sharpe ratio.
    """
    if param_grid is None:
        param_grid = {
            "period": [6, 10, 14, 20],
            "buy_threshold": [20, 25, 30, 35],
            "sell_threshold": [65, 70, 75, 80],
            "stop_loss_pct": [3, 5, 8],
            "position_size_usdt": [1000],
        }

    import itertools
    keys = list(param_grid.keys())
    results: list[tuple[float, str, dict, BacktestResult]] = []

    total = 1
    for v in param_grid.values():
        total *= len(v)
    if total > 500:
        log.warning("Parameter grid has %d combos — limiting to 500", total)

    count = 0
    for values in itertools.product(*[param_grid[k] for k in keys]):
        if count >= 500:
            break
        kwargs = dict(zip(keys, values))
        strategy = strategy_cls(**kwargs)
        result = run_backtest(prices, strategy, initial_capital=initial_capital)
        score = result.sharpe_ratio if result.sharpe_ratio != 0 else result.total_return_pct
        results.append((score, result.summary(), kwargs, result))
        count += 1
        if count % 100 == 0:
            log.info("  Swept %d/%d combos...", count, min(total, 500))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_n]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto Trading Backtester")
    parser.add_argument(
        "--data", type=str, default=None,
        help="Path to CSV file with historical prices (optional, uses synthetic data otherwise)",
    )
    parser.add_argument("--length", type=int, default=1000, help="Number of price bars")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for price generation")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital (USDT)")
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--stop-loss", type=float, default=5, help="Stop loss %%")

    # Single-strategy mode
    parser.add_argument("--strategy", type=str, default="rsi",
                        choices=["rsi", "rsi_macd", "rsi_bb"],
                        help="Strategy to use (default: rsi)")
    parser.add_argument("--period", type=int, default=14, help="RSI period")
    parser.add_argument("--buy", type=float, default=30, help="RSI buy threshold")
    parser.add_argument("--sell", type=float, default=70, help="RSI sell threshold")
    parser.add_argument("--bb-period", type=int, default=20, help="Bollinger Band period")
    parser.add_argument("--bb-std", type=float, default=2.0, help="Bollinger Band std multiplier")
    parser.add_argument("--macd-fast", type=int, default=12, help="MACD fast period")
    parser.add_argument("--macd-slow", type=int, default=26, help="MACD slow period")
    parser.add_argument("--macd-signal", type=int, default=9, help="MACD signal period")

    parser.add_argument("--compare", action="store_true",
                        help="Compare all 3 strategies on same data (ignores --strategy)")
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep (RSI only)")
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON")

    args = parser.parse_args()

    # Load data
    if args.data:
        log.info("Loading prices from %s ...", args.data)
        prices = load_historical_klines(args.data)
        log.info("Loaded %d price points", len(prices))
    else:
        log.info("Generating synthetic price series (seed=%d, n=%d) ...", args.seed, args.length)
        prices = generate_price_series(length=args.length, seed=args.seed)
        log.info("Generated %d price points", len(prices))

    if args.compare:
        log.info("Running multi-strategy comparison ...\n")
        results = compare_strategies(
            prices,
            initial_capital=args.capital,
            symbol=args.symbol,
            stop_loss_pct=args.stop_loss,
        )
        log.info("%s", "=" * 72)
        log.info("  Strategy Comparison — %d price bars, Sharpe sorted", len(prices))
        log.info("%s", "=" * 72)
        for i, r in enumerate(results):
            log.info("\n  #%d: %s", i + 1, r.summary().replace("=" * 56, ""))
            log.info("  → Return: %.2f%% | Sharpe: %.4f | WinRate: %.1f%% | Trades: %d",
                     r.total_return_pct, r.sharpe_ratio, r.win_rate, r.num_trades)

        if args.output:
            # Save all results as list
            with open(args.output, "w") as f:
                json.dump([asdict(r) for r in results], f, indent=2, default=str)
            log.info("Comparison results saved to %s", args.output)

    elif args.sweep:
        log.info("Running parameter sweep ...\n")
        top = param_sweep(prices, initial_capital=args.capital)
        log.info("\n%s", "=" * 56)
        log.info("  Top %d RSI Parameter Combinations", len(top))
        log.info("%s", "=" * 56)
        for i, (score, summary, params, result) in enumerate(top):
            log.info("\n  #%d — period=%d buy=%.0f sell=%.0f sl=%.0f%%  Sharpe=%.4f  Ret=%.2f%%",
                     i + 1,
                     params.get("period", 14),
                     params.get("buy_threshold", 30),
                     params.get("sell_threshold", 70),
                     params.get("stop_loss_pct", 5),
                     score, result.total_return_pct)
        if args.output:
            top[0][3].to_json(args.output)

    else:
        # Single strategy run
        if args.strategy == "rsi":
            strategy = RSIStrategy(
                period=args.period,
                buy_threshold=args.buy,
                sell_threshold=args.sell,
                stop_loss_pct=args.stop_loss,
                position_size_usdt=args.capital / 10,
            )
        elif args.strategy == "rsi_macd":
            strategy = RSI_MACD_Strategy(
                rsi_period=args.period,
                rsi_buy=args.buy,
                rsi_sell=args.sell,
                macd_fast=args.macd_fast,
                macd_slow=args.macd_slow,
                macd_signal=args.macd_signal,
                stop_loss_pct=args.stop_loss,
                position_size_usdt=args.capital / 10,
            )
        elif args.strategy == "rsi_bb":
            strategy = RSI_BB_Strategy(
                rsi_period=args.period,
                rsi_buy=args.buy,
                rsi_sell=args.sell,
                bb_period=args.bb_period,
                bb_std=args.bb_std,
                stop_loss_pct=args.stop_loss,
                position_size_usdt=args.capital / 10,
            )
        else:
            log.error("Unknown strategy: %s", args.strategy)
            sys.exit(1)

        result = run_backtest(prices, strategy, initial_capital=args.capital, symbol=args.symbol)
        log.info("\n%s", result.summary())

        if args.output:
            result.to_json(args.output)


if __name__ == "__main__":
    main()