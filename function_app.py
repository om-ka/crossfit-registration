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
    format_day,
    now_with_tz,
    notify,
    parse_hhmm,
    register_from_button,
    run_coordinated_flow,
    section,
)
from lib.slack import respond, verify_signature

app = func.FunctionApp()

# Allow the timer to fire a few minutes before the target time; run_coordinated_flow waits until RUN_TIME.
RUN_WINDOW_MINUTES = 5

# Set SKIP_TIME_CHECK=true to bypass the time window check (for testing)
SKIP_TIME_CHECK = os.getenv("SKIP_TIME_CHECK", "false").lower() == "true"

# Button clicks are queued so the HTTP handler can ack Slack within its 3s limit.
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


@app.function_name(name="SlackActionWorker")
@app.queue_trigger(arg_name="item", queue_name=SLACK_ACTIONS_QUEUE, connection="AzureWebJobsStorage")
def slack_action_worker(item: func.QueueMessage) -> None:
    job = json.loads(item.get_body().decode())
    try:
        text = register_from_button(job["value"])
    except Exception as exc:  # noqa: BLE001
        logging.exception("Registration from Slack failed")
        text = f"❌ *{format_day(job['value']['d'])} {job['value']['t']}* — {exc}"
    respond(job["response_url"], {"replace_original": True, "blocks": replace_block(job["blocks"], job["block_id"], text)})
