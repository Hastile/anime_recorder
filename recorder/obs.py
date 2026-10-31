"""OBS WebSocket(v5) 제어. OBS가 꺼져 있어도 서버가 죽지 않도록 감싼다."""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class ObsError(RuntimeError):
    pass


@dataclass
class ObsStatus:
    connected: bool
    recording: bool = False
    paused: bool = False
    duration_ms: int = 0
    bytes: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class ObsController:
    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0):
        self._args = dict(host=host, port=port, password=password, timeout=timeout)
        self._client = None
        self._lock = threading.Lock()

    def _get(self):
        if self._client is None:
            import obsws_python as obs  # 지연 import: 테스트/데모 모드에서는 불필요

            try:
                self._client = obs.ReqClient(**self._args)
            except Exception as e:  # 연결 거부, 비밀번호 오류 등
                raise ObsError(f"OBS에 연결할 수 없습니다: {e}") from e
        return self._client

    def _call(self, name: str, *args):
        with self._lock:
            client = self._get()
            try:
                return getattr(client, name)(*args)
            except Exception as e:
                # 연결이 끊긴 경우 다음 호출에서 다시 연결
                try:
                    client.disconnect()
                except Exception:
                    pass
                self._client = None
                raise ObsError(str(e)) from e

    def status(self) -> ObsStatus:
        try:
            r = self._call("get_record_status")
        except ObsError as e:
            return ObsStatus(connected=False, error=str(e))
        return ObsStatus(
            connected=True,
            recording=bool(r.output_active),
            paused=bool(getattr(r, "output_paused", False)),
            duration_ms=int(getattr(r, "output_duration", 0) or 0),
            bytes=int(getattr(r, "output_bytes", 0) or 0),
        )

    def start(self) -> None:
        self._call("start_record")

    def stop(self) -> str | None:
        r = self._call("stop_record")
        return getattr(r, "output_path", None)

    def pause(self) -> None:
        self._call("pause_record")

    def resume(self) -> None:
        self._call("resume_record")

    def record_directory(self) -> str | None:
        try:
            return self._call("get_record_directory").record_directory
        except ObsError:
            return None

    def _scene_name(self) -> str:
        scene = self._call("get_current_program_scene")
        return getattr(scene, "scene_name", None) or scene.current_program_scene_name

    def scene_items(self) -> list[dict]:
        return list(self._call("get_scene_item_list", self._scene_name()).scene_items)

    def inputs(self) -> list[dict]:
        return list(self._call("get_input_list").inputs)

    def is_muted(self, name: str) -> bool:
        return bool(self._call("get_input_mute", name).input_muted)

    def set_window(self, input_name: str, window: str) -> None:
        """윈도우 캡처 / 응용 프로그램 오디오 캡처 소스가 잡을 창을 지정."""
        self._call("set_input_settings", input_name, {"window": window}, True)

    def screenshot(self, width: int = 640) -> bytes | None:
        name = self._scene_name()
        height = max(8, width * 9 // 16)
        r = self._call("get_source_screenshot", name, "jpg", width, height, 70)
        data = r.image_data
        if "," in data:
            data = data.split(",", 1)[1]
        return base64.b64decode(data)


class FakeObs:
    """--demo 모드와 테스트용 가짜 OBS."""

    def __init__(self, record_dir: str | Path = "."):
        self.recording = False
        self.paused = False
        self._started = 0.0
        self._paused_total = 0.0
        self._paused_at = 0.0
        self.record_dir = str(record_dir)
        self.output_path: str | None = None
        self.clock = time.monotonic
        self.items = [{"sourceName": "브라우저", "inputKind": "window_capture", "sceneItemEnabled": True}]
        self.input_list = [{"inputName": "브라우저", "inputKind": "window_capture"},
                           {"inputName": "브라우저 소리", "inputKind": "wasapi_process_output_capture"}]
        self.muted: dict[str, bool] = {}
        self.windows: dict[str, str] = {}

    def _elapsed(self) -> float:
        if not self.recording:
            return 0.0
        end = self._paused_at if self.paused else self.clock()
        return end - self._started - self._paused_total

    def status(self) -> ObsStatus:
        return ObsStatus(
            connected=True,
            recording=self.recording,
            paused=self.paused,
            duration_ms=int(self._elapsed() * 1000),
            bytes=int(self._elapsed() * 1_500_000),
        )

    def start(self) -> None:
        if self.recording:
            raise ObsError("이미 녹화 중입니다")
        self.recording, self.paused = True, False
        self._started, self._paused_total = self.clock(), 0.0

    def stop(self) -> str | None:
        if not self.recording:
            raise ObsError("녹화 중이 아닙니다")
        self.recording = False
        return self.output_path

    def pause(self) -> None:
        self.paused, self._paused_at = True, self.clock()

    def resume(self) -> None:
        self._paused_total += self.clock() - self._paused_at
        self.paused = False

    def record_directory(self) -> str | None:
        return self.record_dir

    def scene_items(self) -> list[dict]:
        return self.items

    def inputs(self) -> list[dict]:
        return self.input_list

    def is_muted(self, name: str) -> bool:
        return self.muted.get(name, False)

    def set_window(self, input_name: str, window: str) -> None:
        self.windows[input_name] = window

    def screenshot(self, width: int = 640) -> bytes | None:
        return None
