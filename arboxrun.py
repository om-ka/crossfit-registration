#!/usr/bin/env python3
"""
Arbox one-shot enrolment.

Timer-friendly script that logs in, waits until a target time, and registers
for the next Sunday/Tuesday/Thursday classes at 06:00 (box 28, location 7).
"""

import os
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional

import requests

try:
    from data.config import config
    from lib.push_notification import send_push_notification_sync
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
# Days to enroll (next occurrence of each)
DAYS_TO_BOOK = ["sun", "tue", "thu"]

# Time coordination constants (Azure Function friendly)
RUN_TIME = "15:00"


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


def fetch_class_id(
    target_date: str,
    start_time: str,
    token: str,
    refresh_token: str,
    location_id: int,
    box_id: int,
    ) -> Dict[str, int]:
    """Return the schedule id and box id for the class that matches the given start time."""
    date_iso = f"{target_date}T00:00:00.000Z"
    payload = {
        "from": date_iso,
        "to": date_iso,
        "locations_box_id": location_id,
        "boxes_id": box_id,
    }

    resp = requests.post(SCHEDULE_URL, headers=auth_headers(token, refresh_token), json=payload)
    resp.raise_for_status()

    schedule_items = resp.json().get("data", [])
    for entry in schedule_items:
        if entry.get("time") == start_time:
            schedule_id = entry.get("id")
            box_id = entry.get("box_fk") or entry.get("box", {}).get("id")
            if schedule_id is not None and box_id is not None:
                return {"schedule_id": schedule_id, "box_id": box_id}

    raise RuntimeError(f"No class starting at {start_time} on {target_date} was found.")


def register_for_class(
    schedule_id: int, membership_user_id: int, token: str, refresh_token: str
) -> Dict[str, Any]:
    payload = {
        "schedule_id": schedule_id,
        "membership_user_id": membership_user_id,
        "extras": {"spot": None},
    }
    resp = requests.post(REGISTER_URL, headers=auth_headers(token, refresh_token), json=payload)

    if resp.status_code == 516:
        raise RuntimeError("Class is full (status 516).")
    if resp.status_code != 200:
        try:
            error_message = resp.json().get("error", {}).get("messageToUser")
        except Exception:  # noqa: BLE001
            error_message = resp.text
        raise RuntimeError(f"Registration failed ({resp.status_code}): {error_message}")

    return resp.json()


def validate_inputs(date_str: str, start_time: str) -> None:
    """Quick validation so the API gets clean values."""
    datetime.strptime(date_str, "%Y-%m-%d")
    datetime.strptime(start_time, "%H:%M")


def notify(title: str, message: str) -> None:
    """Send Alertzy push if a key is configured."""
    key = getattr(config, "alertzy_account_key", None) or os.environ.get("ALERTZY_ACCOUNT_KEY")
    if not key:
        return
    try:
        send_push_notification_sync(key, title, message)
    except Exception as exc:  # noqa: BLE001
        print(f"Push notification failed: {exc}")


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
    """Wait until the configured run time with strategic notifications."""
    target_time = parse_hhmm(run_time_str)
    start = now_with_tz()
    target_dt = datetime.combine(start.date(), target_time, tzinfo=start.tzinfo)

    # Send initial notification
    notify(
        "Arbox waiting",
        f"Started waiting for {run_time_str}. Now {start.strftime('%H:%M:%S')} "
        f"(target {target_dt.strftime('%H:%M:%S')})",
    )

    notified_60s = False
    notified_5s_before = False

    while True:
        current = now_with_tz()
        if current >= target_dt:
            return

        seconds_elapsed = (current - start).total_seconds()
        seconds_remaining = (target_dt - current).total_seconds()

        # Notify after 60 seconds of waiting
        if not notified_60s and seconds_elapsed >= 60:
            notify(
                "Arbox waiting",
                f"Still waiting (60s elapsed). Now {current.strftime('%H:%M:%S')} "
                f"(target {target_dt.strftime('%H:%M:%S')})",
            )
            notified_60s = True

        # Notify 5 seconds before registration
        if not notified_5s_before and seconds_remaining <= 5:
            notify(
                "Arbox almost ready",
                f"Registering in {int(seconds_remaining)}s! "
                f"(target {target_dt.strftime('%H:%M:%S')})",
            )
            notified_5s_before = True

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


