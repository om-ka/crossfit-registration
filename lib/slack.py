import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional

import requests

POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
# Slack rejects requests older than this to prevent replays.
SIGNATURE_MAX_AGE_SEC = 60 * 5


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
