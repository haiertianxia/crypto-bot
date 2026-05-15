"""
微信通知模块 — PushPlus + 备用方案。
用法：
    from notify import send_trade_notification
    send_trade_notification("BUY", "BTCUSDT", 67000, 0.00149, pnl=23.5)

配置（在 config.py 或环境变量）：
    PUSHPLUS_TOKEN  — PushPlus 令牌（https://pushplus.plus 注册获取）
    PUSH_ENABLED    — 1 开启推送，0 关闭
"""

import os
import requests
import logging
from datetime import datetime

log = logging.getLogger("notify")

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
PUSH_ENABLED = os.environ.get("PUSH_ENABLED", "0") == "1"
PUSHPLUS_URL = "https://www.pushplus.plus/send"

_BOT_NAME = "🐾 CryptoBot"


def _pushplus_send(title: str, content: str) -> bool:
    """通过 PushPlus 发送微信消息。返回是否成功。"""
    if not PUSH_ENABLED or not PUSHPLUS_TOKEN:
        log.debug("PushPlus disabled or no token — skip")
        return False
    try:
        r = requests.get(PUSHPLUS_URL, params={
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": content,
            "template": "html",
        }, timeout=10)
        if r.status_code == 200 and "200" in r.text:
            log.info("PushPlus notification sent: %s", title)
            return True
        else:
            log.warning("PushPlus failed: %s", r.text[:100])
            return False
    except Exception as e:
        log.warning("PushPlus request failed: %s", e)
        return False


def _wecom_webhook_hook(content: str) -> bool:
    """
    企业微信机器人通知（备选）。
    设置环境变量 WECOM_WEBHOOK_URL 即可启用。
    """
    url = os.environ.get("WECOM_WEBHOOK_URL", "")
    if not url:
        return False
    try:
        r = requests.post(url, json={
            "msgtype": "text",
            "text": {"content": content}
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.warning("WeCom webhook failed: %s", e)
        return False


def _fmt_price(p: float) -> str:
    return f"${p:,.2f}"


def send_trade_notification(
    side: str,          # "BUY" 或 "SELL"
    symbol: str,
    price: float,
    quantity: float,
    pnl: float | None = None,
    reason: str = "",
    entry_price: float | None = None,
):
    """
    发送交易通知到微信（PushPlus）+ 企业微信机器人（可选）。
    自动判断 BUY / SELL 样式。
    """
    emoji = "🟢" if side == "BUY" else "🔴"
    side_label = "买入提醒" if side == "BUY" else "卖出提醒"
    now = datetime.now().strftime("%m-%d %H:%M")

    # PnL 显示
    if pnl is not None:
        pnl_str = f'<font color="{"green" if pnl >= 0 else "red"}">{"+" if pnl >= 0 else ""}{_fmt_price(pnl)}</font>'
    else:
        pnl_str = "—"

    entry_str = f'<br>入场价：{_fmt_price(entry_price)}' if entry_price else ""

    title = f"{emoji} {side_label} {symbol}"
    content = f"""
<div style="font-size:15px;line-height:1.8">
  <b>{_BOT_NAME} · {now}</b><br>
  <hr style="border:none;border-top:1px solid #eee">
  <b>交易方向：</b>{emoji} {side}<br>
  <b>交易品种：</b>{symbol}<br>
  <b>执行价格：</b>{_fmt_price(price)}<br>
  <b>下单数量：</b>{quantity:.6f}<br>
  <b>合约价值：</b>{_fmt_price(price * quantity)}{entry_str}<br>
  <b>收益/亏损：</b>{pnl_str}<br>
  <b>信号原因：</b>{reason or "—"}
</div>
"""
    _pushplus_send(title, content)
    # 企业微信兜底
    summary = f"{emoji} {symbol} {side} @ {_fmt_price(price)}"
    if pnl is not None:
        summary += f" | PnL: {'+' if pnl >= 0 else ''}{pnl:.2f}"
    _wecom_webhook_hook(summary)


def send_alert(message: str, level: str = "INFO"):
    """发送告警消息（服务器错误、策略异常等）。"""
    emoji_map = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "🚨"}
    emoji = emoji_map.get(level, "📢")
    _pushplus_send(f"{emoji} Bot Alert [{level}]", f"<b>{_BOT_NAME}</b><br>{message}")
