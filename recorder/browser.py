"""녹화용 브라우저 조작 (Playwright).

폰에서 공유한 주소를 PC의 녹화 전용 브라우저로 열고,
- 영상 찾기 (iframe 안의 플레이어 포함)
- 시작 위치로 이동, 재생, 소리 켜기
- 영상을 창에 꽉 채우기 (녹화 화면을 깔끔하게)
- 재생 상태 읽기 (끝나면 녹화 자동 정지에 사용)
- 폰에서 재생/일시정지/이동

Playwright의 동기 API는 만든 스레드에서만 써야 하므로, 전용 작업 스레드 하나에서 모두 처리한다.
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from pathlib import Path

from .isolation import ANTI_THROTTLE_ARGS

log = logging.getLogger(__name__)


class BrowserError(RuntimeError):
    pass


# 페이지 안에서 가장 큰 <video>를 찾는다
_FIND_VIDEO = """() => {
  const vids = [...document.querySelectorAll('video')];
  let best = null, bestArea = 0;
  for (const v of vids) {
    const r = v.getBoundingClientRect();
    const area = Math.max(r.width * r.height, (v.videoWidth || 0) * (v.videoHeight || 0) / 100);
    if (area > bestArea) { best = v; bestArea = area; }
  }
  return best;
}"""

_STATE = """(v) => ({ time: v.currentTime || 0, duration: isFinite(v.duration) ? v.duration : null,
  paused: v.paused, ended: v.ended, muted: v.muted })"""

_FILL_CSS = ("position:fixed!important;inset:0!important;left:0!important;top:0!important;"
             "width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;"
             "margin:0!important;padding:0!important;border:0!important;transform:none!important;"
             "z-index:2147483647!important;background:#000!important;")

_APPLY_FILL = """(el, css) => {
  el.dataset.arPrevStyle = el.getAttribute('style') || '';
  el.setAttribute('style', el.dataset.arPrevStyle + ';' + css);
  if (el.tagName === 'VIDEO') { el.style.setProperty('object-fit', 'contain', 'important'); el.controls = false; }
  document.documentElement.style.setProperty('overflow', 'hidden', 'important');
  document.body && document.body.style.setProperty('overflow', 'hidden', 'important');
}"""


class BrowserController:
    def __init__(self, executable: str | None, profile_dir: Path, *, size: list[int] | None = None,
                 position: list[int] | None = None, extra_args: list[str] | None = None,
                 headless: bool = False):
        self.executable = executable
        self.profile_dir = Path(profile_dir)
        self.size = size
        self.position = position
        self.extra_args = extra_args or []
        self.headless = headless
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._ctx = None
        self._page = None

    # ---- 작업 스레드 ----------------------------------------------------
    def _run(self) -> None:
        while True:
            fn, fut = self._q.get()
            if fn is None:
                break
            try:
                fut.set_result(fn())
            except Exception as e:  # noqa: BLE001 - 호출한 쪽에 그대로 전달
                fut.set_exception(e)

    def call(self, fn, timeout: float = 60):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="browser", daemon=True)
            self._thread.start()
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut.result(timeout)

    # ---- 브라우저 ---------------------------------------------------------
    def _alive(self) -> bool:
        try:
            return self._ctx is not None and self._page is not None and not self._page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    def _launch(self, url: str):
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        args = [a for a in ANTI_THROTTLE_ARGS if not a.startswith("--no-first-run")] + ["--no-first-run"]
        if self.size:
            args.append(f"--window-size={self.size[0]},{self.size[1]}")
        if self.position:
            args.append(f"--window-position={self.position[0]},{self.position[1]}")
        args += self.extra_args
        # --app: 주소창/탭 없는 창 → 녹화 화면이 깔끔
        args.append(f"--app={url}")
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir.resolve()),
            executable_path=self.executable or None,
            headless=self.headless,
            args=args,
            # "자동화 소프트웨어에 의해 제어되고 있습니다" 안내줄이 녹화에 찍히지 않게
            ignore_default_args=["--enable-automation"],
            no_viewport=True,
        )
        self._ctx.set_default_timeout(15000)
        self._page = self._pick_page(url)

    def _pick_page(self, url: str):
        pages = self._ctx.pages
        page = next((p for p in pages if p.url not in ("about:blank", "")), None)
        if page is None:
            page = pages[0] if pages else self._ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
        for p in pages:
            if p is not page and p.url == "about:blank":
                try:
                    p.close()
                except Exception:  # noqa: BLE001
                    pass
        return page

    def open(self, url: str) -> None:
        def go():
            if self._alive():
                self._page.goto(url, wait_until="domcontentloaded")
            else:
                self._close_ctx()
                self._launch(url)
                try:
                    self._page.wait_for_load_state("domcontentloaded")
                except Exception:  # noqa: BLE001
                    pass
        try:
            self.call(go, timeout=60)
        except Exception as e:
            raise BrowserError(f"브라우저를 열 수 없습니다: {e}") from e

    def _close_ctx(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:  # noqa: BLE001
            pass
        self._ctx = self._page = None

    def close(self) -> None:
        try:
            self.call(self._close_ctx, timeout=20)
        except Exception:  # noqa: BLE001
            pass

    # ---- 영상 ------------------------------------------------------------
    def _find_video(self):
        """모든 프레임에서 가장 큰 영상을 찾는다. (frame, handle) 또는 None."""
        best = None
        best_area = 0.0
        for frame in self._page.frames:
            try:
                h = frame.evaluate_handle(_FIND_VIDEO)
                el = h.as_element()
                if el is None:
                    continue
                box = el.bounding_box() or {"width": 0, "height": 0}
                area = box["width"] * box["height"] + 1
                if area > best_area:
                    best, best_area = (frame, el), area
            except Exception:  # noqa: BLE001 - 프레임이 사라지는 등
                continue
        return best

    def wait_for_video(self, timeout: float = 20) -> bool:
        def wait():
            import time

            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if self._alive() and self._find_video():
                    return True
                self._page.wait_for_timeout(500)
            return False
        try:
            return bool(self.call(wait, timeout=timeout + 10))
        except Exception:  # noqa: BLE001
            return False

    def _with_video(self, js: str, arg=None):
        if not self._alive():
            raise BrowserError("녹화용 브라우저가 열려 있지 않습니다")
        found = self._find_video()
        if not found:
            raise BrowserError("페이지에서 영상을 찾지 못했습니다")
        _, el = found
        return el.evaluate(js, arg) if arg is not None else el.evaluate(js)

    def state(self) -> dict | None:
        def get():
            if not self._alive():
                return None
            found = self._find_video()
            if not found:
                return {"found": False, "title": self._page.title()}
            st = found[1].evaluate(_STATE)
            st.update(found=True, title=self._page.title())
            return st
        try:
            return self.call(get, timeout=10)
        except Exception:  # noqa: BLE001
            return None

    def play(self, start_at: float | None = None) -> None:
        # 메타데이터가 로드된 뒤에 이동해야 위치가 적용된다
        js = """async (v, t) => {
            if (v.readyState < 1) await new Promise(r => { v.addEventListener('loadedmetadata', r, {once: true}); setTimeout(r, 8000); });
            if (t !== null && t >= 0) v.currentTime = t;
            v.muted = false; if (v.volume === 0) v.volume = 1;
            return v.play().then(() => true, () => false); }"""
        self.call(lambda: self._with_video(js, start_at if start_at is not None else -1), timeout=20)

    def pause(self) -> None:
        self.call(lambda: self._with_video("v => v.pause()"), timeout=10)

    def toggle(self) -> None:
        self.call(lambda: self._with_video("v => v.paused ? v.play() : v.pause()"), timeout=10)

    def seek(self, delta: float | None = None, to: float | None = None) -> float:
        js = """(v, a) => { const t = a.to !== null ? a.to : v.currentTime + a.delta;
                v.currentTime = Math.max(0, Math.min(isFinite(v.duration) ? v.duration - 0.5 : t, t));
                return v.currentTime; }"""
        return self.call(lambda: self._with_video(js, {"delta": delta or 0, "to": to}), timeout=10)

    def fill(self) -> None:
        """영상(과 영상을 감싼 iframe들)을 창에 꽉 채운다."""
        def do():
            found = self._find_video()
            if not found:
                raise BrowserError("페이지에서 영상을 찾지 못했습니다")
            frame, el = found
            el.evaluate(_APPLY_FILL, _FILL_CSS)
            f = frame
            while f.parent_frame is not None:
                f.frame_element().evaluate(_APPLY_FILL, _FILL_CSS)
                f = f.parent_frame
        self.call(do, timeout=20)
