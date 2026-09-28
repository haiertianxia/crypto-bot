# Minimal Crypto Trading System — SPEC v3

## Project Overview
- **Name**: crypto-bot
- **Type**: Automated trading system (paper/live)
- **Core**: Multi-strategy bot + Web dashboard + WeChat notifications

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
├── bot.py         # Trading loop + notifications (multi-strategy)
├── market.py      # Simulated GBM price engine + all indicators
├── exchange.py    # Binance API (signed) + simulate mode
├── database.py    # SQLite CRUD
├── notify.py      # PushPlus WeChat + WeCom webhook
├── dashboard.py   # Flask web UI (port 5050)
├── backtest.py    # Backtesting engine with multi-strategy support
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
- [x] **Strategy modes** (STRATEGY_MODE env var):
  - `rsi`       — classic RSI mean-reversion only
  - `rsi_macd`  — RSI signal + MACD histogram cross dual confirmation
  - `rsi_bb`    — RSI signal + Bollinger Band touch dual confirmation
- [x] **Technical indicators**: RSI, MACD (line/signal/histogram), Bollinger Bands (upper/middle/lower)
- [x] **Backtest CLI** (`--compare`): compare all 3 strategies side-by-side on same data
- [x] **Backtest parameter sweep** (`--sweep`): grid-search RSI parameters
- [x] **Sharpe + Sortino ratios** in backtest results
- [x] **Backward compatible** — RSI-only mode remains default

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
| `RSI_BUY_THRESHOLD` | `30` | Buy signal (RSI below this) |
| `RSI_SELL_THRESHOLD` | `70` | Sell signal (RSI above this) |
| `STRATEGY_MODE` | `rsi` | Strategy: `rsi` \| `rsi_macd` \| `rsi_bb` |
| `MACD_FAST` | `12` | MACD fast EMA period |
| `MACD_SLOW` | `26` | MACD slow EMA period |
| `MACD_SIGNAL` | `9` | MACD signal line period |
| `BB_PERIOD` | `20` | Bollinger Band lookback period |
| `BB_STD` | `2.0` | Bollinger Band standard deviation multiplier |
| `POSITION_SIZE` | `100` | USDT per trade |
| `STOP_LOSS_PCT` | `2.0` | Stop loss percentage |

## Strategy Logic

### rsi (default)
- **BUY**: RSI < RSI_BUY_THRESHOLD
- **SELL**: RSI > RSI_SELL_THRESHOLD OR stop loss triggered

### rsi_macd (dual confirmation — reduces false signals)
- **BUY**: RSI oversold AND MACD histogram crosses bullish (negative → positive)
- **SELL**: RSI overbought AND MACD histogram crosses bearish (positive → negative)
- Stop loss always active

### rsi_bb (dual confirmation)
- **BUY**: RSI oversold AND price ≤ lower Bollinger Band
- **SELL**: RSI overbought AND price ≥ upper Bollinger Band
- Stop loss always active

## Backtest CLI

```bash
# Single strategy
python backtest.py --strategy rsi_macd --seed 42 --length 1000

# Compare all 3 strategies
python backtest.py --compare --length 2000 --seed 42

# Parameter sweep (RSI only)
python backtest.py --sweep --length 1000 --output results.json

# Load real data from CSV (column 4 = close price)
python backtest.py --data klines.csv --compare
```

## Dashboard API Changes (v3)

`/api/summary` now includes additional fields:
```json
{
  "market": {
    "rsi": 45.2,
    "macd_line": 123.4,
    "macd_signal": 118.2,
    "macd_histogram": 5.2,
    "bb_upper": 68500,
    "bb_middle": 67000,
    "bb_lower": 65500
  }
}
```