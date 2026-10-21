import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests

POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
# Slack rejects requests older than this to prevent replays.
SIGNATURE_MAX_AGE_SEC = 60 * 5


def is_configured() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL_ID"))


def section(text: str) -> Dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def button_element(button: Dict[str, Any]) -> Dict[str, Any]:
    if button["action"] == "register":
        action_id = f"register:{button['schedule_id']}"
        value = {"d": button["date"], "t": button["time"], "s": button["schedule_id"], "r": button["requested_time"]}
    else:
        action_id = button["action"]
        value = {"d": button["date"], "r": button["time"]}
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": button["label"][:75]},
        "action_id": action_id,
        "value": json.dumps(value),
    }


def render_blocks(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Render a neutral message ({"text", "parts": [{"text", "key", "buttons"}]}) as Slack blocks."""
    blocks: List[Dict[str, Any]] = []
    for part in message.get("parts", []):
        blocks.append(section(part["text"]))
        if part.get("buttons"):
            blocks.append({
                "type": "actions",
                "block_id": part["key"],
                "elements": [button_element(b) for b in part["buttons"]],
            })
    return blocks


def render(message: Dict[str, Any]) -> Dict[str, Any]:
    """Slack message payload (text + blocks) for chat.postMessage or a response_url."""
    return {"text": message["text"], "blocks": render_blocks(message)}


def send(message: Dict[str, Any]) -> None:
    rendered = render(message)
    post_message(rendered["text"], rendered["blocks"])


def post_message(text: str, blocks: Optional[List[Dict[str, Any]]] = None) -> None:
    """Post a message to SLACK_CHANNEL_ID. `text` is the notification/fallback text."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    if not token or not channel:
        print(f"Slack not configured, message not sent: {text}")
        return
    payload: Dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        resp = requests.post(
            POST_MESSAGE_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"Slack post failed: {data.get('error')}")
    except Exception as exc:  # noqa: BLE001
        print(f"Slack post failed: {exc}")


def respond(response_url: str, payload: Dict[str, Any]) -> None:
    """Reply to an interaction via its response_url (e.g. to update the original message)."""
    try:
        requests.post(response_url, json=payload, timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"Slack response failed: {exc}")


def verify_signature(timestamp: str, signature: str, body: bytes) -> bool:
    """Verify an incoming Slack request using SLACK_SIGNING_SECRET."""
    secret = os.environ.get("SLACK_SIGNING_SECRET")
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_MAX_AGE_SEC:
            return False
    except ValueError:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
