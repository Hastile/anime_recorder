"""녹화기 본체: 폰에서 보낸 페이지를 PC에서 열어 원본 화질로 녹화한다."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .config import Config
from .isolation import (GLOBAL_AUDIO_KINDS, BrowserWindow, WindowControl, build_browser_cmd,
                        check_obs_setup, find_browser, popen_kwargs, profile_pids)
from .obs import ObsError
from .sessions import Session, SessionStore

log = logging.getLogger(__name__)

VIDEO_EXT = {".mkv", ".mp4", ".mov", ".flv", ".ts", ".m4v"}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RecorderError(RuntimeError):
    pass


def safe_filename(title: str, limit: int = 120) -> str:
    name = _BAD_CHARS.sub("_", title).strip().rstrip(". ")
    return name[:limit] or "녹화"


def parse_time(text: str | float | None) -> float | None:
    """'12:34', '1:02:03', '95' → 초. 비어 있으면 None."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text)
    parts = text.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError("시작 위치는 12:34 같은 형식으로 입력하세요")
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


class LegacyPlayer:
    """Playwright가 없을 때: 브라우저만 띄우고 영상 조작은 하지 않는다."""

    controllable = False

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def open(self, url: str) -> None:
        b = self.cfg.browser
        exe = find_browser(b.executable)
        if not exe:
            raise RecorderError("녹화용 브라우저(크롬/엣지)를 찾을 수 없습니다. config.toml의 browser.executable을 지정하세요")
        subprocess.Popen(build_browser_cmd(
            exe, self.cfg.data_dir / "browser-profile", url,
            size=b.window_size or None, position=b.window_position or None, extra=b.extra_args),
            **popen_kwargs())

    def wait_for_video(self, timeout: float = 20) -> bool:
        return False

    def state(self) -> dict | None:
        return None

    def close(self) -> None:
        pass


def make_player(cfg: Config):
    try:
        import playwright  # noqa: F401
    except ImportError:
        log.warning("playwright가 없어 영상 자동 조작 없이 동작합니다 (pip install playwright)")
        return LegacyPlayer(cfg)
    from .browser import BrowserController

    b = cfg.browser
    ctl = BrowserController(find_browser(b.executable), cfg.data_dir / "browser-profile",
                            size=b.window_size or None, position=b.window_position or None,
                            extra_args=b.extra_args)
    ctl.controllable = True
    return ctl


