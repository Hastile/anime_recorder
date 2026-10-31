import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recorder.clips import probe_duration
from recorder.config import Config
from recorder.core import Recorder
from recorder.obs import FakeObs
from recorder.scheduler import Job
from recorder.server import create_app


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    cfg.data_dir.mkdir()
    cfg.server.token = "secret"
    cfg.browser.wait_seconds = 0
    obs = FakeObs(record_dir=tmp_path)
    obs.clock = Clock()
    opened = []
    rec = Recorder(cfg, obs, opener=lambda c, u: opened.append(u), sleep=lambda s: None)
    client = TestClient(create_app(rec, background=False))
    client.headers["Authorization"] = "Bearer secret"
    return rec, obs, client, opened, tmp_path


def make_video(path: Path, seconds: int = 30):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=duration={seconds}", "-g", "30", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )


def test_requires_token(env):
    _, _, client, _, _ = env
    assert client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200  # 앱 화면은 토큰 없이 열림


def test_record_mark_stop_and_clip(env):
    rec, obs, client, opened, tmp = env
    video = tmp / "ep1.mkv"
    make_video(video, 30)
    obs.output_path = str(video)
    rec.cfg.clips.auto_export = False

    r = client.post("/api/record/start", json={"url": "https://example.com/ep1", "title": "1화"})
    assert r.status_code == 200, r.text
    assert opened == ["https://example.com/ep1"]
    assert client.post("/api/record/start", json={}).status_code == 409

    obs.clock.t += 15
    assert client.post("/api/mark", json={"kind": "point", "label": "명장면"}).json()["t"] == 15
    obs.clock.t += 3
    client.post("/api/mark", json={"kind": "in"})
    obs.clock.t += 6
    client.post("/api/mark", json={"kind": "out", "label": "대사"})
    obs.clock.t += 6

    s = client.post("/api/record/stop").json()
    assert s["output_path"] == str(video) and s["duration"] == 30
    rec._export(s["id"])
    s = rec.sessions.get(s["id"])
    assert s.export_state == "done", s.export_message
    clips = sorted(Path(m.clip_path) for m in s.markers)
    assert len(clips) == 2 and all(c.exists() for c in clips)
    assert "명장면" in s.markers[0].clip_path
    lengths = [probe_duration(rec.cfg.clips, m.clip_path) for m in s.markers]
    assert abs(lengths[0] - 15) < 1.5 and abs(lengths[1] - 6) < 1.5, lengths


def test_range_open_closed_on_stop(env):
    rec, obs, client, _, _ = env
    client.post("/api/record/start", json={})
    obs.clock.t += 5
    client.post("/api/mark", json={"kind": "in"})
    assert client.post("/api/mark", json={"kind": "in"}).status_code == 409
    obs.clock.t += 5
    s = client.post("/api/record/stop").json()
    assert s["markers"][0]["end"] == 10


def test_mark_when_not_recording(env):
    _, _, client, _, _ = env
    assert client.post("/api/mark", json={"kind": "point"}).status_code == 409


def test_obs_started_manually_creates_session(env):
    rec, obs, client, _, _ = env
    obs.start()
    obs.clock.t += 4
    assert client.post("/api/mark", json={"kind": "point"}).status_code == 200
    assert rec.active_id


def test_auto_stop(env):
    rec, obs, client, _, _ = env
    client.post("/api/record/start", json={"auto_stop_min": 1})
    rec.auto_stop_at -= 61
    rec.tick()
    assert not obs.recording and rec.active_id is None


def test_schedule_fires_once(env):
    rec, obs, client, opened, _ = env
    r = client.post("/api/schedules", json={"name": "토요일", "time": "23:30", "weekdays": [5],
                                            "duration_min": 25, "url": "https://x"})
    assert r.status_code == 200, r.text
    sat = datetime(2026, 10, 31, 23, 30, 30)  # 토요일
    rec.tick(datetime(2026, 10, 31, 23, 29))
    assert not obs.recording
    rec.tick(sat)
    assert obs.recording and opened == ["https://x"] and rec.auto_stop_at
    obs.stop(); rec.active_id = None
    rec.tick(sat)  # 같은 회차는 다시 실행되지 않음
    assert not obs.recording


def test_schedule_validation_and_next_run(env):
    _, _, client, _, _ = env
    assert client.post("/api/schedules", json={"name": "x", "time": "25:00"}).status_code == 400
    j = Job(id="a", name="x", time="09:00", weekdays=[0])
    assert j.next_run(datetime(2026, 10, 31, 12)) == datetime(2026, 11, 2, 9, 0)
    one = Job(id="b", name="y", time="09:00", date="2026-11-05")
    assert one.next_run(datetime(2026, 10, 31)) == datetime(2026, 11, 5, 9)


def test_marker_window():
    from recorder.sessions import Marker
    assert Marker(id="a", kind="point", t=3).window(10, 5, 60) == (0, 8)
    assert Marker(id="a", kind="point", t=58).window(10, 5, 60) == (48, 60)
    assert Marker(id="a", kind="range", start=5, end=9).window(10, 5, 60) == (5, 9)
