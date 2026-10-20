#!/usr/bin/env python3
"""
Arbox one-shot enrolment.

Timer-friendly script that logs in, waits until a target time, registers for
the configured classes and posts a single Slack summary. Classes that could
not be booked are offered as buttons with the other times available that day.
"""

import json
import os
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional

import requests

try:
    from data.config import config
    from lib.slack import post_message
except Exception as exc:  # noqa: BLE001
    print(f"Failed to load config/environment: {exc}")
    raise

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


API_BASE = "https://apiappv2.arboxapp.com/api/v2"
LOGIN_URL = f"{API_BASE}/user/login"
SCHEDULE_URL = f"{API_BASE}/schedule/betweenDates?XDEBUG_SESSION_START=PHPSTORM"
REGISTER_URL = f"{API_BASE}/scheduleUser/insert?XDEBUG_SESSION_START=PHPSTORM"
MEMBERSHIP_URL_TEMPLATE = f"{API_BASE}/boxes/{{box_id}}/memberships/{{membership_type}}"

DEFAULT_BOX_ID = 28
DEFAULT_LOCATION_ID = 7
DEFAULT_START_TIME = os.environ.get("ARBOX_REGISTRATION_START_TIME") or "06:00"
DEFAULT_MEMBERSHIP_TYPE = 1
# Days to enroll (next occurrence of each). Each entry is a dict with a
# required "day" and an optional "time" (HH:MM). When "time" is omitted the
# DEFAULT_START_TIME above is used.
DAYS_TO_BOOK: List[Dict[str, str]] = [
    {"day": "sun"},
    {"day": "tue"},
    {"day": "thu"},
]

# Time coordination constants (Azure Function friendly)
RUN_TIME = "15:00"

# Classes sometimes open a bit late. Keep polling the requested classes for up
# to CLASS_OPEN_WAIT_SEC before falling back to asking the user on Slack.
CLASS_OPEN_WAIT_SEC = 30
CLASS_POLL_INTERVAL_SEC = 2

REGISTER_RETRY_DELAYS_SEC = [0.01, 0.1, 1.0, 2.0, 3.0]

# Slack button action ids
ACTION_REGISTER = "register"
ACTION_SKIP = "skip"


class ClassFullError(RuntimeError):
    pass


def common_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        # Mimic Arbox mobile client to avoid CF blocking
        "User-Agent": "okhttp/4.9.2",
        "referername": "app",
        "version": "13",
        "whitelabel": "Arbox",
        "Accept-Encoding": "gzip",
    }


def auth_headers(token: str, refresh_token: Optional[str]) -> Dict[str, str]:
    headers = common_headers()
    headers["accesstoken"] = token
    if refresh_token:
        headers["refreshtoken"] = refresh_token
    # Optional tracing headers the app often sends; static placeholders are fine
    headers.setdefault("sentry-trace", "1c0e3f6bc8d540edae2affa0f303abdf-8a74a161cf08280f")
    headers.setdefault(
        "baggage",
        "sentry-environment=production,sentry-public_key=7437b901fcd6fce680d9a9f6fdc73063,"
        "sentry-trace_id=1c0e3f6bc8d540edae2affa0f303abdf",
    )
    return headers


def login(email: str, password: str) -> Dict[str, Any]:
    """Login and return tokens plus any membership id available in the response."""
    resp = requests.post(LOGIN_URL, headers=common_headers(), json={"email": email, "password": password})
    resp.raise_for_status()

    data = resp.json().get("data", {})
    token = data.get("token")
    refresh_token = data.get("refreshToken") or data.get("refreshtoken")

    if not token or not refresh_token:
        raise RuntimeError("Login succeeded but tokens were not returned.")

    membership_user_id = (
        data.get("membership_user_id")
        or data.get("membershipUserId")
        or data.get("membershipsUserId")
        or data.get("membershipId")
    )

    return {
        "token": token,
        "refresh_token": refresh_token,
        "user_id": data.get("id"),
        "membership_user_id": membership_user_id,
    }


def login_from_config() -> Dict[str, Any]:
    email = config.user_creds.get("email") or os.environ.get("ARBOX_USER_EMAIL")
    password = config.user_creds.get("password") or os.environ.get("ARBOX_USER_PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing ARBOX_USER_EMAIL or ARBOX_USER_PASSWORD in environment.")
    print(f"Logging in as {email} ...")
    return login(email, password)


