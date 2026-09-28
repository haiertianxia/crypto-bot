"""
Main trading bot — run loop, signal generation, order execution + notifications.

Strategy modes (via STRATEGY_MODE env var):
  rsi       : classic RSI only (RSI<buy → BUY, RSI>sell → SELL)
  rsi_macd  : RSI signal + MACD histogram cross confirmation
  rsi_bb    : RSI signal + Bollinger Band touch confirmation
"""

import time
import logging
import logging.handlers  # RotatingFileHandler for safe log rotation
import threading
import argparse
from datetime import datetime

from config import (
    SYMBOL, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD,
    POSITION_SIZE, STOP_LOSS_PCT, INITIAL_CAPITAL,
    PRICE_FETCH_INTERVAL, DB_PATH, LOG_PATH, MODE,
    STRATEGY_MODE,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD,
)
import database
import exchange
import notify

# ─── Logging ─────────────────────────────────────────────────────────────────

# RotatingFileHandler: 10 MB per file, keep 3 backups
rotating = logging.handlers.RotatingFileHandler(
    LOG_PATH,
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        rotating,
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot")


# ─── Bot State ────────────────────────────────────────────────────────────────

class BotState:
    def __init__(self):
        self.cash = INITIAL_CAPITAL
        self.position = 0.0
        self.entry_price = 0.0
        self.open_trade_id = None
        self.running = False
        self._lock = threading.Lock()
        # For MACD cross detection: store prior MACD histogram
        self._prev_macd_hist: float | None = None

    @property
    def equity(self) -> float:
        with self._lock:
            return self.cash + self.position * exchange.get_price(SYMBOL)

    def equity_now(self, price: float) -> float:
        with self._lock:
            return self.cash + self.position * price


state = BotState()


# ─── Indicator Helpers ───────────────────────────────────────────────────────

def _get_indicators(closes: list[float]):
    """Return (rsi, macd_tuple, bb_tuple) for the given close series."""
    from market import calculate_rsi, calculate_macd, calculate_bollinger_bands
    rsi = calculate_rsi(closes, RSI_PERIOD)
    macd = calculate_macd(closes, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    bb = calculate_bollinger_bands(closes, period=BB_PERIOD, num_std=BB_STD)
    return rsi, macd, bb


def _macd_histogram_crossed(prev_hist: float | None, curr_hist: float) -> str | None:
    """
    Detect MACD histogram crossing zero.
    Returns 'bullish' if histogram crossed from negative to positive,
            'bearish' if histogram crossed from positive to negative,
            None if no cross.
    prev_hist may be None on first call.
    """
    if prev_hist is None:
        return None
    if prev_hist < 0 and curr_hist >= 0:
        return "bullish"
    if prev_hist > 0 and curr_hist <= 0:
        return "bearish"
    return None


# ─── Dual-Indicator Signal Generation ────────────────────────────────────────

def _should_buy(rsi: float, macd: tuple | None, bb: tuple | None,
                prev_macd_hist: float | None, current_price: float) -> tuple[bool, str]:
    """
    Determine if a BUY signal is active, using dual confirmation.
    Returns (should_buy, reason).
    """
    if STRATEGY_MODE == "rsi":
        if rsi and rsi < RSI_BUY_THRESHOLD:
            return True, f"RSI oversold ({rsi:.2f} < {RSI_BUY_THRESHOLD})"
        return False, ""

    elif STRATEGY_MODE == "rsi_macd":
        # BUY: RSI oversold AND MACD histogram crossed bullish
        if not (rsi and rsi < RSI_BUY_THRESHOLD):
            return False, ""
        if macd is None:
            return False, ""
        curr_hist = macd[2]
        cross = _macd_histogram_crossed(prev_macd_hist, curr_hist)
        if cross == "bullish":
            return True, f"RSI oversold ({rsi:.2f}) + MACD bullish cross (hist={curr_hist:+.4f})"
        return False, f"RSI ok ({rsi:.2f}) but no MACD cross yet (hist={curr_hist:+.4f})"

    elif STRATEGY_MODE == "rsi_bb":
        # BUY: RSI oversold AND price below lower Bollinger Band
        if not (rsi and rsi < RSI_BUY_THRESHOLD):
            return False, ""
        if bb is None:
            return False, ""
        lower = bb[2]
        if current_price <= lower:
            return True, f"RSI oversold ({rsi:.2f}) + price below BB lower ({current_price:.2f} <= {lower:.2f})"
        return False, f"RSI ok ({rsi:.2f}) but price above BB lower ({current_price:.2f} > {lower:.2f})"

    return False, ""


def _should_sell(rsi: float, macd: tuple | None, bb: tuple | None,
                 prev_macd_hist: float | None, current_price: float) -> tuple[bool, str]:
    """
    Determine if a SELL signal is active (for closing a long position).
    Returns (should_sell, reason).
    Also called with entry_price for stop-loss check.
    """
    if STRATEGY_MODE == "rsi":
        if rsi and rsi > RSI_SELL_THRESHOLD:
            return True, f"RSI overbought ({rsi:.2f} > {RSI_SELL_THRESHOLD})"
        return False, ""

    elif STRATEGY_MODE == "rsi_macd":
        # SELL: RSI overbought AND MACD histogram crossed bearish
        if not (rsi and rsi > RSI_SELL_THRESHOLD):
            return False, ""
        if macd is None:
            return False, ""
        curr_hist = macd[2]
        cross = _macd_histogram_crossed(prev_macd_hist, curr_hist)
        if cross == "bearish":
            return True, f"RSI overbought ({rsi:.2f}) + MACD bearish cross (hist={curr_hist:+.4f})"
        return False, f"RSI ok ({rsi:.2f}) but no MACD cross yet (hist={curr_hist:+.4f})"

    elif STRATEGY_MODE == "rsi_bb":
        # SELL: RSI overbought AND price above upper Bollinger Band
        if not (rsi and rsi > RSI_SELL_THRESHOLD):
            return False, ""
        if bb is None:
            return False, ""
        upper = bb[0]
        if current_price >= upper:
            return True, f"RSI overbought ({rsi:.2f}) + price above BB upper ({current_price:.2f} >= {upper:.2f})"
        return False, f"RSI ok ({rsi:.2f}) but price below BB upper ({current_price:.2f} < {upper:.2f})"

    return False, ""


# ─── Trading Logic ────────────────────────────────────────────────────────────

def check_and_trade():
    """每 tick 执行一次：获取行情 → 计算指标 → 生成信号 → 执行交易。"""
    try:
        snap = exchange.get_ticker_24hr(SYMBOL)
        # Need enough bars for the longest indicator chain
        max_needed = max(
            RSI_PERIOD + 1,
            MACD_SLOW + MACD_SIGNAL,
            BB_PERIOD,
        )
        closes = [k[4] for k in exchange.get_klines(SYMBOL, "1h", max_needed)]
        rsi, macd, bb = _get_indicators(closes)
    except Exception as e:
        log.warning("Failed to fetch market data: %s", e)
        return

    price = snap["price"]
    curr_macd_hist = macd[2] if macd else None

    log.info(
        "📊 [%s/%s] %s  price=%.2f  RSI(%.0f)=%s  MACD_hist=%s  BB_lower=%.2f  cash=%.2f  pos=%.6f",
        MODE, STRATEGY_MODE, SYMBOL, price,
        float(RSI_PERIOD), f"{rsi:.2f}" if rsi else "N/A",
        f"{curr_macd_hist:+.4f}" if curr_macd_hist is not None else "N/A",
        bb[2] if bb else 0.0,
        state.cash, state.position,
    )

    open_trade = database.get_last_open_trade(SYMBOL)

    # ── SELL ─────────────────────────────────────────────────────────────────
    if open_trade:
        should_sell, reason = _should_sell(
            rsi, macd, bb,
            state._prev_macd_hist, price,
        )

        # Also check stop loss independently (always active)
        entry = open_trade["entry_price"]
        if STOP_LOSS_PCT > 0:
            loss_pct = (price - entry) / entry * 100
            if loss_pct <= -STOP_LOSS_PCT:
                should_sell = True
                reason = f"Stop loss ({loss_pct:.2f}%)"

        if should_sell:
            qty = open_trade["quantity"]
            result = exchange.place_order(
                SYMBOL, "SELL", "MARKET", quantity=qty,
            )
            pnl = (price - entry) * qty
            pnl_pct = (price - entry) / entry * 100
            database.close_trade(open_trade["id"], price, pnl, pnl_pct)
            with state._lock:
                state.cash += qty * price
                state.position = 0.0
            log.info(
                "🔴 SELL  qty=%.6f  price=%.2f  PnL=%.2f (%.2f%%)  reason=%s",
                qty, price, pnl, pnl_pct, reason,
            )
            database.log_event("SELL", reason)
            notify.send_trade_notification(
                side="SELL", symbol=SYMBOL, price=price,
                quantity=qty, pnl=pnl, entry_price=entry,
                reason=reason,
            )

    # ── BUY ──────────────────────────────────────────────────────────────────
    elif not open_trade:
        should_buy, reason = _should_buy(
            rsi, macd, bb,
            state._prev_macd_hist, price,
        )

        if should_buy:
            qty = POSITION_SIZE / price
            cost = qty * price
            if cost <= state.cash:
                result = exchange.place_order(
                    SYMBOL, "BUY", "MARKET", quantity=qty,
                )
                trade_id = database.insert_trade(
                    SYMBOL, "BUY", price, qty,
                    notes=f"[{STRATEGY_MODE}] {reason}",
                )
                with state._lock:
                    state.cash -= cost
                    state.position += qty
                    state.entry_price = price
                    state.open_trade_id = trade_id
                log.info(
                    "🟢 BUY   qty=%.6f  price=%.2f  cost=%.2f  %s",
                    qty, price, cost, reason,
                )
                database.log_event("BUY", f"[{STRATEGY_MODE}] {reason}")
                notify.send_trade_notification(
                    side="BUY", symbol=SYMBOL, price=price,
                    quantity=qty,
                    reason=f"[{STRATEGY_MODE}] {reason}",
                )
            else:
                log.warning("Insufficient cash: need %.2f, have %.2f", cost, state.cash)

    # ── Update MACD state ───────────────────────────────────────────────────
    if curr_macd_hist is not None:
        state._prev_macd_hist = curr_macd_hist

    # ── Equity Log ─────────────────────────────────────────────────────────
    try:
        pos_val = state.position * price
        database.log_equity(state.equity_now(price), state.cash, pos_val, price)
    except Exception as e:
        log.warning("Equity log failed: %s", e)


# ─── Run Loop ─────────────────────────────────────────────────────────────────

def run_loop(interval: int = PRICE_FETCH_INTERVAL):
    log.info("🚀 Bot started [%s / %s]", MODE, STRATEGY_MODE)
    log.info(
        "   Symbol: %s  Mode=%s  RSI(%d)  Buy<%.0f  Sell>%.0f  PosSize=%.0f USDT  StopLoss=%.1f%%  Interval=%ds",
        SYMBOL, STRATEGY_MODE, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD,
        POSITION_SIZE, STOP_LOSS_PCT, interval,
    )
    database.log_event("BOT_START", f"Mode={MODE} Strategy={STRATEGY_MODE} Interval={interval}s")
    notify.send_alert(f"🤖 Bot 已启动 [{MODE}/{STRATEGY_MODE}]\n"
                      f"交易对: {SYMBOL} | 间隔: {interval}s",
                      level="INFO")

    while state.running:
        try:
            check_and_trade()
        except Exception as e:
            log.error("Tick error: %s", e, exc_info=True)
            notify.send_alert(f"❌ Tick 异常: {e}", level="ERROR")

        for _ in range(interval):
            if not state.running:
                break
            time.sleep(1)

    log.info("Bot stopped.")
    notify.send_alert(f"⏹ Bot 已停止", level="WARN")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Test market data only")
    parser.add_argument("--interval", type=int, default=PRICE_FETCH_INTERVAL)
    args = parser.parse_args()

    database.init_db()
    log.info("Database initialized at %s", DB_PATH)

    if args.dry_run:
        snap = exchange.get_ticker_24hr(SYMBOL)
        max_needed = max(
            RSI_PERIOD + 1,
            MACD_SLOW + MACD_SIGNAL,
            BB_PERIOD,
        )
        closes = [k[4] for k in exchange.get_klines(SYMBOL, "1h", max_needed)]
        rsi, macd, bb = _get_indicators(closes)
        print(f"\n=== Market Snapshot [{MODE} / {STRATEGY_MODE}] ===")
        print(f"  Price:       ${snap['price']:,.2f}")
        print(f"  24h High:    ${snap['high']:,.2f}")
        print(f"  24h Low:     ${snap['low']:,.2f}")
        print(f"  RSI({RSI_PERIOD}):       {rsi:.2f}" if rsi else "  RSI: N/A")
        print(f"  MACD:        {macd[0]:+.4f} / signal={macd[1]:+.4f} / hist={macd[2]:+.4f}" if macd else "  MACD: N/A")
        if bb:
            print(f"  BB({BB_PERIOD},{BB_STD}):  upper={bb[0]:.2f}  mid={bb[1]:.2f}  lower={bb[2]:.2f}")
        else:
            print("  BB: N/A")
        print(f"\n  Strategy:    {STRATEGY_MODE}")
        print(f"  Mode:        {MODE}")
        print(f"\n✅ Dry-run OK — bot ready")
        return

    state.running = True
    try:
        run_loop(interval=args.interval)
    except KeyboardInterrupt:
        state.running = False
        log.info("Interrupted — shutting down")


if __name__ == "__main__":
    main()