from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .config import AppConfig
from .jobs import run_job
from .models import JobRunResult

_DEFAULT_SCHEDULES = {
    "process_extraction_queue": "*/5 * * * *",
    "process_promotion_queue": "*/5 * * * *",
    "expire_stale_episodes": "0 * * * *",
    "embed_backfill": "15 * * * *",
    "status_report": "0 * * * *",
    "wiki_embedding_neighbor_backfill": "20 * * * *",
    "wiki_cluster_validation": "25 * * * *",
    "wiki_pattern_discovery": "30 * * * *",
    "wiki_graph_rebuild": "35 * * * *",
    "wiki_lifecycle_audit": "40 * * * *",
    "wiki_importance_scoring": "45 * * * *",
    "wiki_health_report": "50 * * * *",
    "wiki_raw_revisitation": "0 2 * * *",
}


def _cron_dow_matches(value: int, dt: datetime) -> bool:
    python_weekday = dt.weekday()
    cron_weekday = (python_weekday + 1) % 7
    return cron_weekday == value


def _field_matches(field: str, current: int, *, dt: datetime | None = None, field_name: str = "") -> bool:
    field = field.strip()
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and current % step == 0
    if "," in field:
        return any(_field_matches(part, current, dt=dt, field_name=field_name) for part in field.split(","))
    if field_name == "dow":
        return dt is not None and _cron_dow_matches(int(field), dt)
    return current == int(field)


class CronSchedule:
    def __init__(self, expression: str) -> None:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"unsupported cron expression: {expression}")
        self.expression = expression
        self.minute, self.hour, self.day, self.month, self.weekday = parts

    def matches(self, dt: datetime) -> bool:
        return (
            _field_matches(self.minute, dt.minute)
            and _field_matches(self.hour, dt.hour)
            and _field_matches(self.day, dt.day)
            and _field_matches(self.month, dt.month)
            and _field_matches(self.weekday, dt.day, dt=dt, field_name="dow")
        )

    def next_after(self, base: datetime) -> datetime:
        candidate = base.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        deadline = candidate + timedelta(days=370)
        while candidate <= deadline:
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError(f"unable to compute next run for {self.expression}")


@dataclass(slots=True)
class JobEntry:
    name: str
    fn: Callable[[AppConfig], JobRunResult]
    schedule: str
    cron: CronSchedule
    next_due: datetime


class JobRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, JobEntry] = {}

    def register(self, name: str, fn: Callable[[AppConfig], JobRunResult], schedule: str) -> None:
        now = datetime.now(timezone.utc)
        cron = CronSchedule(schedule)
        self._entries[name] = JobEntry(
            name=name,
            fn=fn,
            schedule=schedule,
            cron=cron,
            next_due=cron.next_after(now),
        )

    def job_names(self) -> list[str]:
        return sorted(self._entries)

    def all_jobs(self) -> list[JobEntry]:
        return [self._entries[name] for name in self.job_names()]

    def due_jobs(self, now: datetime) -> list[JobEntry]:
        now_utc = now.astimezone(timezone.utc)
        due: list[JobEntry] = []
        for entry in self.all_jobs():
            if entry.next_due <= now_utc:
                due.append(entry)
                entry.next_due = entry.cron.next_after(now_utc)
        return due

    def run(self, config: AppConfig, job_name: str) -> JobRunResult:
        return self._entries[job_name].fn(config)


def _wrap_job(name: str) -> Callable[[AppConfig], JobRunResult]:
    def _runner(config: AppConfig) -> JobRunResult:
        return run_job(config, job_name=name, dry_run=False)

    return _runner


def build_registry(config: AppConfig) -> JobRegistry:
    registry = JobRegistry()
    configured = dict(_DEFAULT_SCHEDULES)
    if config.daemon is not None:
        configured.update(config.daemon.schedules)
    for name, schedule in configured.items():
        registry.register(name, _wrap_job(name), schedule)
    return registry
