"""
APScheduler wrapper — schedules and reschedules daily sync jobs.

Supports 1, 2, 3, or 4 syncs per day spaced evenly from a configured start time.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


def start_scheduler(app) -> None:
    """
    Initialize APScheduler and add sync jobs based on current settings.

    Called from the FastAPI startup event. Attaches the scheduler instance
    to app.state so it can be accessed for rescheduling.

    Args:
        app: The FastAPI application instance.
    """
    raise NotImplementedError("Proprietary implementation")


def reschedule(app) -> None:
    """
    Remove all existing sync jobs and re-add them from current settings.

    Called after the user saves new sync settings so the schedule updates
    without requiring a server restart.

    Args:
        app: The FastAPI application instance (provides app.state.scheduler).
    """
    raise NotImplementedError("Proprietary implementation")
