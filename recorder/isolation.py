"""녹화 격리: PC로 다른 작업을 해도 녹화본에 섞이지 않게 한다.

1. 녹화 전용 브라우저: 별도 프로필로 띄운 독립 브라우저 창. 평소 쓰는 브라우저와
   프로세스/탭/소리가 완전히 분리된다. 창이 가려져도 영상이 멈추지 않도록
   크롬의 가림(occlusion) 절전 기능과 미디어 키 처리를 끈다.
2. OBS 소스 자동 연결: 띄운 창을 OBS "윈도우 캡처"와 "응용 프로그램 오디오 캡처"
   소스에 정확히 지정한다.
3. 포커스 보호: 창을 띄운 뒤 맨 뒤로 보내고 하던 창으로 포커스를 돌려준다.
   녹화 중 창이 최소화되면(캡처가 멈춤) 맨 뒤에서 다시 복원한다.
4. 점검: 화면 전체 캡처나 데스크톱 오디오처럼 다른 작업이 섞이는 설정을 찾아 경고한다.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# 창이 가려지거나 백그라운드여도 재생/렌더링을 멈추지 않게 하는 크롬 옵션
ANTI_THROTTLE_ARGS = [
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    # CalculateNativeWinOcclusion: 가려진 창을 그리지 않음 → 캡처가 멈춤
    # HardwareMediaKeyHandling: 키보드 재생/일시정지 키가 녹화 창을 건드리지 않게
    # GlobalMediaControls: 크롬 미디어 컨트롤로 녹화 창이 조작되지 않게
    "--disable-features=CalculateNativeWinOcclusion,HardwareMediaKeyHandling,GlobalMediaControls",
    "--autoplay-policy=no-user-gesture-required",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
]

WINDOWS_BROWSERS = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
]
OTHER_BROWSERS = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]


def find_browser(preferred: str = "") -> str | None:
    if preferred:
        return preferred if Path(preferred).exists() or shutil.which(preferred) else None
    if IS_WINDOWS:
        for p in WINDOWS_BROWSERS:
            path = os.path.expandvars(p)
            if Path(path).exists():
                return path
        return None
    for p in OTHER_BROWSERS:
        if Path(p).exists() or shutil.which(p):
            return shutil.which(p) or p
    return None


def build_browser_cmd(exe: str, profile_dir: Path, url: str, *, app_mode: bool = True,
                      size: list[int] | None = None, position: list[int] | None = None,
                      extra: list[str] | None = None) -> list[str]:
    cmd = [exe, f"--user-data-dir={Path(profile_dir).resolve()}", *ANTI_THROTTLE_ARGS]
    if size:
        cmd.append(f"--window-size={size[0]},{size[1]}")
    if position:
        cmd.append(f"--window-position={position[0]},{position[1]}")
    cmd += extra or []
    cmd += [f"--app={url}"] if app_mode else ["--new-window", url]
    return cmd


def popen_kwargs() -> dict:
    """Windows: 새 창이 포커스를 가져가지 않도록 요청 (브라우저가 따르면 효과)."""
    if not IS_WINDOWS:
        return {}
    import subprocess

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4  # SW_SHOWNOACTIVATE
    return {"startupinfo": si}


def obs_window_string(title: str, cls: str, exe: str) -> str:
    """OBS 윈도우/오디오 캡처의 'window' 설정 값 (제목:클래스:실행파일)."""
    enc = lambda s: s.replace("#", "#22").replace(":", "#3A")  # noqa: E731
    return f"{enc(title)}:{enc(cls)}:{enc(exe)}"


@dataclass
class BrowserWindow:
    hwnd: int
    pid: int
    title: str
    cls: str
    exe: str

    @property
    def obs_value(self) -> str:
        return obs_window_string(self.title, self.cls, self.exe)


def profile_pids(profile_dir: Path) -> set[int]:
    """녹화 전용 프로필로 실행된 브라우저 프로세스들."""
    try:
        import psutil
    except ImportError:
        return set()
    key = str(Path(profile_dir).resolve()).lower()
    pids = set()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or []).lower()
        except (psutil.Error, TypeError):
            continue
        if f"--user-data-dir={key}" in cmd:
            pids.add(p.info["pid"])
    return pids


# ---------------------------------------------------------------------------
# Windows 창 제어 (ctypes). 다른 OS에서는 아무 것도 하지 않는다.
# ---------------------------------------------------------------------------
class WindowControl:
    def __init__(self):
        self.ok = IS_WINDOWS
        if not self.ok:
            return
        import ctypes
        from ctypes import wintypes

        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        H, B, U, I = wintypes.HWND, wintypes.BOOL, wintypes.UINT, ctypes.c_int
        sigs = {
            "GetForegroundWindow": (H, []),
            "SetForegroundWindow": (B, [H]),
            "IsWindow": (B, [H]),
            "IsIconic": (B, [H]),
            "IsWindowVisible": (B, [H]),
            "GetWindow": (H, [H, U]),
            "ShowWindow": (B, [H, I]),
            "SetWindowPos": (B, [H, H, I, I, I, I, U]),
            "PostMessageW": (B, [H, U, wintypes.WPARAM, wintypes.LPARAM]),
            "GetWindowThreadProcessId": (wintypes.DWORD, [H, ctypes.POINTER(wintypes.DWORD)]),
            "AttachThreadInput": (B, [wintypes.DWORD, wintypes.DWORD, B]),
            "GetWindowTextW": (I, [H, wintypes.LPWSTR, I]),
            "GetClassNameW": (I, [H, wintypes.LPWSTR, I]),
            "GetWindowRect": (B, [H, ctypes.POINTER(wintypes.RECT)]),
        }
        for name, (res, args) in sigs.items():
            fn = getattr(self.user32, name)
            fn.restype, fn.argtypes = res, args
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, B, wintypes.DWORD]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

    def foreground(self) -> int | None:
        return self.user32.GetForegroundWindow() if self.ok else None

    def find_windows(self, pids: set[int]) -> list[BrowserWindow]:
        """주어진 프로세스들의 보이는 최상위 창 목록 (큰 창 순)."""
        if not self.ok or not pids:
            return []
        ct, wt, u = self.ct, self.wt, self.user32
        found = []

        @ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
            if pid.value in pids and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, 4):  # GW_OWNER
                title = ct.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, title, 512)
                cls = ct.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, cls, 256)
                rect = wt.RECT()
                u.GetWindowRect(hwnd, ct.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if title.value and area > 0:
                    found.append((area, BrowserWindow(hwnd, pid.value, title.value, cls.value,
                                                      self._exe_name(pid.value))))
            return True

        u.EnumWindows(cb, 0)
        return [w for _, w in sorted(found, key=lambda x: -x[0])]

    def _exe_name(self, pid: int) -> str:
        ct, wt, k = self.ct, self.wt, self.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ct.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if k.QueryFullProcessImageNameW(h, 0, buf, ct.byref(size)):
                return Path(buf.value).name
            return ""
        finally:
            k.CloseHandle(h)

    def exists(self, hwnd: int) -> bool:
        return bool(self.ok and hwnd and self.user32.IsWindow(hwnd))

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self.ok and self.user32.IsIconic(hwnd))

    def send_to_back(self, hwnd: int) -> None:
        if self.ok:
            # HWND_BOTTOM=1, SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
            self.user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def restore_behind(self, hwnd: int) -> None:
        """최소화된 창을 포커스를 뺏지 않고 맨 뒤에 복원."""
        if self.ok:
            self.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            self.send_to_back(hwnd)

    def focus(self, hwnd: int | None) -> None:
        """작업하던 창으로 포커스를 돌려준다.

        백그라운드 프로세스의 SetForegroundWindow는 보통 막히므로, 현재 포커스를 가진
        스레드에 입력 큐를 잠깐 붙였다 떼는 방식으로 처리한다 (키 입력을 흉내 내지 않음).
        """
        u = self.user32
        if not (self.ok and hwnd and u.IsWindow(hwnd)) or u.GetForegroundWindow() == hwnd:
            return
        fg_tid = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        my_tid = self.kernel32.GetCurrentThreadId()
        attached = fg_tid and fg_tid != my_tid and u.AttachThreadInput(my_tid, fg_tid, True)
        try:
            u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u.AttachThreadInput(my_tid, fg_tid, False)

    def close(self, hwnd: int) -> None:
        if self.exists(hwnd):
            self.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


# ---------------------------------------------------------------------------
# OBS 설정 점검
# ---------------------------------------------------------------------------
SCREEN_KINDS = {"monitor_capture", "screen_capture", "display_capture", "xshm_input",
                "pipewire-desktop-capture-source", "macos-screen-capture"}
WINDOW_KINDS = {"window_capture", "game_capture", "xcomposite_input", "pipewire-window-capture-source"}
GLOBAL_AUDIO_KINDS = {"wasapi_output_capture", "wasapi_input_capture", "pulse_output_capture",
                      "pulse_input_capture", "coreaudio_output_capture", "coreaudio_input_capture"}
APP_AUDIO_KINDS = {"wasapi_process_output_capture"}


def check_obs_setup(items: list[dict], inputs: list[dict], muted: dict[str, bool],
                    video_source: str = "", audio_source: str = "") -> list[dict]:
    """현재 장면 구성(items)과 전체 입력(inputs)으로 격리 상태를 점검한다.

    items: [{"sourceName", "inputKind", "sceneItemEnabled"}]
    inputs: [{"inputName", "inputKind"}]
    muted: {입력이름: 음소거 여부}
    """
    report = []
    visible = [i for i in items if i.get("sceneItemEnabled", True)]
    screens = [i["sourceName"] for i in visible if i.get("inputKind") in SCREEN_KINDS]
    windows = [i["sourceName"] for i in visible if i.get("inputKind") in WINDOW_KINDS]
    if screens:
        report.append(_warn(f"화면 전체 캡처({', '.join(screens)})가 켜져 있어 다른 작업 화면이 녹화됩니다. "
                            "윈도우 캡처로 바꾸세요."))
    elif windows:
        report.append(_ok(f"창 단위 캡처 사용 중 ({', '.join(windows)})"))
    else:
        report.append(_warn("현재 장면에 윈도우 캡처 소스가 없습니다."))

    loud = [i["inputName"] for i in inputs
            if i.get("inputKind") in GLOBAL_AUDIO_KINDS and not muted.get(i["inputName"], False)]
    if loud:
        report.append(_warn(f"전체 오디오({', '.join(loud)})가 켜져 있어 다른 소리도 녹음됩니다. "
                            "OBS 믹서에서 음소거하세요."))
    apps = [i["inputName"] for i in inputs if i.get("inputKind") in APP_AUDIO_KINDS]
    if apps:
        report.append(_ok(f"응용 프로그램 오디오 캡처 사용 중 ({', '.join(apps)})"))
    elif not loud:
        report.append(_warn("오디오 소스가 없습니다. 응용 프로그램 오디오 캡처를 추가하세요."))

    names = {i["inputName"]: i.get("inputKind") for i in inputs}
    for label, name, kinds in (("영상", video_source, WINDOW_KINDS), ("오디오", audio_source, APP_AUDIO_KINDS)):
        if name and name not in names:
            report.append(_warn(f"설정한 {label} 소스 '{name}'를 OBS에서 찾을 수 없습니다."))
        elif name and names[name] not in kinds:
            report.append(_warn(f"'{name}'은(는) {label} 자동 연결에 맞지 않는 소스 종류입니다 ({names[name]})."))
    return report


def _ok(msg: str) -> dict:
    return {"level": "ok", "msg": msg}


def _warn(msg: str) -> dict:
    return {"level": "warn", "msg": msg}
