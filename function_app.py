import json
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import azure.functions as func

from arboxrun import (
    ACTION_REGISTER,
    ACTION_SKIP,
    RUN_TIME,
    day_command,
    format_day,
    now_with_tz,
    notify,
    parse_hhmm,
    register_from_button,
    run_coordinated_flow,
    text_message,
)
from lib import slack, telegram
from lib.slack import respond, section, verify_signature

app = func.FunctionApp()

# Allow the timer to fire a few minutes before the target time; run_coordinated_flow waits until RUN_TIME.
RUN_WINDOW_MINUTES = 5

# Set SKIP_TIME_CHECK=true to bypass the time window check (for testing)
SKIP_TIME_CHECK = os.getenv("SKIP_TIME_CHECK", "false").lower() == "true"

# Button clicks and commands (Slack and Telegram) are queued so the HTTP handlers
# can answer within Slack's 3s limit; the worker does the slow Arbox calls.
SLACK_ACTIONS_QUEUE = "slack-actions"

# Optional comma-separated Slack user ids allowed to press buttons (empty = anyone in the channel).
ALLOWED_USER_IDS = {u.strip() for u in os.getenv("SLACK_ALLOWED_USER_IDS", "").split(",") if u.strip()}


@app.function_name(name="TimerEnroll")
@app.schedule(schedule="0 58 11,12 * * Thu", arg_name="mytimer", run_on_startup=False, use_monitor=True)
def timer_enroll(mytimer: func.TimerRequest) -> None:  # noqa: ANN001
    current = now_with_tz()
    target_dt = datetime.combine(current.date(), parse_hhmm(RUN_TIME), tzinfo=current.tzinfo)
    delta = target_dt - current

    if not SKIP_TIME_CHECK and abs(delta) > timedelta(minutes=RUN_WINDOW_MINUTES):
        logging.info(
            "Timer fired at %s (tz=%s); delta to target is %s (abs %s), outside +/-%d-minute window. Skipping.",
            current.strftime("%Y-%m-%d %H:%M:%S"),
            current.tzinfo,
            delta,
            abs(delta),
            RUN_WINDOW_MINUTES,
        )
        return

    if SKIP_TIME_CHECK:
        logging.info("SKIP_TIME_CHECK is enabled - bypassing time window check for testing.")

    try:
        run_coordinated_flow()
    except Exception as exc:  # noqa: BLE001
        notify("Arbox run failed", str(exc))
        raise


def replace_block(blocks: list, block_id: str, text: str) -> list:
    return [section(text) if b.get("block_id") == block_id else b for b in blocks]


