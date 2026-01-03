import logging
from datetime import datetime, timedelta

import azure.functions as func

from arboxrun import RUN_TIME, now_with_tz, notify, parse_hhmm, run_coordinated_flow

app = func.FunctionApp()

# Allow the timer to fire a few minutes before the target time; run_coordinated_flow waits until RUN_TIME.
RUN_WINDOW_MINUTES = 5


@app.function_name(name="TimerEnroll")
@app.schedule(schedule="0 58 11,12 * * Thu", arg_name="mytimer", run_on_startup=False, use_monitor=True)
def timer_enroll(mytimer: func.TimerRequest) -> None:  # noqa: ANN001
    current = now_with_tz()
    target_dt = datetime.combine(current.date(), parse_hhmm(RUN_TIME), tzinfo=current.tzinfo)
    delta = target_dt - current

    if abs(delta) > timedelta(minutes=RUN_WINDOW_MINUTES):
        logging.info(
            "Timer fired at %s (tz=%s); delta to target is %s (abs %s), outside +/-%d-minute window. Skipping.",
            current.strftime("%Y-%m-%d %H:%M:%S"),
            current.tzinfo,
            delta,
            abs(delta),
            RUN_WINDOW_MINUTES,
        )
        return

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
        notify("Arbox run failed", str(exc))
        raise
