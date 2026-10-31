"""예약 녹화. 매주 요일 반복 또는 특정 날짜 1회."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# 예약 시각이 지난 뒤 이 시간 안이면 시작한다 (PC가 잠깐 바빴던 경우 대비)
GRACE = timedelta(minutes=2)


@dataclass
class Job:
    id: str
    name: str
    time: str  # "HH:MM"
    duration_min: int = 30
    url: str = ""
    weekdays: list[int] = field(default_factory=list)  # 월=0 ... 일=6
    date: str | None = None  # "YYYY-MM-DD" (1회 예약)
    enabled: bool = True
    last_fired: str | None = None  # "YYYY-MM-DDTHH:MM"

    def _start_on(self, day: date) -> datetime:
        h, m = (int(x) for x in self.time.split(":"))
        return datetime(day.year, day.month, day.day, h, m)

    def _matches(self, day: date) -> bool:
        if self.date:
            return day.isoformat() == self.date
        return not self.weekdays or day.weekday() in self.weekdays

    def next_run(self, now: datetime) -> datetime | None:
        if not self.enabled:
            return None
        for offset in range(0, 8 if not self.date else 400):
            day = now.date() + timedelta(days=offset)
            if self._matches(day):
                start = self._start_on(day)
                if start + GRACE > now and start.isoformat(timespec="minutes") != self.last_fired:
                    return start
        return None

    def due(self, now: datetime) -> datetime | None:
        if not self.enabled or not self._matches(now.date()):
            return None
        start = self._start_on(now.date())
        key = start.isoformat(timespec="minutes")
        if start <= now < start + GRACE and self.last_fired != key:
            return start
        return None


def validate(job: Job) -> None:
    try:
        h, m = (int(x) for x in job.time.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except (ValueError, AssertionError):
        raise ValueError("시간은 HH:MM 형식이어야 합니다")
    if job.date:
        date.fromisoformat(job.date)
    if any(d not in range(7) for d in job.weekdays):
        raise ValueError("요일 값이 잘못되었습니다")
    if not 1 <= job.duration_min <= 24 * 60:
        raise ValueError("녹화 길이는 1분~24시간이어야 합니다")


class ScheduleStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.jobs: list[Job] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.jobs = [Job(**j) for j in raw]

    def _save(self) -> None:
        self.path.write_text(
            json.dumps([asdict(j) for j in self.jobs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, **kw) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], **kw)
        validate(job)
        with self._lock:
            self.jobs.append(job)
            self._save()
        return job

    def update(self, jid: str, **kw) -> Job:
        with self._lock:
            job = self.get(jid)
            for k, v in kw.items():
                if k in ("name", "time", "duration_min", "url", "weekdays", "date", "enabled"):
                    setattr(job, k, v)
            validate(job)
            self._save()
            return job

    def get(self, jid: str) -> Job:
        for j in self.jobs:
            if j.id == jid:
                return j
        raise KeyError(jid)

    def remove(self, jid: str) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != jid]
            self._save()

    def pop_due(self, now: datetime) -> list[Job]:
        """지금 시작해야 할 작업을 돌려주고 실행 처리한다."""
        due = []
        with self._lock:
            for j in self.jobs:
                start = j.due(now)
                if start:
                    j.last_fired = start.isoformat(timespec="minutes")
                    if j.date:
                        j.enabled = False
                    due.append(j)
            if due:
                self._save()
        return due