@app.function_name(name="SlackActions")
@app.route(route="slack/actions", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.queue_output(arg_name="queue", queue_name=SLACK_ACTIONS_QUEUE, connection="AzureWebJobsStorage")
def slack_actions(req: func.HttpRequest, queue: func.Out[str]) -> func.HttpResponse:
    body = req.get_body()
    if not verify_signature(
        req.headers.get("X-Slack-Request-Timestamp", ""), req.headers.get("X-Slack-Signature", ""), body
    ):
        return func.HttpResponse("invalid signature", status_code=401)

    payload = json.loads(parse_qs(body.decode())["payload"][0])
    if ALLOWED_USER_IDS and payload.get("user", {}).get("id") not in ALLOWED_USER_IDS:
        return func.HttpResponse(status_code=200)

    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id", "")
    block_id = action.get("block_id", "")
    value = json.loads(action.get("value") or "{}")
    blocks = (payload.get("message") or {}).get("blocks", [])
    response_url = payload.get("response_url")

    if action_id == ACTION_SKIP:
        text = f"⏭️ *{format_day(value['d'])} {value['r']}* — skipped"
        respond(response_url, {"replace_original": True, "blocks": replace_block(blocks, block_id, text)})
    elif action_id.startswith(ACTION_REGISTER):
        # Swap the buttons for a pending line right away so they can't be pressed twice.
        pending = replace_block(blocks, block_id, f"⏳ Registering *{format_day(value['d'])} {value['t']}* ...")
        respond(response_url, {"replace_original": True, "blocks": pending})
        queue.set(json.dumps({"value": value, "response_url": response_url, "blocks": blocks, "block_id": block_id}))

    return func.HttpResponse(status_code=200)


@app.function_name(name="SlackCommands")
@app.route(route="slack/commands", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.queue_output(arg_name="queue", queue_name=SLACK_ACTIONS_QUEUE, connection="AzureWebJobsStorage")
def slack_commands(req: func.HttpRequest, queue: func.Out[str]) -> func.HttpResponse:
    body = req.get_body()
    if not verify_signature(
        req.headers.get("X-Slack-Request-Timestamp", ""), req.headers.get("X-Slack-Signature", ""), body
    ):
        return func.HttpResponse("invalid signature", status_code=401)

    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
    if ALLOWED_USER_IDS and form.get("user_id") not in ALLOWED_USER_IDS:
        return func.HttpResponse(status_code=200)

    # Logging in and loading the schedule can exceed Slack's 3s limit, so answer from the worker.
    queue.set(json.dumps({"kind": "command", "text": form.get("text", ""), "response_url": form.get("response_url")}))
    return func.HttpResponse(status_code=200)


TELEGRAM_HELP = (
    "Send `/arbox <day>` (or just `/wed`) to see that day's classes and book one.\n"
    "Your chat id is `{chat_id}`."
)


def telegram_command_arg(text: str) -> str:
    """'/arbox wed', '/arbox@my_bot wed' and '/wed' all return 'wed'."""
    words = text.strip().split()
    command = words[0][1:].split("@")[0].lower()
    return " ".join(words[1:]) if command == "arbox" else command


@app.function_name(name="TelegramWebhook")
@app.route(route="telegram/webhook", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
@app.queue_output(arg_name="queue", queue_name=SLACK_ACTIONS_QUEUE, connection="AzureWebJobsStorage")
def telegram_webhook(req: func.HttpRequest, queue: func.Out[str]) -> func.HttpResponse:
    if not telegram.verify_secret(req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
        return func.HttpResponse("invalid secret", status_code=401)

    update = req.get_json()
    allowed_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    callback = update.get("callback_query")
    message = update.get("message")

    if callback:
        msg = callback.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if str(chat_id) != allowed_chat:
            telegram.answer_callback(callback["id"])
            return func.HttpResponse(status_code=200)
        value = telegram.parse_button_data(callback.get("data", ""))
        # Remove that day's buttons right away so they can't be pressed twice.
        telegram.remove_day_buttons(chat_id, msg["message_id"], msg.get("reply_markup"), value["d"])
        if value["action"] == ACTION_SKIP:
            telegram.answer_callback(callback["id"], "Skipped")
            telegram.send(text_message(f"⏭️ *{format_day(value['d'])} {value['r']}* — skipped"), chat_id, msg["message_id"])
        else:
            telegram.answer_callback(callback["id"], f"Registering {value['t']} ...")
            queue.set(json.dumps({"kind": "telegram_register", "value": value, "chat_id": chat_id, "message_id": msg["message_id"]}))
    elif message and (message.get("text") or "").startswith("/"):
        chat_id = message["chat"]["id"]
        arg = telegram_command_arg(message["text"])
        if arg in ("start", "help") or str(chat_id) != allowed_chat:
            # Reply with the chat id even to unknown chats: it's needed for setup and reveals nothing else.
            telegram.send(text_message(TELEGRAM_HELP.format(chat_id=chat_id)), chat_id)
        else:
            queue.set(json.dumps({"kind": "telegram_command", "text": arg, "chat_id": chat_id}))

    return func.HttpResponse(status_code=200)


def run_job(job: dict) -> None:
    kind = job.get("kind")
    if kind == "command":
        try:
            message = day_command(job["text"])
        except Exception as exc:  # noqa: BLE001
            logging.exception("Slack command failed")
            message = text_message(f"❌ {exc}")
        respond(job["response_url"], {"response_type": "in_channel", **slack.render(message)})
        return

    if kind == "telegram_command":
        try:
            message = day_command(job["text"])
        except Exception as exc:  # noqa: BLE001
            logging.exception("Telegram command failed")
            message = text_message(f"❌ {exc}")
        telegram.send(message, job["chat_id"])
        return

    value = job["value"]
    try:
        text = register_from_button(value)
    except Exception as exc:  # noqa: BLE001
        logging.exception("Registration from button failed")
        text = f"❌ *{format_day(value['d'])} {value['t']}* — {exc}"

    if kind == "telegram_register":
        telegram.send(text_message(text), job["chat_id"], job["message_id"])
    else:
        respond(job["response_url"], {"replace_original": True, "blocks": replace_block(job["blocks"], job["block_id"], text)})


@app.function_name(name="SlackActionWorker")
@app.queue_trigger(arg_name="item", queue_name=SLACK_ACTIONS_QUEUE, connection="AzureWebJobsStorage")
def slack_action_worker(item: func.QueueMessage) -> None:
    # Handles queued Slack and Telegram jobs (name kept so the deployed function isn't recreated).
    run_job(json.loads(item.get_body().decode()))
