"""OBS, 세션, 클립, 예약을 묶는 녹화기 본체."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

from . import clips
from .config import Config
from .obs import ObsError
from .scheduler import ScheduleStore
from .sessions import Session, SessionStore

log = logging.getLogger(__name__)

VIDEO_EXT = {".mkv", ".mp4", ".mov", ".flv", ".ts", ".m4v"}


class RecorderError(RuntimeError):
    pass


def open_url(cfg: Config, url: str) -> None:
    if cfg.browser.command:
        cmd = [part.replace("{url}", url) for part in cfg.browser.command]
        if not any("{url}" in p for p in cfg.browser.command):
            cmd.append(url)
        subprocess.Popen(cmd)
    else:
        webbrowser.open(url, new=1)


class Recorder:
    def __init__(self, cfg: Config, obs, *, opener=open_url, sleep=time.sleep):
        self.cfg = cfg
        self.obs = obs
        self.sessions = SessionStore(cfg.data_dir / "sessions")
        self.schedules = ScheduleStore(cfg.data_dir / "schedules.json")
        self._opener = opener
        self._sleep = sleep
        self._lock = threading.RLock()
        self.active_id: str | None = None
        self._active_since = 0.0
        self.auto_stop_at: float | None = None
        self.starting = False
        self.events: deque[dict] = deque(maxlen=40)

    # ---- 이벤트 로그 -------------------------------------------------
    def event(self, msg: str, level: str = "info") -> None:
        getattr(log, "error" if level == "error" else "info")(msg)
        self.events.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level})

    # ---- 상태 -------------------------------------------------------
    def status(self) -> dict:
        st = self.obs.status()
        with self._lock:
            # OBS에서 직접 녹화를 멈춘 경우 세션 정리
            if (self.active_id and st.connected and not st.recording and not self.starting
                    and time.monotonic() - self._active_since > 5):
                self._finish(output_path=None, duration=None, note="OBS에서 녹화가 중지됨")
            active = self.sessions.get(self.active_id) if self.active_id else None
        rec_dir = self.obs.record_directory() if st.connected else None
        disk = None
        if rec_dir and Path(rec_dir).exists():
            u = shutil.disk_usage(rec_dir)
            disk = {"free": u.free, "total": u.total}
        remaining = None
        if self.auto_stop_at:
            remaining = max(0, int(self.auto_stop_at - time.time()))
        return {
            "obs": st.as_dict(),
            "starting": self.starting,
            "session": _session_dict(active) if active else None,
            "auto_stop_in": remaining,
            "disk": disk,
            "events": list(self.events)[:10],
            "quick_labels": self.cfg.clips.quick_labels,
        }

    # ---- 녹화 제어 ---------------------------------------------------
    def start(self, url: str = "", title: str = "", auto_stop_min: float | None = None) -> Session:
        with self._lock:
            if self.starting or self.obs.status().recording:
                raise RecorderError("이미 녹화 중입니다")
            self.starting = True
        try:
            if url:
                try:
                    self._opener(self.cfg, url)
                except OSError as e:
                    raise RecorderError(f"브라우저를 열 수 없습니다: {e}")
                self._sleep(self.cfg.browser.wait_seconds)
            try:
                self.obs.start()
            except ObsError as e:
                raise RecorderError(str(e))
            with self._lock:
                s = self.sessions.create(title=title, url=url)
                self.active_id, self._active_since = s.id, time.monotonic()
                self.auto_stop_at = time.time() + auto_stop_min * 60 if auto_stop_min else None
            self.event(f"녹화 시작: {s.title}")
            return s
        finally:
            self.starting = False

    def stop(self) -> Session | None:
        st = self.obs.status()
        if not st.recording:
            raise RecorderError("녹화 중이 아닙니다")
        duration = st.duration_ms / 1000
        try:
            path = self.obs.stop()
        except ObsError as e:
            raise RecorderError(str(e))
        with self._lock:
            return self._finish(output_path=path, duration=duration, note="녹화 종료")

    def pause(self) -> None:
        try:
            self.obs.pause()
        except ObsError as e:
            raise RecorderError(str(e))
        self.event("일시정지")

    def resume(self) -> None:
        try:
            self.obs.resume()
        except ObsError as e:
            raise RecorderError(str(e))
        self.event("다시 녹화")

    def _ensure_session(self) -> Session:
        """OBS에서 직접 시작한 녹화에도 마커를 달 수 있게 세션을 만든다."""
        if self.active_id:
            return self.sessions.get(self.active_id)
        s = self.sessions.create(title="")
        self.active_id, self._active_since = s.id, time.monotonic()
        self.event("OBS 녹화를 감지해 새 세션을 만들었습니다")
        return s

    def _finish(self, output_path: str | None, duration: float | None, note: str) -> Session | None:
        if not self.active_id:
            return None
        s = self.sessions.get(self.active_id)
        self.active_id, self.auto_stop_at = None, None
        s.ended_at = datetime.now().isoformat(timespec="seconds")
        s.output_path = output_path or self._guess_latest_recording(s)
        s.duration = duration
        open_m = s.open_range()
        if open_m:
            open_m.end = duration
        self.sessions.save(s)
        self.event(f"{note}: {Path(s.output_path).name if s.output_path else '파일 경로 미확인'}")
        if s.markers and self.cfg.clips.auto_export and s.output_path:
            self.export_async(s.id)
        return s

    def _guess_latest_recording(self, s: Session) -> str | None:
        rec_dir = self.obs.record_directory()
        if not rec_dir or not Path(rec_dir).is_dir():
            return None
        started = datetime.fromisoformat(s.started_at).timestamp() - 30
        files = [p for p in Path(rec_dir).iterdir()
                 if p.suffix.lower() in VIDEO_EXT and p.stat().st_mtime >= started]
        return str(max(files, key=lambda p: p.stat().st_mtime)) if files else None

    # ---- 마커 -------------------------------------------------------
    def mark(self, kind: str, label: str = "") -> dict:
        st = self.obs.status()
        if not st.recording:
            raise RecorderError("녹화 중일 때만 표시할 수 있습니다")
        t = st.duration_ms / 1000
        with self._lock:
            s = self._ensure_session()
            if kind == "point":
                m = self.sessions.new_marker(kind="point", t=t, label=label)
                s.markers.append(m)
            elif kind == "in":
                if s.open_range():
                    raise RecorderError("이미 구간이 열려 있습니다. 먼저 '구간 끝'을 누르세요")
                m = self.sessions.new_marker(kind="range", start=t, label=label)
                s.markers.append(m)
            elif kind == "out":
                m = s.open_range()
                if not m:
                    raise RecorderError("열린 구간이 없습니다")
                m.end = t
                if label:
                    m.label = label
            else:
                raise RecorderError("알 수 없는 마커 종류")
            self.sessions.save(s)
            return m.__dict__.copy()

    def edit_marker(self, sid: str, mid: str, label: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            _find_marker(s, mid).label = label
            self.sessions.save(s)

    def delete_marker(self, sid: str, mid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            _find_marker(s, mid)
            s.markers = [m for m in s.markers if m.id != mid]
            self.sessions.save(s)

    # ---- 클립 내보내기 -----------------------------------------------
    def export_async(self, sid: str) -> None:
        with self._lock:
            s = self.sessions.get(sid)
            if s.export_state == "running":
                raise RecorderError("이미 클립을 만드는 중입니다")
            if sid == self.active_id:
                raise RecorderError("녹화가 끝난 뒤에 클립을 만들 수 있습니다")
            s.export_state, s.export_message = "running", ""
            self.sessions.save(s)
        threading.Thread(target=self._export, args=(sid,), daemon=True).start()

    def _export(self, sid: str) -> None:
        s = self.sessions.get(sid)
        try:
            ok, fail = clips.export_session(s, self.cfg.clips)
            s.export_state = "done" if not fail else "error"
            s.export_message = f"클립 {ok}개 저장" + (f", {fail}개 실패" if fail else "")
            self.event(f"{s.title}: {s.export_message}", "info" if not fail else "error")
        except Exception as e:  # noqa: BLE001 - 사용자에게 그대로 보여준다
            s.export_state, s.export_message = "error", str(e)
            self.event(f"클립 실패: {e}", "error")
        with self._lock:
            # 내보내는 동안 바뀐 라벨 등은 유지하고 결과만 반영
            latest = self.sessions.get(sid)
            by_id = {m.id: m for m in s.markers}
            for m in latest.markers:
                if m.id in by_id:
                    m.clip_path, m.clip_error = by_id[m.id].clip_path, by_id[m.id].clip_error
            latest.export_state, latest.export_message = s.export_state, s.export_message
            self.sessions.save(latest)

    # ---- 백그라운드 루프 (예약 / 자동 정지) -----------------------------
    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if self.auto_stop_at and time.time() >= self.auto_stop_at:
            try:
                self.stop()
            except RecorderError as e:
                self.auto_stop_at = None
                self.event(f"자동 정지 실패: {e}", "error")
        for job in self.schedules.pop_due(now):
            try:
                self.start(url=job.url, title=job.name, auto_stop_min=job.duration_min)
                self.event(f"예약 녹화 시작: {job.name}")
            except RecorderError as e:
                self.event(f"예약 녹화 '{job.name}' 실패: {e}", "error")
        # OBS에서 직접 멈춘 경우 감지
        if self.active_id:
            self.status()

    def run_loop(self, stop_event: threading.Event, interval: float = 5.0) -> None:
        while not stop_event.wait(interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 루프는 절대 죽지 않게
                log.exception("백그라운드 작업 오류")


def _find_marker(s: Session, mid: str):
    for m in s.markers:
        if m.id == mid:
            return m
    raise KeyError(mid)


def _session_dict(s: Session) -> dict:
    from dataclasses import asdict

    return asdict(s)