def get_days_to_book() -> List[str]:
    try:
        """Return configured days to book, allowing an env override (comma-separated)."""
        env_value = os.environ.get("ARBOX_REGISTRATIPON_DAYS")
        if env_value:
            parsed = [day.strip() for day in env_value.split(",") if day.strip()]
            if parsed:
                return parsed
    except Exception as exc:  # noqa: BLE001
        notify(f"Error parsing ARBOX_REGISTRATIPON_DAYS: {exc}, using default values of {DEFAULT_DAYS_TO_BOOK}.")
    return DEFAULT_DAYS_TO_BOOK


def run_enrollment(
    date_str: str,
    start_time: str,
    token: str,
    refresh_token: str,
    box_id: int = DEFAULT_BOX_ID,
    location_id: int = DEFAULT_LOCATION_ID,
    membership_type: int = DEFAULT_MEMBERSHIP_TYPE,
) -> Dict[str, Any]:
    """Core enrollment flow for a single class."""
    validate_inputs(date_str, start_time)

    print(
        f"Looking for class on {date_str} at {start_time} "
        f"(box {box_id}, location {location_id}) ..."
    )
    class_info = fetch_class_id(
        date_str, start_time, token, refresh_token, location_id, box_id
    )
    schedule_id = class_info["schedule_id"]
    box_id_for_membership = class_info["box_id"]
    print(f"Found schedule_id={schedule_id} (box_id={box_id_for_membership}), fetching membership id ...")

    membership_user_id = fetch_membership_user_id(
        token, refresh_token, box_id_for_membership, membership_type
    )

    print(f"Membership id resolved to {membership_user_id}, attempting to register ...")
    result = register_for_class(schedule_id, membership_user_id, token, refresh_token)
    print("Registration successful.")
    print(result)
    return {
        "date": date_str,
        "start_time": start_time,
        "schedule_id": schedule_id,
        "membership_user_id": membership_user_id,
    }


def run_coordinated_flow() -> Dict[str, Any]:
    """Check time, notify, wait until run time, then enroll all configured classes."""
    current = now_with_tz()
    notify(
        "Arbox run started",
        f"Time check {current.strftime('%Y-%m-%d %H:%M:%S')} (waiting for {RUN_TIME})",
    )
    wait_until_run_time(RUN_TIME)

    email = config.user_creds.get("email") or os.environ.get("ARBOX_USER_EMAIL")
    password = config.user_creds.get("password") or os.environ.get("ARBOX_USER_PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing ARBOX_USER_EMAIL or ARBOX_USER_PASSWORD in environment.")

    print(f"Logging in as {email} ...")
    auth = login(email, password)
    token = auth["token"]
    refresh_token = auth["refresh_token"]

    # Build list of target dates for configured weekdays
    today = now_with_tz()
    targets = []
    for day in get_days_to_book():
        try:
            idx = weekday_index(day)
        except KeyError:
            print(f"Skipping unknown day '{day}'")
            continue
        target_date = next_date_for_weekday(idx, today)
        targets.append(target_date)

    summaries = []
    for date_str in targets:
        try:
            summary = run_enrollment(
                date_str=date_str,
                start_time=DEFAULT_START_TIME,
                token=token,
                refresh_token=refresh_token,
                box_id=DEFAULT_BOX_ID,
                location_id=DEFAULT_LOCATION_ID,
                membership_type=DEFAULT_MEMBERSHIP_TYPE,
            )
            summaries.append(summary)
            notify(
                "Arbox run succeeded",
                f"Registered for {summary['date']} {summary['start_time']} "
                f"(schedule_id={summary['schedule_id']}, membership_id={summary['membership_user_id']})",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Error registering for {date_str}: {exc}")
            notify("Arbox run failed", f"{date_str} {DEFAULT_START_TIME}: {exc}")

    return {"results": summaries}


if __name__ == "__main__":
    try:
        summary = run_coordinated_flow()
        results = summary.get("results", []) if isinstance(summary, dict) else []
        for res in results:
            notify(
                "Arbox run succeeded",
                f"Registered for {res.get('date')} {res.get('start_time')} "
                f"(schedule_id={res.get('schedule_id')}, membership_id={res.get('membership_user_id')})",
            )
        if not results:
            notify("Arbox run completed", "No registrations were created.")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        notify("Arbox run failed", str(exc))
        sys.exit(1)
