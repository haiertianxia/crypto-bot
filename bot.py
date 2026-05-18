"""
Main trading bot — run loop, signal generation, order execution + notifications.
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

    @property
    def equity(self) -> float:
        with self._lock:
            return self.cash + self.position * exchange.get_price(SYMBOL)

    def equity_now(self, price: float) -> float:
        with self._lock:
            return self.cash + self.position * price


state = BotState()


# ─── Trading Logic ────────────────────────────────────────────────────────────

def check_and_trade():
    """每 tick 执行一次：获取行情 → 计算信号 → 执行交易。"""
    try:
        snap = exchange.get_ticker_24hr(SYMBOL)
        closes = [k[4] for k in exchange.get_klines(SYMBOL, "1h", 100)]
        from market import calculate_rsi
        rsi = calculate_rsi(closes, RSI_PERIOD)
    except Exception as e:
        log.warning("Failed to fetch market data: %s", e)
        return

    price = snap["price"]
    rsi_val = rsi

    log.info(
        "📊 [%s] %s  price=%.2f  RSI(%.0f)=%s  cash=%.2f  pos=%.6f",
        MODE, SYMBOL, price,
        float(RSI_PERIOD), f"{rsi_val:.2f}" if rsi_val else "N/A",
        state.cash, state.position,
    )

    open_trade = database.get_last_open_trade(SYMBOL)

    # ── SELL ─────────────────────────────────────────────────────────────────
    if open_trade:
        should_sell = False
        reason = ""

        if rsi_val and rsi_val > RSI_SELL_THRESHOLD:
            should_sell = True
            reason = f"RSI overbought ({rsi_val:.2f} > {RSI_SELL_THRESHOLD})"

        entry = open_trade["entry_price"]
        if STOP_LOSS_PCT > 0:
            loss_pct = (price - entry) / entry * 100
            if loss_pct <= -STOP_LOSS_PCT:
                should_sell = True
                reason = f"Stop loss ({loss_pct:.2f}%)"

        if should_sell:
            qty = open_trade["quantity"]
            # 真实下单
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
            # 微信通知
            notify.send_trade_notification(
                side="SELL", symbol=SYMBOL, price=price,
                quantity=qty, pnl=pnl, entry_price=entry,
                reason=reason,
            )

    # ── BUY ──────────────────────────────────────────────────────────────────
    elif not open_trade and rsi_val and rsi_val < RSI_BUY_THRESHOLD:
        qty = POSITION_SIZE / price
        cost = qty * price
        if cost <= state.cash:
            result = exchange.place_order(
                SYMBOL, "BUY", "MARKET", quantity=qty,
            )
            trade_id = database.insert_trade(
                SYMBOL, "BUY", price, qty,
                notes=f"RSI oversold ({rsi_val:.2f} < {RSI_BUY_THRESHOLD})",
            )
            with state._lock:
                state.cash -= cost
                state.position += qty
                state.entry_price = price
                state.open_trade_id = trade_id
            log.info(
                "🟢 BUY   qty=%.6f  price=%.2f  cost=%.2f  RSI=%.2f",
                qty, price, cost, rsi_val,
            )
            database.log_event("BUY", f"RSI={rsi_val:.2f}")
            notify.send_trade_notification(
                side="BUY", symbol=SYMBOL, price=price,
                quantity=qty, reason=f"RSI oversold ({rsi_val:.2f} < {RSI_BUY_THRESHOLD})",
            )
        else:
            log.warning("Insufficient cash: need %.2f, have %.2f", cost, state.cash)

    # ── Equity Log ─────────────────────────────────────────────────────────
    try:
        pos_val = state.position * price
        database.log_equity(state.equity_now(price), state.cash, pos_val, price)
    except Exception as e:
        log.warning("Equity log failed: %s", e)


# ─── Run Loop ─────────────────────────────────────────────────────────────────

def run_loop(interval: int = PRICE_FETCH_INTERVAL):
    log.info("🚀 Bot started [%s]", MODE)
    log.info(
        "   Symbol: %s  RSI(%d)  Buy<%.0f  Sell>%.0f  PosSize=%.0f USDT  Interval=%ds",
        SYMBOL, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD,
        POSITION_SIZE, interval,
    )
    database.log_event("BOT_START", f"Mode={MODE} Interval={interval}s")
    notify.send_alert(f"🤖 Bot 已启动 [{MODE}]\n"
                     f"交易对: {SYMBOL} | RSI({RSI_PERIOD}) | 间隔: {interval}s",
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
    parser = argparse.ArgumentParser(description="Crypto RSI Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Test market data only")
    parser.add_argument("--interval", type=int, default=PRICE_FETCH_INTERVAL)
    args = parser.parse_args()

    database.init_db()
    log.info("Database initialized at %s", DB_PATH)

    if args.dry_run:
        snap = exchange.get_ticker_24hr(SYMBOL)
        closes = [k[4] for k in exchange.get_klines(SYMBOL, "1h", 100)]
        from market import calculate_rsi
        rsi = calculate_rsi(closes, RSI_PERIOD)
        print(f"\n=== Market Snapshot [{MODE}] ===")
        print(f"  Price:    ${snap['price']:,.2f}")
        print(f"  24h High: ${snap['high']:,.2f}")
        print(f"  24h Low:  ${snap['low']:,.2f}")
        print(f"  RSI({RSI_PERIOD}):  {rsi:.2f}" if rsi else "  RSI: N/A")
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