class Recorder:
    def __init__(self, cfg: Config, obs, *, player=None, sleep=time.sleep,
                 windows: WindowControl | None = None, pid_finder=None):
        self.cfg = cfg
        self.obs = obs
        self.player = player if player is not None else make_player(cfg)
        self.win = windows or WindowControl()
        self._pid_finder = pid_finder or (lambda: profile_pids(cfg.data_dir / "browser-profile"))
        self._sleep = sleep
        self.sessions = SessionStore(cfg.data_dir / "sessions")
        self.browser: BrowserWindow | None = None
        self._isolation: tuple[float, list[dict]] = (0.0, [])
        self._lock = threading.RLock()
        self.active_id: str | None = None
        self._active_since = 0.0
        self.stop_on_end = False
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
            # OBS에서 직접 녹화를 멈춘 경우 정리
            if (self.active_id and st.connected and not st.recording and not self.starting
                    and time.monotonic() - self._active_since > 5):
                self._finish(output_path=None, duration=None, note="OBS에서 녹화가 중지됨")
            active = self.sessions.get(self.active_id) if self.active_id else None
        rec_dir = self.obs.record_directory() if st.connected else None
        disk = None
        if rec_dir and Path(rec_dir).exists():
            u = shutil.disk_usage(rec_dir)
            disk = {"free": u.free, "total": u.total}
        return {
            "obs": st.as_dict(),
            "starting": self.starting,
            "session": active.__dict__.copy() if active else None,
            "player": self.player.state() if (active or self.starting) else None,
            "controllable": getattr(self.player, "controllable", False),
            "stop_on_end": self.stop_on_end,
            "disk": disk,
            "events": list(self.events)[:8],
            "isolation": self.isolation() if st.connected else [],
            "browser_window": self.browser.title if self.browser else None,
        }

    # ---- 녹화 -------------------------------------------------------
    def start(self, url: str = "", title: str = "", start_at: float | None = None,
              stop_on_end: bool = True) -> Session:
        with self._lock:
            if self.starting or self.obs.status().recording:
                raise RecorderError("이미 녹화 중입니다")
            self.starting = True
        try:
            playing = False
            if url:
                playing = self._prepare(url, start_at)
                if not title:
                    st = self.player.state() or {}
                    title = st.get("title") or ""
            try:
                self.obs.start()
            except ObsError as e:
                raise RecorderError(str(e))
            if playing:
                # 녹화가 시작된 다음 재생해야 앞부분이 잘리지 않는다
                try:
                    self.player.play()
                except Exception as e:  # noqa: BLE001
                    self.event(f"재생 시작 실패: {e}", "error")
            with self._lock:
                s = self.sessions.create(title=title, url=url, start_at=start_at)
                self.active_id, self._active_since = s.id, time.monotonic()
                self.stop_on_end = bool(stop_on_end and playing)
            self.event(f"녹화 시작: {s.title}")
            return s
        finally:
            self.starting = False

    def _prepare(self, url: str, start_at: float | None) -> bool:
        """페이지를 열고 영상을 시작 위치에 맞춰 멈춰 둔다. 영상 조작이 되면 True."""
        b = self.cfg.browser
        prev_focus = self.win.foreground()
        before = {w.hwnd for w in self._browser_windows()}
        try:
            self.player.open(url)
        except Exception as e:  # noqa: BLE001
            raise RecorderError(str(e))
        found = None
        for _ in range(int(b.wait_seconds / 0.5) or 1):
            found = next((w for w in self._browser_windows() if w.hwnd not in before), None)
            if found:
                break
            self._sleep(0.5)
        if found and b.send_to_back:
            # 녹화 창은 뒤로, 포커스는 하던 작업으로 (PC 작업 방해 X)
            self.win.send_to_back(found.hwnd)
            self.win.focus(prev_focus)

        playing = False
        if getattr(self.player, "controllable", False):
            if self.player.wait_for_video(b.video_timeout):
                try:
                    self.player.play(start_at)  # 버퍼링/메타데이터 로드
                    self.player.pause()
                    if start_at is not None:
                        self.player.seek(to=start_at)
                    if b.fill_video:
                        self.player.fill()
                    playing = True
                except Exception as e:  # noqa: BLE001
                    self.event(f"영상 조작 실패: {e}", "error")
            else:
                self.event("페이지에서 영상을 찾지 못해 화면 그대로 녹화합니다", "error")
        else:
            self._sleep(b.wait_seconds)

        if found:
            # 페이지가 로드된 뒤의 제목으로 다시 읽어서 OBS 소스에 연결
            self.browser = next((w for w in self._browser_windows() if w.hwnd == found.hwnd), found)
            self._bind_obs(self.browser)
        elif self.win.ok:
            self.event("녹화 창을 찾지 못해 OBS 소스를 자동 연결하지 못했습니다", "error")
        return playing

    def stop(self, note: str = "녹화 종료") -> Session | None:
        st = self.obs.status()
        if not st.recording:
            raise RecorderError("녹화 중이 아닙니다")
        duration = st.duration_ms / 1000
        try:
            path = self.obs.stop()
        except ObsError as e:
            raise RecorderError(str(e))
        with self._lock:
            return self._finish(output_path=path, duration=duration, note=note)

    def pause(self) -> None:
        """영상과 녹화를 함께 일시정지."""
        try:
            self.obs.pause()
        except ObsError as e:
            raise RecorderError(str(e))
        self._player_call("pause")
        self.event("일시정지")

    def resume(self) -> None:
        self._player_call("play")
        try:
            self.obs.resume()
        except ObsError as e:
            raise RecorderError(str(e))
        self.event("다시 녹화")

    def seek(self, delta: float | None = None, to: float | None = None) -> float:
        if not getattr(self.player, "controllable", False):
            raise RecorderError("이 브라우저는 영상 조작을 지원하지 않습니다")
        try:
            return self.player.seek(delta=delta, to=to)
        except Exception as e:  # noqa: BLE001
            raise RecorderError(str(e))

    def _player_call(self, name: str) -> None:
        if getattr(self.player, "controllable", False):
            try:
                getattr(self.player, name)()
            except Exception as e:  # noqa: BLE001
                self.event(f"영상 조작 실패: {e}", "error")

    def _finish(self, output_path: str | None, duration: float | None, note: str) -> Session | None:
        if not self.active_id:
            return None
        s = self.sessions.get(self.active_id)
        self.active_id, self.stop_on_end = None, False
        s.ended_at = datetime.now().isoformat(timespec="seconds")
        s.output_path = output_path or self._guess_latest_recording(s)
        s.duration = duration
        self.sessions.save(s)
        self.event(f"{note}: {Path(s.output_path).name if s.output_path else '파일 경로 미확인'}")
        if self.cfg.browser.close_on_stop and (self.browser or s.url):
            self.player.close()
            self.browser = None
        if s.output_path and self.cfg.recording.rename_to_title and s.title:
            threading.Thread(target=self._rename_file_later, args=(s.id,), daemon=True).start()
        return s

    def _guess_latest_recording(self, s: Session) -> str | None:
        rec_dir = self.obs.record_directory()
        if not rec_dir or not Path(rec_dir).is_dir():
            return None
        started = datetime.fromisoformat(s.started_at).timestamp() - 30
        files = [p for p in Path(rec_dir).iterdir()
                 if p.suffix.lower() in VIDEO_EXT and p.stat().st_mtime >= started]
        return str(max(files, key=lambda p: p.stat().st_mtime)) if files else None

    # ---- 이름 변경 ----------------------------------------------------
    def rename(self, sid: str, title: str) -> Session:
        title = title.strip()
        if not title:
            raise ValueError("이름을 입력하세요")
        with self._lock:
            s = self.sessions.get(sid)
            s.title = title
            self.sessions.save(s)
            if sid == self.active_id:
                return s  # 녹화가 끝나면 파일 이름에 반영
        return self._rename_file(sid)

    def _rename_file(self, sid: str) -> Session:
        with self._lock:
            s = self.sessions.get(sid)
            if not s.output_path or not Path(s.output_path).exists():
                return s
            src = Path(s.output_path)
            base = safe_filename(s.title)
            if src.stem == base:
                return s
            dst = src.with_name(base + src.suffix)
            n = 2
            while dst.exists():
                dst = src.with_name(f"{base} ({n}){src.suffix}")
                n += 1
            os.replace(src, dst)
            s.output_path = str(dst)
            self.sessions.save(s)
            return s

    def _rename_file_later(self, sid: str) -> None:
        # OBS가 파일을 다 닫을 때까지 잠깐 기다리며 재시도
        for _ in range(10):
            try:
                s = self._rename_file(sid)
                self.event(f"저장: {Path(s.output_path).name}")
                return
            except PermissionError:
                time.sleep(1)
            except OSError as e:
                self.event(f"이름 변경 실패: {e}", "error")
                return
        self.event("파일이 사용 중이라 이름을 바꾸지 못했습니다. 녹화본 목록에서 다시 시도하세요", "error")

    # ---- 녹화 전용 브라우저 창 -----------------------------------------
    def _browser_windows(self) -> list[BrowserWindow]:
        return self.win.find_windows(self._pid_finder())

    def _bind_obs(self, w: BrowserWindow) -> None:
        bound = False
        for name in (self.cfg.obs.video_source, self.cfg.obs.audio_source):
            if not name:
                continue
            try:
                self.obs.set_window(name, w.obs_value)
                bound = True
            except ObsError as e:
                self.event(f"OBS 소스 '{name}' 연결 실패: {e}", "error")
        if bound:
            self.event(f"녹화 창 연결: {w.title} ({w.exe})")
        self._isolation = (0.0, [])

    def _guard_browser(self) -> None:
        """녹화 중 창이 최소화되면 캡처가 멈추므로 맨 뒤에서 다시 복원한다."""
        w = self.browser
        if not w:
            return
        if not self.win.exists(w.hwnd):
            self.browser = None
            self.event("녹화 창이 닫혔습니다", "error")
        elif self.win.is_minimized(w.hwnd):
            self.win.restore_behind(w.hwnd)
            self.event("녹화 창이 최소화되어 맨 뒤에 다시 띄웠습니다")

    # ---- 격리 점검 ---------------------------------------------------
    def isolation(self, max_age: float = 10.0) -> list[dict]:
        ts, cached = self._isolation
        if time.monotonic() - ts < max_age and cached:
            return cached
        try:
            inputs = self.obs.inputs()
            muted = {i["inputName"]: self.obs.is_muted(i["inputName"])
                     for i in inputs if i.get("inputKind") in GLOBAL_AUDIO_KINDS}
            report = check_obs_setup(self.obs.scene_items(), inputs, muted,
                                     self.cfg.obs.video_source, self.cfg.obs.audio_source)
        except ObsError as e:
            return [{"level": "warn", "msg": f"OBS 설정을 읽지 못했습니다: {e}"}]
        exe = find_browser(self.cfg.browser.executable)
        if exe:
            report.append({"level": "ok", "msg": f"녹화 전용 브라우저: {Path(exe).name} (별도 프로필)"})
        else:
            report.append({"level": "warn", "msg": "녹화용 브라우저(크롬/엣지)를 찾을 수 없습니다"})
        if not getattr(self.player, "controllable", False):
            report.append({"level": "warn", "msg": "영상 자동 조작이 꺼져 있습니다 (pip install playwright)"})
        if not (self.cfg.obs.video_source or self.cfg.obs.audio_source):
            report.append({"level": "warn", "msg": "OBS 소스 자동 연결이 꺼져 있습니다 "
                           "(config.toml의 obs.video_source / audio_source)"})
        self._isolation = (time.monotonic(), report)
        return report

    # ---- 백그라운드 루프 ------------------------------------------------
    def tick(self) -> None:
        if not self.active_id:
            return
        if self.stop_on_end:
            st = self.player.state()
            if st and st.get("found"):
                dur = st.get("duration")
                ended = st.get("ended") or (dur and st.get("time", 0) >= dur - 0.3 and st.get("paused"))
                if ended:
                    try:
                        self.stop(note="영상이 끝나 녹화를 멈췄습니다")
                    except RecorderError as e:
                        self.stop_on_end = False
                        self.event(f"자동 정지 실패: {e}", "error")
                    return
        self.status()  # OBS에서 직접 멈춘 경우 감지
        self._guard_browser()

    def run_loop(self, stop_event: threading.Event, interval: float = 1.0) -> None:
        while not stop_event.wait(interval):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 루프는 절대 죽지 않게
                log.exception("백그라운드 작업 오류")
