"""
Backtesting engine for crypto trading strategies.
Simulates trades over historical or generated price data and reports detailed metrics.
"""

import argparse
import json
import logging
import math
import random
import sys
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
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 56,
            f"  Backtest Results — {self.symbol}",
            "=" * 56,
            f"  Initial Capital:   ${self.start_capital:>10,.2f}",
            f"  Final Capital:     ${self.end_capital:>10,.2f}",
            f"  Total Return:      ${self.total_return:>+10,.2f}  ({self.total_return_pct:+.2f}%)",
            f"  Max Drawdown:      ${self.max_drawdown:>10,.2f}  ({self.max_drawdown_pct:.2f}%)",
            f"  Sharpe Ratio:      {self.sharpe_ratio:>10.4f}",
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


# ─── RSI Strategy (default, matches bot.py logic) ────────────────────────────

class RSIStrategy:
    def __init__(
        self,
        period: int = 14,
        buy_threshold: float = 30.0,
        sell_threshold: float = 70.0,
        stop_loss_pct: float = 5.0,
        position_size_usdt: float = 1000.0,
    ):
        self.period = period
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.stop_loss_pct = stop_loss_pct
        self.position_size_usdt = position_size_usdt

    def calculate_rsi(self, closes: list[float]) -> float | None:
        """Wilder's smoothed RSI."""
        if len(closes) < self.period + 1:
            return None
        closes = [float(c) for c in closes]
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[: self.period]) / self.period
        avg_loss = sum(losses[: self.period]) / self.period
        if avg_loss == 0:
            return 100.0
        for i in range(self.period, len(gains)):
            avg_gain = (avg_gain * (self.period - 1) + gains[i]) / self.period
            avg_loss = (avg_loss * (self.period - 1) + losses[i]) / self.period
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 4)

    def generate_signals(self, prices: list[float]) -> list[dict]:
        """Generate buy/sell signals over the price series."""
        signals = []
        in_position = False
        entry_price = 0.0

        for i in range(len(prices)):
            lookback = prices[: i + 1]
            rsi = self.calculate_rsi(lookback)
            if rsi is None:
                continue

            if not in_position and rsi < self.buy_threshold:
                signals.append({
                    "time": i,
                    "price": prices[i],
                    "action": "BUY",
                    "reason": f"RSI={rsi:.2f} < {self.buy_threshold}",
                })
                in_position = True
                entry_price = prices[i]

            elif in_position:
                # Check stop loss
                if self.stop_loss_pct > 0:
                    loss_pct = (prices[i] - entry_price) / entry_price * 100
                    if loss_pct <= -self.stop_loss_pct:
                        signals.append({
                            "time": i,
                            "price": prices[i],
                            "action": "SELL",
                            "reason": f"Stop loss ({loss_pct:.2f}%)",
                        })
                        in_position = False
                        continue

                # Check RSI sell signal
                if rsi > self.sell_threshold:
                    signals.append({
                        "time": i,
                        "price": prices[i],
                        "action": "SELL",
                        "reason": f"RSI={rsi:.2f} > {self.sell_threshold}",
                    })
                    in_position = False

        # Force close at end
        if in_position and len(signals) > 0 and signals[-1]["action"] == "BUY":
            signals.append({
                "time": len(prices) - 1,
                "price": prices[-1],
                "action": "SELL",
                "reason": "End of data",
            })

        return signals


# ─── Backtest Engine ──────────────────────────────────────────────────────────

