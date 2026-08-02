from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func

from mower.controller import run_control_cycle


app = func.FunctionApp()
LOGGER = logging.getLogger("ssv53.azure.platzpflege")


@app.timer_trigger(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
@app.retry(
    strategy="fixed_delay",
    max_retry_count="3",
    delay_interval="00:00:10",
)
def ssv53_mower_timer(
    timer: func.TimerRequest,
    context: func.Context,
) -> None:
    """Sicherer Azure-Heartbeat als erste Migrationsstufe."""

    result = run_control_cycle(
        now_utc=datetime.now(timezone.utc),
        environment=os.environ,
        past_due=bool(timer.past_due),
    )
    payload = result.to_dict()
    payload["invocation_id"] = context.invocation_id

    retry_context = getattr(context, "retry_context", None)
    payload["retry_count"] = (
        getattr(retry_context, "retry_count", 0)
        if retry_context is not None
        else 0
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )
    if timer.past_due:
        LOGGER.warning("SSV53_CONTROL_CYCLE_PAST_DUE %s", serialized)
    else:
        LOGGER.info("SSV53_CONTROL_CYCLE %s", serialized)