def fetch_membership_user_id(token: str, refresh_token: str, box_id: int, membership_type: int) -> int:
    """Fetch membership user id from membership endpoint."""
    url = f"{MEMBERSHIP_URL_TEMPLATE.format(box_id=box_id, membership_type=membership_type)}/false?XDEBUG_SESSION_START=PHPSTORM"
    resp = requests.get(url, headers=auth_headers(token, refresh_token))
    resp.raise_for_status()

    payload = resp.json()
    membership_list = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(membership_list, list) and membership_list:
        membership_id = membership_list[0].get("id")
        if membership_id is not None:
            return membership_id

    raise RuntimeError("Unable to determine membership_user_id from membership endpoint.")


def fetch_day_schedule(
    target_date: str,
    token: str,
    refresh_token: str,
    location_id: int = DEFAULT_LOCATION_ID,
    box_id: int = DEFAULT_BOX_ID,
) -> List[Dict[str, Any]]:
    """Return all schedule entries for a single day."""
    date_iso = f"{target_date}T00:00:00.000Z"
    payload = {
        "from": date_iso,
        "to": date_iso,
        "locations_box_id": location_id,
        "boxes_id": box_id,
    }

    resp = requests.post(SCHEDULE_URL, headers=auth_headers(token, refresh_token), json=payload)
    resp.raise_for_status()
    return resp.json().get("data", []) or []


def find_class(schedule: List[Dict[str, Any]], start_time: str) -> Dict[str, Any]:
    for entry in schedule:
        if entry.get("time") == start_time and entry.get("id") is not None:
            return entry
    raise RuntimeError(f"No class starting at {start_time} was found.")


def entry_box_id(entry: Dict[str, Any]) -> int:
    return entry.get("box_fk") or (entry.get("box") or {}).get("id") or DEFAULT_BOX_ID


def register_for_class(
    schedule_id: int, membership_user_id: int, token: str, refresh_token: str
) -> Dict[str, Any]:
    payload = {
        "schedule_id": schedule_id,
        "membership_user_id": membership_user_id,
        "extras": {"spot": None},
    }

    last_error: Optional[Exception] = None
    for attempt in range(len(REGISTER_RETRY_DELAYS_SEC) + 1):
        try:
            resp = requests.post(REGISTER_URL, headers=auth_headers(token, refresh_token), json=payload)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        else:
            # 516 = class full; retrying won't help.
            if resp.status_code == 516:
                raise ClassFullError("Class is full (status 516).")
            if resp.status_code == 200:
                return resp.json()
            try:
                error_message = resp.json().get("error", {}).get("messageToUser")
            except Exception:  # noqa: BLE001
                error_message = resp.text
            last_error = RuntimeError(f"Registration failed ({resp.status_code}): {error_message}")

        if attempt < len(REGISTER_RETRY_DELAYS_SEC):
            delay = REGISTER_RETRY_DELAYS_SEC[attempt]
            print(
                f"Registration attempt {attempt + 1} failed: {last_error}. "
                f"Retrying in {int(delay * 1000)}ms."
            )
            time.sleep(delay)

    raise last_error  # type: ignore[misc]


def validate_inputs(date_str: str, start_time: str) -> None:
    """Quick validation so the API gets clean values."""
    datetime.strptime(date_str, "%Y-%m-%d")
    datetime.strptime(start_time, "%H:%M")


def notify(title: str, message: str) -> None:
    """Send a plain Slack message (used for errors only; the run summary has its own message)."""
    post_message(f"*{title}*\n{message}")


def get_timezone():
    tz_name = getattr(config, "timezone", None)
    if tz_name and ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return None
    return None


def now_with_tz():
    tz = get_timezone()
    return datetime.now(tz)


def parse_hhmm(value: str) -> dt_time:
    dt_obj = datetime.strptime(value, "%H:%M")
    return dt_time(hour=dt_obj.hour, minute=dt_obj.minute)


def wait_until_run_time(run_time_str: str) -> None:
    """Wait until the configured run time."""
    target_time = parse_hhmm(run_time_str)
    start = now_with_tz()
    target_dt = datetime.combine(start.date(), target_time, tzinfo=start.tzinfo)
    print(f"Waiting until {target_dt.strftime('%H:%M:%S')} (now {start.strftime('%H:%M:%S')}) ...")
    while now_with_tz() < target_dt:
        time.sleep(1)


