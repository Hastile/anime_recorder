from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recorder.config import Config
from recorder.core import Recorder, parse_time, safe_filename
from recorder.obs import FakeObs
from recorder.server import create_app

from recorder.demo import FakePlayer


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
    obs = FakeObs(record_dir=tmp_path)
    obs.clock = Clock()
    player = FakePlayer()
    rec = Recorder(cfg, obs, player=player, sleep=lambda s: None)
    client = TestClient(create_app(rec, background=False))
    client.headers["Authorization"] = "Bearer secret"
    return rec, obs, player, client, tmp_path


def wait_rename(rec, sid):
    import time
    for _ in range(50):
        p = rec.sessions.get(sid).output_path
        if p and not Path(p).name.startswith("raw"):
            return p
        time.sleep(0.05)
    return rec.sessions.get(sid).output_path


def test_requires_token(env):
    *_, client, _ = env
    assert client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200


def test_shared_page_recorded_from_position(env):
    rec, obs, player, client, tmp = env
    raw = tmp / "raw 2026-10-31.mkv"
    raw.write_bytes(b"x")
    obs.output_path = str(raw)

    r = client.post("/api/record/start", json={"url": "https://ex.com/ep3", "start_at": "12:34"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["title"] == "진격의 거인 3화 - 스트리밍"  # 비우면 페이지 제목
    assert s["start_at"] == 754
    # 시작 위치로 맞춰 멈춰 두고, 화면을 채우고, 녹화가 시작된 다음 재생
    names = [c[0] for c in player.calls]
    assert names.index("pause") < names.index("fill") < len(names) - 1
    assert player.calls[-1] == ("play", None) and player.time == 754 and obs.recording

    st = client.get("/api/status").json()
    assert st["player"]["time"] == 754 and st["stop_on_end"] is True

    obs.clock.t += 30
    out = client.post("/api/record/stop").json()
    assert out["duration"] == 30 and ("close",) in player.calls
    final = wait_rename(rec, s["id"])
    assert Path(final).name == "진격의 거인 3화 - 스트리밍.mkv" and Path(final).exists()


def test_stops_when_video_ends(env):
    rec, obs, player, client, tmp = env
    client.post("/api/record/start", json={"url": "https://ex.com/ep", "title": "3화"})
    obs.clock.t += 10
    rec.tick()
    assert obs.recording
    player.ended = True
    rec.tick()
    assert not obs.recording and rec.active_id is None
    assert "끝나" in rec.events[0]["msg"] or any("끝나" in e["msg"] for e in rec.events)


def test_no_auto_stop_when_disabled_or_no_video(env):
    rec, obs, player, client, _ = env
    client.post("/api/record/start", json={"url": "https://ex.com", "stop_on_end": False})
    player.ended = True
    rec.tick()
    assert obs.recording
    client.post("/api/record/stop")

    player.has_video = False
    player.ended = False
    r = client.post("/api/record/start", json={"url": "https://ex.com"}).json()
    assert obs.recording and client.get("/api/status").json()["stop_on_end"] is False


def test_pause_resume_and_seek(env):
    rec, obs, player, client, _ = env
    client.post("/api/record/start", json={"url": "https://ex.com"})
    assert client.post("/api/record/pause").status_code == 200
    assert obs.paused and player.paused
    client.post("/api/record/resume")
    assert not obs.paused and not player.paused
    t = client.post("/api/player/seek", json={"delta": 10}).json()["time"]
    assert t == 10
    assert client.post("/api/player/seek", json={"to": "1:00"}).json()["time"] == 60


def test_record_without_url(env):
    rec, obs, player, client, _ = env
    s = client.post("/api/record/start", json={}).json()
    assert obs.recording and player.calls == [] and s["title"]
    assert client.post("/api/record/start", json={}).status_code == 409


def test_rename_after_recording(env):
    rec, obs, player, client, tmp = env
    rec.cfg.recording.rename_to_title = False
    raw = tmp / "raw.mkv"
    raw.write_bytes(b"x")
    (tmp / "새 이름.mkv").write_bytes(b"other")  # 이름이 겹치면 (2)
    obs.output_path = str(raw)
    s = client.post("/api/record/start", json={}).json()
    client.post("/api/record/stop")
    r = client.patch(f"/api/recordings/{s['id']}", json={"title": "새 이름"}).json()
    assert Path(r["output_path"]).name == "새 이름 (2).mkv" and not raw.exists()
    r = client.patch(f"/api/recordings/{s['id']}", json={"title": 'a/b:c?'}).json()
    assert Path(r["output_path"]).name == "a_b_c_.mkv"
    lst = client.get("/api/recordings").json()
    assert lst[0]["title"] == "a/b:c?"
    assert client.patch(f"/api/recordings/{s['id']}", json={"title": " "}).status_code == 400


def test_rename_while_recording_applies_at_stop(env):
    rec, obs, player, client, tmp = env
    raw = tmp / "raw.mkv"
    raw.write_bytes(b"x")
    obs.output_path = str(raw)
    s = client.post("/api/record/start", json={}).json()
    client.patch(f"/api/recordings/{s['id']}", json={"title": "도중에 바꾼 이름"})
    client.post("/api/record/stop")
    assert Path(wait_rename(rec, s["id"])).name == "도중에 바꾼 이름.mkv"


def test_helpers():
    assert parse_time("12:34") == 754 and parse_time("1:02:03") == 3723 and parse_time("") is None
    with pytest.raises(ValueError):
        parse_time("abc")
    assert safe_filename('  제목: "1화" ') == "제목_ _1화_"


def test_pairing_flow(env):
    rec, *_ = env
    from recorder.pairing import Pairing
    pins = []
    pairing = Pairing(rec.cfg.data_dir)
    pairing.on_pin = lambda d, p: pins.append(p)
    c = TestClient(create_app(rec, background=False, pairing=pairing))
    assert c.get("/api/info").json()["app"] == "anime-recorder"
    pid = c.post("/api/pair/start", json={"device": "Galaxy"}).json()["pair_id"]
    bad = "0000" if pins[0] != "0000" else "1111"
    r = c.post("/api/pair/confirm", json={"pair_id": pid, "pin": bad})
    assert r.status_code == 403 and "남음" in r.json()["detail"]
    tok = c.post("/api/pair/confirm", json={"pair_id": pid, "pin": pins[0]}).json()["token"]
    assert c.get("/api/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert c.post("/api/pair/confirm", json={"pair_id": pid, "pin": pins[0]}).status_code == 403


def test_pairing_lockout(tmp_path):
    from recorder.pairing import Pairing
    p = Pairing(tmp_path)
    pins = []
    p.on_pin = lambda d, pin: pins.append(pin)
    pid = p.start("x")
    wrong = "0000" if pins[0] != "0000" else "1111"
    for _ in range(5):
        with pytest.raises(PermissionError):
            p.confirm(pid, wrong)
    with pytest.raises(PermissionError, match="만료"):
        p.confirm(pid, pins[0])


def test_discovery_responds(tmp_path):
    import json, socket, threading, time
    from recorder.pairing import DISCOVERY_MAGIC, HostInfo, serve_discovery
    info = HostInfo(tmp_path, "집PC", 8765)
    stop = threading.Event()
    threading.Thread(target=serve_discovery, args=(info, stop, 18766), daemon=True).start()
    time.sleep(0.3)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(DISCOVERY_MAGIC, ("127.0.0.1", 18766))
    d = json.loads(s.recvfrom(4096)[0])
    stop.set()
    assert d["name"] == "집PC" and d["id"] == info.id
