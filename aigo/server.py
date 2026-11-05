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
from typing import Literal, Optional

from pydantic import BaseModel, Field

from aigo.controller import Controller
from aigo.device import DeviceError
from aigo.routing import FetchJson, RoutingError, plan_trip
from aigo.security import LocalOnlyMiddleware
from aigo.trips import Scheduler, play_saved_route, start_trip, stops_from, trip_origin

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    points: list[Point] = Field(min_length=1, max_length=20000)
    speed_kmh: float = Field(gt=0, le=1000)
    loop: bool = False
    variation: float = Field(default=0.0, ge=0, le=0.5)
    from_current: bool = True


class StepRequest(BaseModel):
    bearing: float = Field(ge=0, lt=360)
    meters: float = Field(gt=0, le=1000)


class FavoriteRequest(Point):
    name: str = Field(default="", max_length=100)


class StopIn(Point):
    wait_min: float = Field(default=0, ge=0, le=24 * 60)


class TripRequest(BaseModel):
    stops: list[StopIn] = Field(min_length=1, max_length=5000)
    speed_kmh: float = Field(gt=0, le=1000)
    follow_roads: bool = True
    loop: bool = False
    vary: bool = True


class HomeRequest(Point):
    name: str = Field(default="Home", max_length=100)


class HomeSettings(BaseModel):
    start_from_home: bool


class SpeedRequest(BaseModel):
    speed_kmh: float = Field(gt=0, le=1000)


class SavedRoute(TripRequest):
    name: str = Field(min_length=1, max_length=100)


class ScheduleIn(BaseModel):
    name: str = Field(default="", max_length=100)
    enabled: bool = True
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(min_length=1, max_length=7)
    action: Literal["teleport", "travel", "route", "stop"]
    favorite_id: Optional[str] = None
    route_id: Optional[str] = None
    speed_kmh: Optional[float] = Field(default=None, gt=0, le=1000)


def _check_schedule(s: ScheduleIn) -> dict:
    if any(d < 0 or d > 6 for d in s.days):
        raise HTTPException(422, "Days must be 0 (Monday) to 6 (Sunday).")
    if s.action in ("teleport", "travel") and not s.favorite_id:
        raise HTTPException(422, "Pick a favorite place for this schedule.")
    if s.action == "route" and not s.route_id:
        raise HTTPException(422, "Pick a saved route for this schedule.")
    return {**s.model_dump(), "days": sorted(set(s.days))}


def create_app(
    controller: Controller,
    *,
    connect_on_start: bool = True,
    fetch_json: Optional[FetchJson] = None,
    scheduler: Optional[Scheduler] = None,
) -> FastAPI:
    scheduler = scheduler or Scheduler(controller, fetch_json=fetch_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if connect_on_start:
            # Connect in the background so the UI loads right away and shows progress.
            async def _connect():
                with contextlib.suppress(DeviceError):
                    await controller.connect()

            task = asyncio.create_task(_connect())
        controller.start_keepalive()
        scheduler.start()
        yield
        if task:
            task.cancel()
        await scheduler.stop()
        await controller.shutdown()

    app = FastAPI(title="aiGo", lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware)

    async def run(coro):
        try:
            return await coro
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except DeviceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except RoutingError as exc:
            raise HTTPException(502, str(exc)) from exc

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
        points = [(p.lat, p.lon) for p in r.points]
        result = await run(controller.start_route(points, r.speed_kmh, r.loop, r.variation, r.from_current))
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

    # ----- trips (stops planned on the server, optionally along roads) -----
    @app.post("/api/directions")
    async def directions(t: TripRequest):
        """Preview what /api/trip would drive, without starting it."""
        plan = await run(
            plan_trip(trip_origin(controller), stops_from([x.model_dump() for x in t.stops]), t.speed_kmh,
                      t.follow_roads, t.loop, fetch_json)
        )
        return {"points": plan["points"], "dwell": [[i, s] for i, s in plan["dwell"].items()], "note": plan["note"]}

    @app.post("/api/trip")
    async def trip(t: TripRequest):
        result = await run(
            start_trip(controller, stops_from([x.model_dump() for x in t.stops]), t.speed_kmh, t.follow_roads,
                       t.loop, 0.15 if t.vary else 0.0, fetch_json)
        )
        return {**result, "status": controller.status()}

    # ----- home -----
    def _home_state() -> dict:
        s = _store()
        return {"home": s.setting("home"), "start_from_home": s.setting("start_from_home", True)}

    @app.get("/api/home")
    async def home_get():
        return _home_state()

    @app.put("/api/home")
    async def home_set(h: HomeRequest):
        _store().set_setting("home", {"lat": h.lat, "lon": h.lon, "name": h.name.strip() or "Home"})
        return _home_state()

    @app.delete("/api/home")
    async def home_clear():
        _store().set_setting("home", None)
        return _home_state()

    @app.put("/api/home/settings")
    async def home_settings(h: HomeSettings):
        _store().set_setting("start_from_home", h.start_from_home)
        return _home_state()

    @app.post("/api/route/pause")
    async def route_pause():
        await run(controller.pause_route())
        return controller.status()

    @app.post("/api/route/resume")
    async def route_resume():
        await run(controller.resume_route())
        return controller.status()

    @app.post("/api/route/speed")
    async def route_speed(r: SpeedRequest):
        await run(controller.set_route_speed(r.speed_kmh))
        return controller.status()

    # ----- saved routes -----
    @app.get("/api/routes")
    async def routes_list():
        return _store().items("routes")

    @app.post("/api/routes")
    async def routes_add(r: SavedRoute):
        return _store().add("routes", r.model_dump())

    @app.put("/api/routes/{route_id}")
    async def routes_update(route_id: str, r: SavedRoute):
        saved = _store().update("routes", route_id, r.model_dump())
        if saved is None:
            raise HTTPException(404, "Saved route not found")
        return saved

    @app.delete("/api/routes/{route_id}")
    async def routes_delete(route_id: str):
        if not _store().remove("routes", route_id):
            raise HTTPException(404, "Saved route not found")
        return {"ok": True}

    @app.post("/api/routes/{route_id}/play")
    async def routes_play(route_id: str):
        route = _store().get("routes", route_id)
        if route is None:
            raise HTTPException(404, "Saved route not found")
        result = await run(play_saved_route(controller, route, fetch_json))
        return {**result, "status": controller.status()}

    # ----- schedules -----
    def _with_last_run(entry: dict) -> dict:
        return {**entry, "last_run": scheduler.last_runs.get(entry["id"])}

    @app.get("/api/schedules")
    async def schedules_list():
        return [_with_last_run(e) for e in _store().items("schedules")]

    @app.post("/api/schedules")
    async def schedules_add(s: ScheduleIn):
        return _with_last_run(_store().add("schedules", _check_schedule(s)))

    @app.put("/api/schedules/{entry_id}")
    async def schedules_update(entry_id: str, s: ScheduleIn):
        saved = _store().update("schedules", entry_id, _check_schedule(s))
        if saved is None:
            raise HTTPException(404, "Schedule not found")
        return _with_last_run(saved)

    @app.delete("/api/schedules/{entry_id}")
    async def schedules_delete(entry_id: str):
        if not _store().remove("schedules", entry_id):
            raise HTTPException(404, "Schedule not found")
        return {"ok": True}

    @app.post("/api/schedules/{entry_id}/run")
    async def schedules_run(entry_id: str):
        entry = _store().get("schedules", entry_id)
        if entry is None:
            raise HTTPException(404, "Schedule not found")
        return await scheduler.run(entry)

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