def weekday_index(day_str: str) -> int:
    mapping = {
        "mon": 0, "monday": 0,
        "tue": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    return mapping[day_str.lower()]


def next_date_for_weekday(target_weekday: int, from_date: datetime) -> str:
    """Return next occurrence (future) of target weekday as YYYY-MM-DD."""
    current_weekday = from_date.weekday()
    delta_days = (target_weekday - current_weekday) % 7
    if delta_days == 0:
        delta_days = 7
    target_date = from_date + timedelta(days=delta_days)
    return target_date.strftime("%Y-%m-%d")


def get_days_to_book() -> List[Dict[str, str]]:
    """Return configured days to book (with optional per-day time).

    Env override ARBOX_REGISTRATION_DAYS is comma-separated. Each entry is
    either "<day>" (uses DEFAULT_START_TIME) or "<day>:HH:MM" (per-day time).
    Example: "sun,tue:07:00,thu"
    """
    try:
        env_value = os.environ.get("ARBOX_REGISTRATION_DAYS")
        if env_value:
            parsed: List[Dict[str, str]] = []
            for raw in env_value.split(","):
                raw = raw.strip()
                if not raw:
                    continue
                day, _, time_str = raw.partition(":")
                entry: Dict[str, str] = {"day": day.strip()}
                time_str = time_str.strip()
                if time_str:
                    entry["time"] = time_str
                parsed.append(entry)
            if parsed:
                return parsed
    except Exception as exc:  # noqa: BLE001
        print(f"Error parsing ARBOX_REGISTRATION_DAYS: {exc}, using default values of {DAYS_TO_BOOK}.")
    return DAYS_TO_BOOK


def enroll_schedule_entry(
    entry: Dict[str, Any], token: str, refresh_token: str, membership_type: int = DEFAULT_MEMBERSHIP_TYPE
) -> Dict[str, Any]:
    """Register for a schedule entry (no-op if already booked)."""
    schedule_id = entry["id"]
    summary = {"date": entry.get("date"), "start_time": entry.get("time"), "schedule_id": schedule_id}
    if entry.get("user_booked"):
        print(f"Already booked for schedule_id={schedule_id}.")
        return {**summary, "already_booked": True}

    membership_user_id = fetch_membership_user_id(token, refresh_token, entry_box_id(entry), membership_type)
    print(f"Registering schedule_id={schedule_id} with membership id {membership_user_id} ...")
    result = register_for_class(schedule_id, membership_user_id, token, refresh_token)
    print(f"Registration successful: {result}")
    return {**summary, "membership_user_id": membership_user_id}


def run_enrollment(date_str: str, start_time: str, token: str, refresh_token: str) -> Dict[str, Any]:
    """Core enrollment flow for a single class."""
    validate_inputs(date_str, start_time)
    print(f"Looking for class on {date_str} at {start_time} ...")
    entry = find_class(fetch_day_schedule(date_str, token, refresh_token), start_time)
    summary = enroll_schedule_entry(entry, token, refresh_token)
    summary.update(date=date_str, start_time=start_time)
    return summary


def enroll_all(targets: List[tuple], token: str, refresh_token: str) -> Dict[str, Any]:
    """Enroll all targets, polling round-robin for late-opening classes.

    Returns {"summaries": [...], "failures": {(date, time): error}}.
    """
    summaries: List[Dict[str, Any]] = []
    pending: Dict[tuple, Exception] = {t: RuntimeError("not attempted") for t in targets}
    failures: Dict[tuple, Exception] = {}
    deadline = time.monotonic() + CLASS_OPEN_WAIT_SEC

    while pending:
        for target in list(pending):
            date_str, start_time = target
            try:
                summaries.append(run_enrollment(date_str, start_time, token, refresh_token))
                del pending[target]
            except ClassFullError as exc:
                # Waiting won't help a full class.
                failures[target] = exc
                del pending[target]
            except Exception as exc:  # noqa: BLE001
                pending[target] = exc
                print(f"{date_str} {start_time} not available yet: {exc}")
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(CLASS_POLL_INTERVAL_SEC)

    failures.update(pending)
    return {"summaries": summaries, "failures": failures}


def bookable_alternatives(schedule: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Classes on the day that are active, upcoming, have free spots and aren't booked yet."""
    return [
        e for e in schedule
        if e.get("status") == "active" and not e.get("past") and (e.get("free") or 0) > 0 and not e.get("user_booked")
    ]


def format_day(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%a %d/%m")


def class_label(entry: Dict[str, Any]) -> str:
    name = (entry.get("box_categories") or {}).get("name")
    return f"{entry.get('time')} {name}" if name else str(entry.get("time"))


def register_button(entry: Dict[str, Any], requested_time: str) -> Dict[str, Any]:
    value = {"d": entry.get("date"), "t": entry.get("time"), "s": entry["id"], "r": requested_time}
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": class_label(entry)[:75]},
        "action_id": f"{ACTION_REGISTER}:{entry['id']}",
        "value": json.dumps(value),
    }


def failure_blocks(date_str: str, start_time: str, error: Exception, token: str, refresh_token: str) -> List[Dict[str, Any]]:
    """Blocks for a class that couldn't be booked: what went wrong plus buttons for alternatives."""
    day = format_day(date_str)
    try:
        schedule = fetch_day_schedule(date_str, token, refresh_token)
    except Exception as exc:  # noqa: BLE001
        return [section(f"❌ *{day} {start_time}* — {error}\nCouldn't load that day's schedule: {exc}")]

    if not schedule:
        return [section(f"🚫 *{day} {start_time}* — no classes listed that day (holiday / closed?)")]

    alternatives = bookable_alternatives(schedule)
    if not alternatives:
        return [section(f"⚠️ *{day} {start_time}* — {error}\nNo other classes with free spots that day.")]

    # Slack allows up to 25 elements per actions block; one is reserved for Skip.
    buttons = [register_button(e, start_time) for e in alternatives[:24]]
    buttons.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "Skip"},
        "action_id": ACTION_SKIP,
        "value": json.dumps({"d": date_str, "r": start_time}),
    })
    return [
        section(f"⚠️ *{day} {start_time}* — {error}\nPick another class that day:"),
        {"type": "actions", "block_id": f"day:{date_str}", "elements": buttons},
    ]


