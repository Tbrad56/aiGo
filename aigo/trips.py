"""Starting trips from stops, saved routes, and timed schedules."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from aigo.controller import Controller
from aigo.device import DeviceError
from aigo.routing import FetchJson, RoutingError, Stop, plan_trip

log = logging.getLogger(__name__)

TRAVEL_VARIATION = 0.15
SPEED_PRESETS_KMH = {"walk": 5, "run": 10, "bike": 18, "drive": 50}


def trip_origin(controller: Controller):
    """Where trips begin: Home when it's set and "start trips from Home" is on, else the phone's spot."""
    store = controller.store
    home = store.setting("home") if store else None
    if home and store.setting("start_from_home", True):
        return (float(home["lat"]), float(home["lon"]))
    return controller.position


def stops_from(items: list[dict[str, Any]]) -> list[Stop]:
    return [Stop(float(s["lat"]), float(s["lon"]), float(s.get("wait_min") or 0) * 60) for s in items]


async def start_trip(
    controller: Controller,
    stops: list[Stop],
    speed_kmh: float,
    follow_roads: bool = True,
    loop: bool = False,
    variation: float = TRAVEL_VARIATION,
    fetch_json: Optional[FetchJson] = None,
) -> dict[str, Any]:
    """Plan a trip from its origin (Home or the phone's spot) through `stops`, then start driving it.

    The phone jumps to the origin first if it isn't already there."""
    plan = await plan_trip(trip_origin(controller), stops, speed_kmh, follow_roads, loop, fetch_json)
    result = await controller.start_route(
        plan["points"], speed_kmh, loop=loop, variation=variation, from_current=False, dwell=plan["dwell"]
    )
    return {**result, "note": plan["note"], "path": plan["points"]}


async def play_saved_route(
    controller: Controller, route: dict[str, Any], fetch_json: Optional[FetchJson] = None
) -> dict[str, Any]:
    return await start_trip(
        controller,
        stops_from(route.get("stops", [])),
        float(route.get("speed_kmh") or 5),
        follow_roads=bool(route.get("follow_roads", True)),
        loop=bool(route.get("loop", False)),
        variation=TRAVEL_VARIATION if route.get("vary", True) else 0.0,
        fetch_json=fetch_json,
    )


class Scheduler:
    """Runs schedule entries at their time on their days, while aiGo is open.

    Entry: {id, name, enabled, time "HH:MM", days [0=Mon..6=Sun], action, favorite_id?, route_id?,
    speed_kmh?}. Actions: "teleport" or "travel" to a favorite, "route" (play a saved route),
    "stop" (stop spoofing). A time that passed while aiGo was closed is not run later.
    """

    def __init__(
        self,
        controller: Controller,
        *,
        now: Callable[[], datetime] = datetime.now,
        tick_s: float = 15.0,
        window: timedelta = timedelta(minutes=2),
        fetch_json: Optional[FetchJson] = None,
    ):
        self.controller = controller
        self.store = controller.store
        self.now = now
        self.tick_s = tick_s
        self.window = window
        self.fetch_json = fetch_json
        self.last_runs: dict[str, dict[str, Any]] = {}
        self._fired: dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick_s)
            try:
                await self.check()
            except Exception:  # never let one bad entry stop the scheduler
                log.exception("schedule check failed")

    def _slot(self, entry: dict[str, Any], now: datetime) -> Optional[str]:
        """The firing key if `entry` is due now, else None."""
        if not entry.get("enabled", True) or now.weekday() not in entry.get("days", range(7)):
            return None
        try:
            hour, minute = (int(x) for x in str(entry["time"]).split(":"))
        except (KeyError, ValueError):
            return None
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not at <= now < at + self.window:
            return None
        key = at.isoformat()
        return None if self._fired.get(entry["id"]) == key else key

    async def check(self) -> list[str]:
        if self.store is None:
            return []
        now = self.now()
        ran = []
        for entry in self.store.items("schedules"):
            key = self._slot(entry, now)
            if key:
                self._fired[entry["id"]] = key
                await self.run(entry)
                ran.append(entry["id"])
        return ran

    async def run(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Carry out one entry now (also used by the page's "Run now")."""
        try:
            message, ok = await self._do(entry), True
        except (ValueError, DeviceError, RoutingError) as exc:
            message, ok = str(exc), False
        record = {"at": self.now().isoformat(timespec="minutes"), "ok": ok, "message": message}
        self.last_runs[entry["id"]] = record
        label = entry.get("name") or entry.get("time", "")
        self.controller.announce(f"Schedule “{label}”: {message}", ok)
        return record

    async def _do(self, entry: dict[str, Any]) -> str:
        action = entry.get("action")
        ctl, store = self.controller, self.store
        if action == "stop":
            await ctl.clear()
            return "Stopped spoofing."
        if action == "route":
            route = store.get("routes", entry.get("route_id", "")) if store else None
            if not route:
                raise ValueError("That saved route was deleted.")
            await play_saved_route(ctl, route, self.fetch_json)
            return f"Started the route “{route['name']}”."
        fav = store.get("favorites", entry.get("favorite_id", "")) if store else None
        if not fav:
            raise ValueError("That favorite was deleted.")
        if action == "teleport":
            await ctl.teleport(fav["lat"], fav["lon"])
            return f"Teleported to {fav['name']}."
        if action == "travel":
            speed = float(entry.get("speed_kmh") or SPEED_PRESETS_KMH["drive"])
            result = await start_trip(ctl, [Stop(fav["lat"], fav["lon"])], speed, fetch_json=self.fetch_json)
            mins = round(result["eta_s"] / 60)
            return f"Heading to {fav['name']}, about {mins} min."
        raise ValueError(f"Unknown action: {action}")
