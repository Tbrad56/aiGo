"""Local web API and static UI, served on 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aigo.controller import Controller
from aigo.device import DeviceError

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    points: list[Point] = Field(min_length=1, max_length=5000)
    speed_kmh: float = Field(gt=0, le=1000)
    loop: bool = False


class StepRequest(BaseModel):
    bearing: float = Field(ge=0, lt=360)
    meters: float = Field(gt=0, le=1000)


class FavoriteRequest(Point):
    name: str = Field(default="", max_length=100)


def create_app(controller: Controller, *, connect_on_start: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if connect_on_start:
            # Connect in the background so the UI loads right away and shows progress.
            async def _connect():
                with contextlib.suppress(DeviceError):
                    await controller.connect()

            task = asyncio.create_task(_connect())
        yield
        if task:
            task.cancel()
        await controller.shutdown()

    app = FastAPI(title="aiGo", lifespan=lifespan)

    async def run(coro):
        try:
            return await coro
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except DeviceError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/status")
    async def status():
        return controller.status()

    @app.post("/api/connect")
    async def connect():
        await run(controller.connect())
        return controller.status()

    @app.post("/api/teleport")
    async def teleport(p: Point):
        result = await run(controller.teleport(p.lat, p.lon))
        return {**result, "status": controller.status()}

    @app.post("/api/clear")
    async def clear():
        await run(controller.clear())
        return controller.status()

    @app.post("/api/step")
    async def step(s: StepRequest):
        await run(controller.step(s.bearing, s.meters))
        return controller.status()

    @app.post("/api/route")
    async def route(r: RouteRequest):
        result = await run(controller.start_route([(p.lat, p.lon) for p in r.points], r.speed_kmh, r.loop))
        return {**result, "status": controller.status()}

    @app.post("/api/route/stop")
    async def route_stop():
        await controller.stop_route()
        return controller.status()

    def _store():
        if controller.store is None:
            raise HTTPException(404, "No store configured")
        return controller.store

    @app.get("/api/favorites")
    async def favorites():
        return _store().favorites()

    @app.post("/api/favorites")
    async def add_favorite(f: FavoriteRequest):
        return _store().add_favorite(f.name, f.lat, f.lon)

    @app.delete("/api/favorites/{fav_id}")
    async def delete_favorite(fav_id: str):
        if not _store().remove_favorite(fav_id):
            raise HTTPException(404, "Favorite not found")
        return {"ok": True}

    @app.get("/api/history")
    async def history():
        return _store().history()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = controller.subscribe()

        async def sender():
            await websocket.send_json(controller.status())
            while True:
                await websocket.send_json(await q.get())

        async def receiver():
            # Watching for the close is what lets the server shut down while a tab is open.
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await t
            controller.unsubscribe(q)

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