def run_backtest(
    prices: list[float],
    strategy: RSIStrategy,
    initial_capital: float = 10000.0,
    symbol: str = "BTCUSDT",
) -> BacktestResult:
    """
    Run a full backtest over price data with the given strategy.
    Returns a detailed BacktestResult.
    """
    signals = strategy.generate_signals(prices)
    result = BacktestResult(
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

    for i, sig in enumerate(signals):
        price = sig["price"]
        action = sig["action"]

        if action == "BUY" and open_trade is None:
            qty = strategy.position_size_usdt / price
            cost = qty * price
            if cost <= cash:
                cash -= cost
                position += qty
                open_trade = Trade(
                    entry_time=i,
                    entry_price=price,
                    side="LONG",
                    quantity=qty,
                    reason=sig["reason"],
                )

        elif action == "SELL" and open_trade is not None:
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
        equity_curve.append({"step": i, "equity": round(equity, 2), "price": price})
        if equity > peak:
            peak = equity

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
    max_dd = 0
    max_dd_pct = 0
    for point in equity_curve:
        if point["equity"] > peak_equity:
            peak_equity = point["equity"]
        dd = peak_equity - point["equity"]
        dd_pct = dd / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct
    result.max_drawdown = round(max_dd, 2)
    result.max_drawdown_pct = round(max_dd_pct, 2)

    # Sharpe ratio (annualized, assuming 1h bars)
    if len(equity_curve) > 1:
        returns = []
        for j in range(1, len(equity_curve)):
            r = (equity_curve[j]["equity"] - equity_curve[j - 1]["equity"]) / equity_curve[j - 1]["equity"]
            returns.append(r)
        avg_r = sum(returns) / len(returns)
        var_r = sum((r - avg_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.0001
        # Annualize: 8760 hours in a year
        result.sharpe_ratio = round((avg_r / std_r) * math.sqrt(8760), 4) if std_r > 0 else 0.0

    # Convert trades to dicts
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


# ─── Parameter Sweep (Optimization) ──────────────────────────────────────────

def param_sweep(
    prices: list[float],
    strategy_cls=RSIStrategy,
    param_grid: Optional[dict] = None,
    initial_capital: float = 10000.0,
    top_n: int = 5,
) -> list[tuple[float, dict]]:
    """
    Grid search over parameter combinations.
    Returns top N by sharpe ratio (or total return if sharpe ties).
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
    results: list[tuple[float, dict]] = []

    # Limit to 500 combos max
    total = 1
    for v in param_grid.values():
        total *= len(v)
    if total > 500:
        log.warning("Parameter grid has %d combos — limiting to 500", total)

    count = 0
    for values in itertools.product(*[param_grid[k] for k in keys]):
        if count >= 500:
            break
        params = dict(zip(keys, values))
        strategy = strategy_cls(**params)
        result = run_backtest(prices, strategy, initial_capital)
        # Score by sharpe, fallback to return pct
        score = result.sharpe_ratio if result.sharpe_ratio != 0 else result.total_return_pct
        results.append((score, result.summary(), params, result))
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

    parser.add_argument("--period", type=int, default=14, help="RSI period")
    parser.add_argument("--buy", type=float, default=30, help="RSI buy threshold")
    parser.add_argument("--sell", type=float, default=70, help="RSI sell threshold")
    parser.add_argument("--stop-loss", type=float, default=5, help="Stop loss %")
    parser.add_argument("--position-size", type=float, default=1000, help="Position size USDT")

    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")
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

    if args.sweep:
        log.info("Running parameter sweep ...\n")
        top = param_sweep(prices, initial_capital=args.capital)
        log.info("\n%s", "=" * 56)
        log.info("  Top %d Parameter Combinations", len(top))
        log.info("%s", "=" * 56)
        for i, (score, summary, params, result) in enumerate(top):
            log.info("\n  #%d — RSI(period=%d, buy=%.0f, sell=%.0f, sl=%.0f%%)  Sharpe=%.4f",
                     i + 1,
                     params.get("period", 14),
                     params.get("buy_threshold", 30),
                     params.get("sell_threshold", 70),
                     params.get("stop_loss_pct", 5),
                     score)

        if args.output:
            top[0][3].to_json(args.output)
    else:
        strategy = RSIStrategy(
            period=args.period,
            buy_threshold=args.buy,
            sell_threshold=args.sell,
            stop_loss_pct=args.stop_loss,
            position_size_usdt=args.position_size,
        )
        result = run_backtest(prices, strategy, initial_capital=args.capital, symbol=args.symbol)
        log.info("\n%s", result.summary())

        if args.output:
            result.to_json(args.output)


if __name__ == "__main__":
    main()
