"""HTTP API와 폰용 웹앱(PWA)을 제공한다."""

from __future__ import annotations

import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import Recorder, RecorderError, parse_time
from .obs import ObsError
from .pairing import HostInfo, Pairing, serve_discovery

STATIC = Path(__file__).parent / "static"


class StartReq(BaseModel):
    url: str = ""
    title: str = ""
    start_at: str | float | None = None  # "12:34" 또는 초
    stop_on_end: bool = True
    live: bool = False  # 폰에서 지금 재생 중인 위치 (PC 준비 시간만큼 보정)


class SeekReq(BaseModel):
    delta: float | None = None
    to: str | float | None = None


class RenameReq(BaseModel):
    title: str


class PairStartReq(BaseModel):
    device: str = ""


class PairConfirmReq(BaseModel):
    pair_id: str
    pin: str


def show_pin(device: str, pin: str) -> None:
    """PC 화면에 페어링 PIN을 크게 보여준다."""
    print("\n" + "=" * 40 + f"\n  페어링 요청: {device}\n  PIN:  {' '.join(pin)}\n" + "=" * 40 + "\n", flush=True)
    import sys

    if sys.platform == "win32":
        import ctypes

        text = f"{device}에서 연결을 요청했습니다.\n\n휴대폰에 이 PIN을 입력하세요:\n\n        {pin}"
        # MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        threading.Thread(target=ctypes.windll.user32.MessageBoxW,
                         args=(None, text, "Anime Recorder 페어링", 0x40 | 0x40000 | 0x10000),
                         daemon=True).start()


def create_app(rec: Recorder, *, background: bool = True, pairing: Pairing | None = None) -> FastAPI:
    stop_event = threading.Event()
    cfg = rec.cfg
    info = HostInfo(cfg.data_dir, cfg.server.name, cfg.server.port)
    if pairing is None:
        pairing = Pairing(cfg.data_dir)
        pairing.on_pin = show_pin
    last_pair_start = [0.0]

    @asynccontextmanager
    async def lifespan(app):
        if background:
            threading.Thread(target=rec.run_loop, args=(stop_event,), daemon=True).start()
            if cfg.server.discovery:
                threading.Thread(target=serve_discovery, args=(info, stop_event), daemon=True).start()
        yield
        stop_event.set()

    app = FastAPI(title="Anime Recorder", lifespan=lifespan, docs_url=None, redoc_url=None)
    token = rec.cfg.server.token

    def auth(request: Request):
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else ""
        if not (secrets.compare_digest(given.encode(), token.encode()) or pairing.verify(given)):
            raise HTTPException(401, "토큰이 올바르지 않습니다")

    @app.exception_handler(RecorderError)
    async def _rec_err(_, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _val_err(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def _perm_err(_, e):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(KeyError)
    async def _key_err(_, e):
        return JSONResponse({"detail": "찾을 수 없습니다"}, status_code=404)

    dep = [Depends(auth)]

    # ---- 앱 연결 (토큰 없이 호출) ----------------------------------------
    @app.get("/api/info")
    def host_info():
        return info.as_dict()

    @app.post("/api/pair/start")
    def pair_start(req: PairStartReq):
        now = time.monotonic()
        if now - last_pair_start[0] < 3:
            raise HTTPException(429, "잠시 후 다시 시도하세요")
        last_pair_start[0] = now
        rec.event(f"페어링 요청: {req.device or '휴대폰'}")
        return {"pair_id": pairing.start(req.device)}

    @app.post("/api/pair/confirm")
    def pair_confirm(req: PairConfirmReq):
        tok = pairing.confirm(req.pair_id, req.pin)
        rec.event("새 기기가 연결되었습니다")
        return {"token": tok, "host": info.as_dict()}

    @app.get("/api/devices", dependencies=dep)
    def devices():
        return pairing.list()

    @app.delete("/api/devices/{device_id}", dependencies=dep)
    def remove_device(device_id: str):
        pairing.remove(device_id)
        return {"ok": True}

    @app.get("/api/status", dependencies=dep)
    def status():
        return rec.status()

    @app.post("/api/record/start", dependencies=dep)
    def start(req: StartReq):
        s = rec.start(req.url.strip(), req.title.strip(), parse_time(req.start_at), req.stop_on_end, req.live)
        return asdict(s)

    @app.post("/api/record/stop", dependencies=dep)
    def stop():
        s = rec.stop()
        return asdict(s) if s else {}

    @app.post("/api/record/pause", dependencies=dep)
    def pause():
        rec.pause()
        return {"ok": True}

    @app.post("/api/record/resume", dependencies=dep)
    def resume():
        rec.resume()
        return {"ok": True}

    @app.post("/api/player/seek", dependencies=dep)
    def seek(req: SeekReq):
        return {"time": rec.seek(delta=req.delta, to=parse_time(req.to))}

    @app.get("/api/preview", dependencies=dep)
    def preview(width: int = 640):
        try:
            img = rec.obs.screenshot(max(160, min(width, 1280)))
        except ObsError as e:
            raise HTTPException(503, str(e))
        if not img:
            return Response(status_code=204)
        return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/recordings", dependencies=dep)
    def recordings():
        return [asdict(s) for s in rec.sessions.list()]

    @app.patch("/api/recordings/{sid}", dependencies=dep)
    def rename(sid: str, req: RenameReq):
        return asdict(rec.rename(sid, req.title))

    @app.delete("/api/recordings/{sid}", dependencies=dep)
    def delete_recording(sid: str):
        if sid == rec.active_id:
            raise RecorderError("녹화 중인 항목은 지울 수 없습니다")
        rec.sessions.delete(sid)  # 녹화 파일 자체는 지우지 않는다
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
