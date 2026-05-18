"""
Flask web dashboard.
"""

import os
import sqlite3
from flask import Flask, render_template, jsonify

import database
import exchange
from config import (
    DB_PATH, DASHBOARD_PORT, SYMBOL, RSI_PERIOD, RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD,
    MODE, STOP_LOSS_PCT, POSITION_SIZE,
)

app = Flask(__name__, template_folder="templates")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.route("/")
def index():
    return render_template(
        "index.html",
        symbol=SYMBOL,
        port=DASHBOARD_PORT,
        mode=MODE,
        simulate=(MODE == "SIMULATION"),
    )


@app.route("/api/summary")
def api_summary():
    snap = {}
    try:
        ticker = exchange.get_ticker_24hr(SYMBOL)
        closes = [k[4] for k in exchange.get_klines(SYMBOL, "1h", 100)]
        from market import calculate_rsi
        rsi = calculate_rsi(closes, RSI_PERIOD)
        snap = {
            "symbol": SYMBOL,
            "price": ticker["price"],
            "rsi": rsi,
            "high_24h": ticker["high"],
            "low_24h": ticker["low"],
            "volume_24h": ticker["quoteVolume"],
            "price_change_pct": ticker["priceChangePct"],
            "rsi_period": RSI_PERIOD,
        }
    except Exception as e:
        import logging
        logging.getLogger("dashboard").warning("api_summary market data error: %s", e)
        pass

    eq = database.get_latest_equity()
    stats = database.get_stats()
    open_trade = database.get_last_open_trade(SYMBOL)
    eq_history = database.get_equity_history(limit=500)

    return jsonify({
        "market": snap,
        "equity": eq,
        "stats": stats,
        "open_trade": open_trade,
        "equity_history": eq_history,
        "mode": MODE,
        "simulate": MODE == "SIMULATION",
        "stop_loss_pct": STOP_LOSS_PCT,
        "position_size": POSITION_SIZE,
    })


@app.route("/api/trades")
def api_trades():
    return jsonify(database.get_all_trades(limit=100))


@app.route("/api/logs")
def api_logs():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM config_log ORDER BY id DESC LIMIT 50")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(rows)


@app.route("/api/config")
def api_config():
    """Expose safe (non-secret) config for the dashboard."""
    return jsonify({
        "symbol": SYMBOL,
        "rsi_period": RSI_PERIOD,
        "rsi_buy": RSI_BUY_THRESHOLD,
        "rsi_sell": RSI_SELL_THRESHOLD,
        "stop_loss_pct": STOP_LOSS_PCT,
        "position_size": POSITION_SIZE,
        "mode": MODE,
        "simulate": MODE == "SIMULATION",
        "push_enabled": bool(os.environ.get("PUSH_ENABLED", "0") == "1"),
    })


if __name__ == "__main__":
    database.init_db()
    print(f"🌐 Dashboard [{MODE}] running at http://localhost:{DASHBOARD_PORT}")
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False, use_reloader=False)
