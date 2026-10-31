"""HTTP API와 폰용 웹앱(PWA)을 제공한다."""

from __future__ import annotations

import secrets
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import Recorder, RecorderError
from .obs import ObsError

STATIC = Path(__file__).parent / "static"


class StartReq(BaseModel):
    url: str = ""
    title: str = ""
    auto_stop_min: float | None = Field(default=None, gt=0, le=24 * 60)


class MarkReq(BaseModel):
    kind: str = "point"
    label: str = ""


class LabelReq(BaseModel):
    label: str


class JobReq(BaseModel):
    name: str
    time: str
    duration_min: int = 30
    url: str = ""
    weekdays: list[int] = []
    date: str | None = None
    enabled: bool = True


class JobPatch(BaseModel):
    name: str | None = None
    time: str | None = None
    duration_min: int | None = None
    url: str | None = None
    weekdays: list[int] | None = None
    date: str | None = None
    enabled: bool | None = None


def create_app(rec: Recorder, *, background: bool = True) -> FastAPI:
    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        if background:
            threading.Thread(target=rec.run_loop, args=(stop_event,), daemon=True).start()
        yield
        stop_event.set()

    app = FastAPI(title="Anime Recorder", lifespan=lifespan, docs_url=None, redoc_url=None)
    token = rec.cfg.server.token

    def auth(request: Request):
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else ""
        if not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "토큰이 올바르지 않습니다")

    @app.exception_handler(RecorderError)
    async def _rec_err(_, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _val_err(_, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _key_err(_, e):
        return JSONResponse({"detail": "찾을 수 없습니다"}, status_code=404)

    dep = [Depends(auth)]

    @app.get("/api/status", dependencies=dep)
    def status():
        return rec.status()

    @app.post("/api/record/start", dependencies=dep)
    def start(req: StartReq):
        return asdict(rec.start(req.url.strip(), req.title.strip(), req.auto_stop_min))

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

    @app.post("/api/mark", dependencies=dep)
    def mark(req: MarkReq):
        return rec.mark(req.kind, req.label.strip())

    @app.get("/api/preview", dependencies=dep)
    def preview(width: int = 640):
        try:
            img = rec.obs.screenshot(max(160, min(width, 1280)))
        except ObsError as e:
            raise HTTPException(503, str(e))
        if not img:
            return Response(status_code=204)
        return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions", dependencies=dep)
    def sessions():
        return [asdict(s) for s in rec.sessions.list()]

    @app.get("/api/sessions/{sid}", dependencies=dep)
    def session(sid: str):
        return asdict(rec.sessions.get(sid))

    @app.delete("/api/sessions/{sid}", dependencies=dep)
    def delete_session(sid: str):
        if sid == rec.active_id:
            raise RecorderError("녹화 중인 세션은 지울 수 없습니다")
        rec.sessions.delete(sid)  # 녹화 파일 자체는 지우지 않는다
        return {"ok": True}

    @app.post("/api/sessions/{sid}/export", dependencies=dep)
    def export(sid: str):
        rec.export_async(sid)
        return {"ok": True}

    @app.patch("/api/sessions/{sid}/markers/{mid}", dependencies=dep)
    def edit_marker(sid: str, mid: str, req: LabelReq):
        rec.edit_marker(sid, mid, req.label.strip())
        return {"ok": True}

    @app.delete("/api/sessions/{sid}/markers/{mid}", dependencies=dep)
    def delete_marker(sid: str, mid: str):
        rec.delete_marker(sid, mid)
        return {"ok": True}

    @app.get("/api/recent-urls", dependencies=dep)
    def recent_urls():
        seen: dict[str, str] = {}
        for s in rec.sessions.list(100):
            if s.url and s.url not in seen:
                seen[s.url] = s.title
        return [{"url": u, "title": t} for u, t in list(seen.items())[:10]]

    def _job_dict(j):
        d = asdict(j)
        nxt = j.next_run(datetime.now())
        d["next_run"] = nxt.isoformat(timespec="minutes") if nxt else None
        return d

    @app.get("/api/schedules", dependencies=dep)
    def schedules():
        return [_job_dict(j) for j in rec.schedules.jobs]

    @app.post("/api/schedules", dependencies=dep)
    def add_schedule(req: JobReq):
        return _job_dict(rec.schedules.add(**req.model_dump()))

    @app.patch("/api/schedules/{jid}", dependencies=dep)
    def edit_schedule(jid: str, req: JobPatch):
        return _job_dict(rec.schedules.update(jid, **req.model_dump(exclude_unset=True)))

    @app.delete("/api/schedules/{jid}", dependencies=dep)
    def delete_schedule(jid: str):
        rec.schedules.remove(jid)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