def section(text: str) -> Dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def success_line(summary: Dict[str, Any]) -> str:
    note = " (already booked)" if summary.get("already_booked") else ""
    return f"✅ *{format_day(summary['date'])} {summary['start_time']}* — registered{note}"


def post_summary(outcome: Dict[str, Any], token: str, refresh_token: str) -> None:
    """Post one Slack message summarizing the run."""
    summaries = sorted(outcome["summaries"], key=lambda s: s["date"])
    failures = sorted(outcome["failures"].items())

    blocks: List[Dict[str, Any]] = []
    if summaries:
        blocks.append(section("\n".join(success_line(s) for s in summaries)))
    for (date_str, start_time), exc in failures:
        blocks.extend(failure_blocks(date_str, start_time, exc, token, refresh_token))
    if not blocks:
        blocks.append(section("Nothing to book this week."))

    if failures:
        text = f"Arbox: {len(summaries)} registered, {len(failures)} need attention"
    else:
        text = f"Arbox: all {len(summaries)} classes registered"
    post_message(text, blocks)


def run_coordinated_flow() -> Dict[str, Any]:
    """Wait until run time, enroll all configured classes and post a Slack summary."""
    # SKIP_TIME_CHECK=true (testing) runs immediately instead of waiting for RUN_TIME.
    if os.getenv("SKIP_TIME_CHECK", "false").lower() != "true":
        wait_until_run_time(RUN_TIME)

    auth = login_from_config()
    token = auth["token"]
    refresh_token = auth["refresh_token"]

    # Build list of (target_date, start_time) pairs for configured days.
    today = now_with_tz()
    targets: List[tuple] = []
    for entry in get_days_to_book():
        day = entry.get("day")
        if not day:
            raise ValueError(f"DAYS_TO_BOOK entry missing 'day' key: {entry!r}")
        start_time = entry.get("time") or DEFAULT_START_TIME
        try:
            idx = weekday_index(day)
        except KeyError:
            print(f"Skipping unknown day '{day}'")
            continue
        target_date = next_date_for_weekday(idx, today)
        targets.append((target_date, start_time))

    outcome = enroll_all(targets, token, refresh_token)
    post_summary(outcome, token, refresh_token)
    return {"results": outcome["summaries"]}


def register_from_button(value: Dict[str, Any]) -> str:
    """Handle a Slack 'register' button: book the chosen class and return a status line."""
    date_str, start_time, schedule_id = value["d"], value["t"], value["s"]
    day = format_day(date_str)
    tz = get_timezone()
    class_start = datetime.combine(datetime.strptime(date_str, "%Y-%m-%d").date(), parse_hhmm(start_time), tzinfo=tz)
    if now_with_tz() >= class_start:
        return f"⌛ *{day} {start_time}* — that class already started."

    auth = login_from_config()
    token, refresh_token = auth["token"], auth["refresh_token"]
    schedule = fetch_day_schedule(date_str, token, refresh_token)
    entry = next((e for e in schedule if e.get("id") == schedule_id), None)
    if entry is None:
        return f"❌ *{day} {start_time}* — class no longer exists."
    try:
        summary = enroll_schedule_entry(entry, token, refresh_token)
    except ClassFullError:
        return f"❌ *{day} {start_time}* — class is full now."
    except Exception as exc:  # noqa: BLE001
        return f"❌ *{day} {start_time}* — {exc}"
    return success_line({**summary, "date": date_str, "start_time": start_time})


if __name__ == "__main__":
    try:
        run_coordinated_flow()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        notify("Arbox run failed", str(exc))
        sys.exit(1)
