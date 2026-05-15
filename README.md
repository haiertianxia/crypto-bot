# 🐾 Crypto Bot — RSI Trading System

Paper/live crypto trading bot with real-time dashboard and WeChat notifications.

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Run dashboard
python3 dashboard.py      # → http://localhost:5050

# Run bot (separate terminal)
python3 bot.py
```

---

## Modes

### Simulation (default — offline)
```bash
python3 bot.py
# Works fully offline — no internet, no API keys
```

### Binance Testnet (paper trading with real exchange)
```bash
export SIMULATE_MODE=1
export BINAKE_API_KEY="your_testnet_key"
export BINAKE_API_SECRET="your_testnet_secret"
python3 bot.py
```

### Binance Live (real money ⚠️)
```bash
export SIMULATE_MODE=0
export BINAKE_API_KEY="your_key"
export BINAKE_API_SECRET="your_secret"
python3 bot.py
```

---

## WeChat Notifications (PushPlus)

1. Visit **pushplus.plus** → scan QR with WeChat → copy Token
2. Set env var:
   ```bash
   export PUSHPLUS_TOKEN="your_token"
   export PUSH_ENABLED=1
   ```
3. Restart bot — every trade will be pushed to your WeChat

---

## Strategy

| RSI Value | Action |
|---|---|
| < 30 | 🟢 BUY (oversold) |
| > 70 | 🔴 SELL (overbought) |

Stop loss: 2% (configurable via `STOP_LOSS_PCT`)

---

## Configuration

All settings via environment variables:

| Variable | Default | Description |
|---|---|---|
| `SYMBOL` | BTCUSDT | Trading pair |
| `RSI_PERIOD` | 14 | RSI lookback |
| `RSI_BUY_THRESHOLD` | 30 | Buy below this |
| `RSI_SELL_THRESHOLD` | 70 | Sell above this |
| `POSITION_SIZE` | 100 | USDT per trade |
| `INITIAL_CAPITAL` | 10000 | Starting balance |
| `STOP_LOSS_PCT` | 2.0 | Stop loss % (0 = off) |
| `INTERVAL` | 30 | Tick interval (seconds) |

---

## Dashboard Tabs

- **Dashboard** — price, RSI gauge, equity curve, open position
- **Trades** — full history with PnL
- **Logs** — bot activity log
- **Settings** — current config + switch guide

---

## Disclaimer

Paper trading only in simulation/testnet mode. Not financial advice.
