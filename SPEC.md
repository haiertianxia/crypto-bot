# Minimal Crypto Trading System — SPEC v2

## Project Overview
- **Name**: crypto-bot
- **Type**: Automated trading system (paper/live)
- **Core**: RSI mean-reversion bot + Web dashboard + WeChat notifications

## Modes

| Mode | Env | Description |
|---|---|---|
| SIMULATION (default) | `SIMULATE_MODE=1` | Offline price simulator, no network |
| TESTNET | `SIMULATE_MODE=1` + API keys | Binance testnet, real orders |
| LIVE | `SIMULATE_MODE=0` + API keys | Real Binance spot, real money |

## Tech Stack
- Python 3 / Flask / SQLite
- Binance API (testnet or real)
- PushPlus (WeChat personal notifications)

## File Structure
```
crypto-bot/
├── config.py      # All settings via env vars
├── bot.py         # Trading loop + notifications
├── market.py      # Simulated GBM price engine
├── exchange.py    # Binance API (signed) + simulate mode
├── database.py    # SQLite CRUD
├── notify.py      # PushPlus WeChat + WeCom webhook
├── dashboard.py   # Flask web UI (port 5050)
├── templates/     # Dashboard HTML
├── trading.db     # SQLite database
├── requirements.txt
└── README.md
```

## Features
- [x] RSI(14) strategy: buy RSI<30, sell RSI>70
- [x] Stop loss (configurable %)
- [x] SQLite trade history + equity curve
- [x] Flask dashboard (Dashboard / Trades / Logs / Settings)
- [x] Simulated price engine (offline)
- [x] Real Binance API integration (signed, HMAC)
- [x] WeChat PushPlus notifications (personal WeChat)
- [x] Environment-variable configuration

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `SIMULATE_MODE` | `1` | 1=simulate 0=live |
| `BINAKE_API_KEY` | — | Binance API key |
| `BINAKE_API_SECRET` | — | Binance API secret |
| `PUSHPLUS_TOKEN` | — | PushPlus token |
| `PUSH_ENABLED` | `0` | 1=enable WeChat push |
| `SYMBOL` | `BTCUSDT` | Trading pair |
| `RSI_PERIOD` | `14` | RSI lookback |
| `RSI_BUY_THRESHOLD` | `30` | Buy signal |
| `RSI_SELL_THRESHOLD` | `70` | Sell signal |
| `POSITION_SIZE` | `100` | USDT per trade |
