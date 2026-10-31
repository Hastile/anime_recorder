"""OBS·브라우저 없이 화면을 확인하는 데모/테스트용 가짜 플레이어."""

class FakePlayer:
    """Playwright 브라우저 흉내."""
    controllable = True

    def __init__(self, on_open=None, has_video=True, title="진격의 거인 3화 - 스트리밍"):
        self.on_open = on_open
        self.has_video = has_video
        self.title = title
        self.calls = []
        self.time = 0.0
        self.duration = 1440.0
        self.paused = True
        self.ended = False
        self.opened = None

    def open(self, url):
        self.opened = url
        self.calls.append(("open", url))
        if self.on_open:
            self.on_open(url)

    def wait_for_video(self, timeout=20):
        return self.has_video

    def play(self, start_at=None):
        self.calls.append(("play", start_at))
        if start_at is not None and start_at >= 0:
            self.time = start_at
        self.paused = False

    def pause(self):
        self.calls.append(("pause",))
        self.paused = True

    def seek(self, delta=None, to=None):
        self.time = to if to is not None else self.time + (delta or 0)
        self.calls.append(("seek", self.time))
        return self.time

    def fill(self):
        self.calls.append(("fill",))

    def state(self):
        if not self.opened:
            return None
        if not self.has_video:
            return {"found": False, "title": self.title}
        return {"found": True, "title": self.title, "time": self.time, "duration": self.duration,
                "paused": self.paused, "ended": self.ended}

    def close(self):
        self.calls.append(("close",))
        self.opened = None
