"""
Configuration for Crypto Trading Bot
All settings in one place — edit this file to customize the bot.

启动模式（设置环境变量切换）：
  SIMULATE_MODE=1           → 模拟引擎（离线，默认）
  SIMULATE_MODE=1 + Key     → Binance Testnet
  SIMULATE_MODE=0 + Key     → Binance 真实现货（危险！）

  export SIMULATE_MODE=0
  export BINAKE_API_KEY="your_key"
  export BINAKE_API_SECRET="your_secret"

微信通知（PushPlus）：
  export PUSHPLUS_TOKEN="your_token"   # https://pushplus.plus 注册
  export PUSH_ENABLED=1
"""

import os

# ─── Exchange ────────────────────────────────────────────────────────────────
SIMULATE_MODE  = os.environ.get("SIMULATE_MODE", "1") == "1"
BASE_URL       = "https://testnet.binance.vision" if SIMULATE_MODE else "https://api.binance.com"
API_KEY        = os.environ.get("BINAKE_API_KEY", "")
API_SECRET     = os.environ.get("BINAKE_API_SECRET", "")

# ─── Trading ─────────────────────────────────────────────────────────────────
SYMBOL              = os.environ.get("SYMBOL", "BTCUSDT")
RSI_PERIOD          = int(os.environ.get("RSI_PERIOD", "14"))
RSI_BUY_THRESHOLD   = float(os.environ.get("RSI_BUY_THRESHOLD", "30"))
RSI_SELL_THRESHOLD  = float(os.environ.get("RSI_SELL_THRESHOLD", "70"))
INITIAL_CAPITAL     = float(os.environ.get("INITIAL_CAPITAL", "10000"))
POSITION_SIZE       = float(os.environ.get("POSITION_SIZE", "100"))   # USDT per trade
STOP_LOSS_PCT       = float(os.environ.get("STOP_LOSS_PCT", "2.0"))

# ─── Market Data ─────────────────────────────────────────────────────────────
PRICE_FETCH_INTERVAL = int(os.environ.get("INTERVAL", "30"))

# ─── Dashboard ──────────────────────────────────────────────────────────────
DASHBOARD_PORT = 5050
DB_PATH        = "trading.db"
LOG_PATH       = "bot.log"

# ─── Notifications ─────────────────────────────────────────────────────────
# PushPlus: https://pushplus.plus/ （个人微信扫码绑定）
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
PUSH_ENABLED   = os.environ.get("PUSH_ENABLED", "0") == "1"
# 企业微信机器人 webhook（备选）
WECOM_WEBHOOK_URL = os.environ.get("WECOM_WEBHOOK_URL", "")

# ─── Mode Label ─────────────────────────────────────────────────────────────
def mode_label() -> str:
    if SIMULATE_MODE:
        return "SIMULATION"
    return "TESTNET" if "testnet" in BASE_URL else "LIVE ⚠️ REAL MONEY"

MODE = mode_label()
