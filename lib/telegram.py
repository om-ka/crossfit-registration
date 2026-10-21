import hmac
import html
import os
import re
from typing import Any, Dict, List, Optional

import requests

API_URL = "https://api.telegram.org/bot{token}/{method}"
BUTTONS_PER_ROW = 3


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def call(method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    try:
        resp = requests.post(API_URL.format(token=token, method=method), json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"Telegram {method} failed: {data.get('description')}")
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram {method} failed: {exc}")
        return None


def to_html(text: str) -> str:
    """Convert the Slack-style *bold* / `code` used in messages to Telegram HTML."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", text)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)


def button_data(button: Dict[str, Any]) -> str:
    """Compact callback data (Telegram allows 64 bytes): r|date|time|schedule_id or s|date|time."""
    if button["action"] == "register":
        return f"r|{button['date']}|{button['time']}|{button['schedule_id']}"
    return f"s|{button['date']}|{button['time']}"


def parse_button_data(data: str) -> Dict[str, Any]:
    parts = data.split("|")
    if parts[0] == "r" and len(parts) == 4:
        return {"action": "register", "d": parts[1], "t": parts[2], "s": int(parts[3])}
    if parts[0] == "s" and len(parts) == 3:
        return {"action": "skip", "d": parts[1], "r": parts[2]}
    raise ValueError(f"Unknown button data: {data}")


def keyboard(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rows: List[List[Dict[str, str]]] = []
    for part in message.get("parts", []):
        buttons = [{"text": b["label"][:64], "callback_data": button_data(b)} for b in part.get("buttons") or []]
        rows += [buttons[i:i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]
    return {"inline_keyboard": rows} if rows else None


def render_text(message: Dict[str, Any]) -> str:
    return "\n\n".join(to_html(p["text"]) for p in message.get("parts", [])) or to_html(message["text"])


def send(message: Dict[str, Any], chat_id: Optional[str] = None, reply_to: Optional[int] = None) -> None:
    """Send a neutral message ({"text", "parts": [{"text", "buttons"}]}) to the chat."""
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": render_text(message), "parse_mode": "HTML"}
    markup = keyboard(message)
    if markup:
        payload["reply_markup"] = markup
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    call("sendMessage", payload)


def remove_day_buttons(chat_id: Any, message_id: int, markup: Optional[Dict[str, Any]], date_str: str) -> None:
    """Drop the buttons for one day from a message so they can't be pressed again."""
    rows = [
        [b for b in row if f"|{date_str}|" not in b.get("callback_data", "")]
        for row in (markup or {}).get("inline_keyboard", [])
    ]
    rows = [row for row in rows if row]
    call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": rows}})


def answer_callback(callback_id: str, text: str = "") -> None:
    call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def verify_secret(header_value: str) -> bool:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    return bool(secret) and hmac.compare_digest(secret, header_value or "")
