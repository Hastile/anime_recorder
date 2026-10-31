"""녹화 기록. 녹화마다 JSON 파일 하나로 저장한다."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path


@dataclass
class Session:
    id: str
    title: str = ""
    url: str = ""
    started_at: str = ""
    ended_at: str | None = None
    output_path: str | None = None
    duration: float | None = None
    start_at: float | None = None  # 영상 시작 위치(초)

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


class SessionStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, sid: str) -> Path:
        if not sid.replace("-", "").isalnum():
            raise KeyError(sid)
        return self.dir / f"{sid}.json"

    def create(self, title: str = "", url: str = "", start_at: float | None = None) -> Session:
        now = datetime.now()
        s = Session(
            id=now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
            title=title or now.strftime("%Y-%m-%d %H-%M 녹화"),
            url=url,
            started_at=now.isoformat(timespec="seconds"),
            start_at=start_at,
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

    def list(self, limit: int = 100) -> list[Session]:
        out = []
        for f in sorted(self.dir.glob("*.json"), reverse=True)[:limit]:
            try:
                out.append(Session.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue
        return out

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)
