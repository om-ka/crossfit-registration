# Arbox Auto-Enroll (Azure Function)

Timer-triggered Azure Function that logs into Arbox and enrolls you into the next Sunday, Tuesday, and Thursday 06:00 classes (box 28 / location 7). It sends Alertzy notifications when it starts, while waiting, and per-class success/failure.

## How it works
- Timer trigger: fires Thursdays at 11:58 and 12:58 UTC (cron `0 58 11,12 * * Thu`) and runs only when within a few minutes of 15:00 Israel time (`TimerEnroll/function.json`, `TimerEnroll/__init__.py`). This avoids DST drift and gives a 2-minute head start before the 15:00 run window.
- At trigger time, it notifies via Alertzy, then waits until 15:00 (with “waiting” notifications every 10 seconds) before running enrollment.
- Enrollment logic (`arboxrun.py`):
  - Targets next occurrences of Sunday, Tuesday, and Thursday.
  - Start time: `06:00`
  - Box ID: `28`
  - Location ID: `7`
  - Membership type: `1`
  - APIs used:
    - Login: `POST /api/v2/user/login`
    - Schedule lookup: `POST /api/v2/schedule/betweenDates`
    - Membership lookup: `GET /api/v2/boxes/{box_id}/memberships/{membership_type}/false`
    - Register: `POST /api/v2/scheduleUser/insert`
  - Status 516 is treated as “class is full.”
  - Sends Alertzy notifications for each class success or failure.

## Setup
1) Install Python deps:
```bash
pip install -r requirements.txt
```

2) Configure environment (local or App Settings):
- `ARBOX_USER_EMAIL`
- `ARBOX_USER_PASSWORD`
- `ALERTZY_ACCOUNT_KEY`
- Optional: `TZ` (e.g., `Asia/Jerusalem`) or set `timezone` in `data/config.py`.

`local.settings.json` is provided for local runs (fill in your values).

## Running
- Azure Function locally (requires Azure Functions Core Tools):
```bash
func start
```
- One-off manual run (immediate):
```bash
python arboxrun.py
```
This follows the same wait/notify/enroll flow using the default constants.

## Important files
- `arboxrun.py` — main logic, wait-and-notify loop, multi-class enrollment flow.
- `TimerEnroll/function.json` — timer schedule (Thursday 15:00).
- `TimerEnroll/__init__.py` — Azure Function entrypoint (sends per-class notifications).
- `data/config.py` — loads env vars; timezone fallback (`TZ`).
- `lib/push_notification.py` — Alertzy helper.

## Notes
- Python Azure Functions run on Linux; timezone is handled by the dual UTC triggers plus the TZ guard. Keep `TZ` (e.g., `Asia/Jerusalem`) set in App Settings.
- Alerts are sent for start, waiting, and per-class success/failure when `ALERTZY_ACCOUNT_KEY` is set.
- If you run `python arboxrun.py` directly, it follows the same wait/notify/enroll flow.
