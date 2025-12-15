import azure.functions as func

from arboxrun import run_coordinated_flow, notify


def main(mytimer: func.TimerRequest) -> None:  # noqa: ANN001
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
