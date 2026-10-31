"""녹화 세션과 마커 저장소. 세션마다 JSON 파일 하나로 저장한다."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Marker:
    id: str
    kind: str  # "point" | "range"
    label: str = ""
    t: float | None = None  # point: 녹화 시작 기준 초
    start: float | None = None  # range
    end: float | None = None  # range (None이면 아직 열려 있음)
    clip_path: str | None = None
    clip_error: str | None = None

    def window(self, pre: float, post: float, total: float | None) -> tuple[float, float]:
        """잘라낼 구간 [시작, 끝] (초)."""
        if self.kind == "point":
            s, e = self.t - pre, self.t + post
        else:
            s = self.start
            e = self.end if self.end is not None else (total or self.start + post)
        s = max(0.0, s)
        if total:
            e = min(total, e)
        return s, max(s, e)


@dataclass
class Session:
    id: str
    title: str = ""
    url: str = ""
    started_at: str = ""
    ended_at: str | None = None
    output_path: str | None = None
    duration: float | None = None
    markers: list[Marker] = field(default_factory=list)
    export_state: str = "idle"  # idle | running | done | error
    export_message: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        d = dict(d)
        d["markers"] = [Marker(**m) for m in d.get("markers", [])]
        return cls(**d)

    def open_range(self) -> Marker | None:
        for m in reversed(self.markers):
            if m.kind == "range" and m.end is None:
                return m
        return None


class SessionStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, sid: str) -> Path:
        if not sid.replace("-", "").isalnum():
            raise KeyError(sid)
        return self.dir / f"{sid}.json"

    def create(self, title: str = "", url: str = "") -> Session:
        now = datetime.now()
        s = Session(
            id=now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
            title=title or now.strftime("%m/%d %H:%M 녹화"),
            url=url,
            started_at=now.isoformat(timespec="seconds"),
        )
        self.save(s)
        return s

    def save(self, s: Session) -> None:
        with self._lock:
            tmp = self._path(s.id).with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path(s.id))

    def get(self, sid: str) -> Session:
        p = self._path(sid)
        if not p.exists():
            raise KeyError(sid)
        return Session.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list(self, limit: int = 50) -> list[Session]:
        files = sorted(self.dir.glob("*.json"), reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(Session.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue
        return out

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)

    @staticmethod
    def new_marker(**kw) -> Marker:
        return Marker(id=uuid.uuid4().hex[:8], **kw)
