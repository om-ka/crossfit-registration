# Arbox Auto-Enroll (Azure Function)

Timer-triggered Azure Function that logs into Arbox and enrolls you into the next configured morning classes (default: Tuesday/Thursday at 06:00, box 28 / location 7). It posts a single Slack summary per run; classes it couldn't book are offered as buttons with the other times available that day.

## How it works
- Timer trigger: defined in `function_app.py` (Python v2 programming model), fires Thursdays at 11:58 and 12:58 UTC (cron `0 58 11,12 * * Thu`), and only proceeds when within a few minutes of 15:00 Israel time. This avoids DST drift and gives a 2-minute head start before the 15:00 run window.
- At trigger time, it waits until 15:00, then runs enrollment. Classes that aren't open yet are polled for up to 30 seconds.
- Slack buttons hit the `SlackActions` HTTP function (`/api/slack/actions`), which queues the click; `SlackActionWorker` registers and updates the message.
- Enrollment logic (`arboxrun.py`):
  - Targets next occurrences of the configured days (default: Tuesday/Thursday).
  - Start time: `06:00` (override with env)
  - Box ID: `28`
  - Location ID: `7`
  - Membership type: `1`
  - APIs used:
    - Login: `POST /api/v2/user/login`
    - Schedule lookup: `POST /api/v2/schedule/betweenDates`
    - Membership lookup: `GET /api/v2/boxes/{box_id}/memberships/{membership_type}/false`
    - Register: `POST /api/v2/scheduleUser/insert`
  - Status 516 is treated as “class is full.”

## Setup
1) Install Python deps:
```bash
pip install -r requirements.txt
```

2) Configure environment (local or App Settings):
- `ARBOX_USER_EMAIL`
- `ARBOX_USER_PASSWORD`
- `SLACK_BOT_TOKEN` (bot scope `chat:write`, bot invited to the channel)
- `SLACK_SIGNING_SECRET`
- `SLACK_CHANNEL_ID`
- Optional: `SLACK_ALLOWED_USER_IDS` (comma-separated Slack user ids allowed to press buttons)
- Optional: `TZ` (e.g., `Asia/Jerusalem`) or set `timezone` in `data/config.py`.
- Optional overrides:
  - `ARBOX_REGISTRATION_START_TIME` (e.g., `06:00`)
  - `ARBOX_REGISTRATION_DAYS` (comma-separated; each entry is `<day>` or `<day>:HH:MM`, e.g., `sun,tue:07:00,thu`)
- Recommended for the Python v2 programming model: `AzureWebJobsFeatureFlags=EnableWorkerIndexing` (set as an App Setting in Azure and in `local.settings.json` for local runs).

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
- `function_app.py` — Python v2 programming model entrypoint; timer schedule lives here.
- `arboxrun.py` — main logic, wait-and-notify loop, multi-class enrollment flow.
- `data/config.py` — loads env vars; timezone fallback (`TZ`).
- `lib/slack.py` — Slack posting and request signature verification.

## Notes
- Python Azure Functions run on Linux; timezone is handled by the dual UTC triggers plus the TZ guard. Keep `TZ` (e.g., `Asia/Jerusalem`) set in App Settings.
- Slack Interactivity Request URL: `https://<app>.azurewebsites.net/api/slack/actions?code=<function key>`.
- If you run `python arboxrun.py` directly, it follows the same wait/notify/enroll flow.
