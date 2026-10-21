from typing import Any, Dict

from lib import slack, telegram


def send_message(message: Dict[str, Any]) -> None:
    """Send a neutral message to every configured channel (Slack and/or Telegram)."""
    sent = False
    if slack.is_configured():
        slack.send(message)
        sent = True
    if telegram.is_configured():
        telegram.send(message)
        sent = True
    if not sent:
        print(f"No messaging configured, message not sent: {message.get('text')}")
