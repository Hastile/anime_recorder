from pathlib import Path

import pytest

from recorder.config import Config
from recorder.core import Recorder
from recorder.isolation import BrowserWindow, build_browser_cmd, check_obs_setup, obs_window_string
from recorder.obs import FakeObs


class FakeWindows:
    """Win32 창 제어 흉내."""
    ok = True

    def __init__(self):
        self.windows: list[BrowserWindow] = []
        self.fg = 111  # 사용자가 작업 중인 창
        self.calls = []
        self.minimized = set()

    def foreground(self):
        return self.fg

    def find_windows(self, pids):
        return list(self.windows)

    def send_to_back(self, hwnd):
        self.calls.append(("back", hwnd))

    def focus(self, hwnd):
        self.calls.append(("focus", hwnd))
        self.fg = hwnd

    def exists(self, hwnd):
        return any(w.hwnd == hwnd for w in self.windows)

    def is_minimized(self, hwnd):
        return hwnd in self.minimized

    def restore_behind(self, hwnd):
        self.calls.append(("restore", hwnd))
        self.minimized.discard(hwnd)

    def close(self, hwnd):
        self.calls.append(("close", hwnd))
        self.windows = [w for w in self.windows if w.hwnd != hwnd]


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    cfg.data_dir.mkdir()
    cfg.browser.wait_seconds = 2
    cfg.obs.video_source = "브라우저"
    cfg.obs.audio_source = "브라우저 소리"
    obs = FakeObs(record_dir=tmp_path)
    win = FakeWindows()
    old = BrowserWindow(50, 1, "이전 녹화 창", "Chrome_WidgetWin_1", "chrome.exe")
    win.windows.append(old)

    def opener(cfg, url):
        # 브라우저가 열리면 새 창이 생기고 포커스를 가져감
        win.windows.append(BrowserWindow(200, 2, "3화 - 스트리밍", "Chrome_WidgetWin_1", "chrome.exe"))
        win.fg = 200

    rec = Recorder(cfg, obs, opener=opener, sleep=lambda s: None, windows=win, pid_finder=lambda: {1, 2})
    return rec, obs, win


def test_opens_binds_and_returns_focus(env):
    rec, obs, win = env
    rec.start(url="https://example.com/ep3")
    assert rec.browser.hwnd == 200  # 기존 창(50)이 아니라 새로 뜬 창
    expected = obs_window_string("3화 - 스트리밍", "Chrome_WidgetWin_1", "chrome.exe")
    assert obs.windows == {"브라우저": expected, "브라우저 소리": expected}
    assert ("back", 200) in win.calls and ("focus", 111) in win.calls
    assert win.fg == 111  # 작업하던 창으로 포커스 복귀


def test_minimized_window_is_restored_behind(env):
    rec, obs, win = env
    rec.start(url="https://x")
    win.minimized.add(200)
    rec.tick()
    assert ("restore", 200) in win.calls and obs.recording


def test_window_closed_on_stop(env):
    rec, obs, win = env
    rec.start(url="https://x")
    rec.stop()
    assert ("close", 200) in win.calls and rec.browser is None
    assert win.exists(50)  # 다른 창은 건드리지 않음


def test_no_url_does_not_touch_windows(env):
    rec, obs, win = env
    rec.start()
    assert win.calls == [] and obs.windows == {}


def test_window_string_escaping():
    assert obs_window_string("a:b#c", "Cls", "x.exe") == "a#3Ab#22c:Cls:x.exe"


def test_browser_cmd_isolated_profile(tmp_path):
    cmd = build_browser_cmd("chrome", tmp_path / "prof", "https://x", size=[1920, 1080])
    joined = " ".join(cmd)
    assert f"--user-data-dir={(tmp_path / 'prof').resolve()}" in cmd
    assert "CalculateNativeWinOcclusion" in joined and "HardwareMediaKeyHandling" in joined
    assert cmd[-1] == "--app=https://x" and "--window-size=1920,1080" in cmd


def test_check_obs_setup_warns_about_leaks():
    items = [{"sourceName": "모니터", "inputKind": "monitor_capture", "sceneItemEnabled": True}]
    inputs = [{"inputName": "데스크톱 오디오", "inputKind": "wasapi_output_capture"},
              {"inputName": "마이크", "inputKind": "wasapi_input_capture"}]
    report = check_obs_setup(items, inputs, {"마이크": True})
    warns = [r["msg"] for r in report if r["level"] == "warn"]
    assert any("화면 전체" in w for w in warns)
    assert any("데스크톱 오디오" in w and "마이크" not in w for w in warns)


def test_check_obs_setup_clean():
    items = [{"sourceName": "브라우저", "inputKind": "window_capture", "sceneItemEnabled": True},
             {"sourceName": "모니터", "inputKind": "monitor_capture", "sceneItemEnabled": False}]
    inputs = [{"inputName": "브라우저", "inputKind": "window_capture"},
              {"inputName": "브라우저 소리", "inputKind": "wasapi_process_output_capture"},
              {"inputName": "데스크톱 오디오", "inputKind": "wasapi_output_capture"}]
    report = check_obs_setup(items, inputs, {"데스크톱 오디오": True}, "브라우저", "브라우저 소리")
    assert all(r["level"] == "ok" for r in report), report


def test_status_includes_isolation(env):
    rec, obs, win = env
    st = rec.status()
    assert st["isolation"] and isinstance(st["isolation"][0]["msg"], str)
