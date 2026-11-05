"""The app's brain: owns the device connection, the current position and any moving route."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from aigo.device import DeviceError, LocationDevice
from aigo.geo import LatLon, destination, distance_m, path_length_m, walk_path
from aigo.store import Store

log = logging.getLogger(__name__)

# Trips implying more than this speed look unrealistic to apps like Life360.
SUSPICIOUS_SPEED_MPS = 300 / 3.6
SUSPICIOUS_MIN_JUMP_M = 1000


class Controller:
    def __init__(
        self,
        device: LocationDevice,
        store: Optional[Store] = None,
        *,
        interval: float = 1.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        fake: bool = False,
    ):
        self.device = device
        self.store = store
        self.interval = interval
        self._sleep = sleep
        self._clock = clock
        self.fake = fake
        self.rng = random.Random()
        # While holding a position, re-send it this often. This notices a dropped link (e.g. Wi-Fi
        # when the phone sleeps), reconnects, and puts the simulated location back.
        self.keepalive_interval = 5.0
        self._keepalive_task: Optional[asyncio.Task] = None

        self.connection = "disconnected"  # disconnected | connecting | connected | error
        self.error: Optional[str] = None
        self.position: Optional[LatLon] = None
        self.mode = "idle"  # idle | holding | route
        self.route_progress: Optional[dict[str, Any]] = None
        self.last_event: Optional[dict[str, Any]] = None  # e.g. a schedule that just ran

        self._last_set_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._route_task: Optional[asyncio.Task] = None
        self._route: Optional[_RouteState] = None
        self._listeners: set[asyncio.Queue] = set()

    # ----- status / subscriptions -----
    def status(self) -> dict[str, Any]:
        info = self.device.info
        return {
            "connection": self.connection,
            "error": self.error,
            "fake": self.fake,
            "device": {
                "name": info.name,
                "ios": info.ios,
                "model": info.model,
                "udid": info.udid,
                "transport": info.transport,
            },
            "position": {"lat": self.position[0], "lon": self.position[1]} if self.position else None,
            "mode": self.mode,
            "route": self.route_progress,
            "event": self.last_event,
        }

    def announce(self, text: str, ok: bool = True) -> None:
        """Tell every open page about something that happened on its own (shown as a toast)."""
        n = (self.last_event or {}).get("id", 0) + 1
        self.last_event = {"id": n, "text": text, "ok": ok}
        self._notify()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _notify(self) -> None:
        snapshot = self.status()
        for q in list(self._listeners):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(snapshot)

    # ----- connection -----
    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        self.connection, self.error = "connecting", None
        self._notify()
        try:
            await self.device.connect()
        except DeviceError as exc:
            self.connection, self.error = "error", str(exc)
            self._notify()
            raise
        self.connection = "connected"
        self._notify()
        # A fresh session has no simulated location; put the last one back.
        if self.position is not None and self.mode != "idle":
            await self.device.set_location(*self.position)

    async def _set(self, pos: LatLon) -> None:
        """Send a position to the phone, reconnecting once if the link dropped."""
        async with self._lock:
            try:
                await self.device.set_location(*pos)
            except DeviceError as exc:
                log.warning("set failed (%s); reconnecting", exc)
                self.connection, self.error = "error", str(exc)
                self._notify()
                await self._connect_locked()
                await self.device.set_location(*pos)
            self.position = pos
            self._last_set_at = self._clock()
            if self.error:
                self.error = None

    # ----- actions -----
    async def teleport(self, lat: float, lon: float) -> dict[str, Any]:
        await self.stop_route()
        target = (lat, lon)
        warning = self._jump_warning(target)
        await self._set(target)
        self.mode = "holding"
        if self.store:
            self.store.add_history(lat, lon)
        self._notify()
        return {"warning": warning}

    def _jump_warning(self, target: LatLon) -> Optional[str]:
        if self.position is None or self._last_set_at is None:
            return None
        dist = distance_m(self.position, target)
        elapsed = max(self._clock() - self._last_set_at, 1.0)
        if dist >= SUSPICIOUS_MIN_JUMP_M and dist / elapsed > SUSPICIOUS_SPEED_MPS:
            return (
                f"That's a {dist / 1000:.1f} km jump. Apps like Life360 may flag travel faster than "
                f"300 km/h; use a route to move there gradually."
            )
        return None

    async def clear(self) -> None:
        await self.stop_route()
        async with self._lock:
            await self.device.clear_location()
        self.position = None
        self._last_set_at = None
        self.mode = "idle"
        self._notify()

    async def step(self, bearing: float, meters: float) -> None:
        """Joystick: move from the current position."""
        if self.position is None:
            raise ValueError("Teleport somewhere first, then use the joystick.")
        await self.stop_route()
        await self._set(destination(self.position, bearing, meters))
        self.mode = "holding"
        self._notify()

    async def start_route(
        self,
        points: list[LatLon],
        speed_kmh: float,
        loop: bool = False,
        variation: float = 0.0,
        from_current: bool = True,
        dwell: Optional[dict[int, float]] = None,
    ) -> dict[str, Any]:
        """Travel along `points` at `speed_kmh`.

        `variation` (0-0.5) randomly varies each step's length by up to that fraction, so the pace
        isn't perfectly even. With `from_current=False` (e.g. a GPX track) the phone jumps to the
        first point instead of travelling there from where it is. `dwell` maps an index in `points`
        to seconds to wait there (a stop on an errand)."""
        if speed_kmh <= 0:
            raise ValueError("Speed must be above 0.")
        if not 0 <= variation <= 0.5:
            raise ValueError("Speed variation must be between 0 and 0.5.")
        dwell = dict(dwell or {})
        if any(v < 0 for v in dwell.values()):
            raise ValueError("Wait times can't be negative.")
        if from_current and self.position is not None:
            points = [self.position, *points]
            dwell = {i + 1: v for i, v in dwell.items()}
        if len(points) < 2:
            raise ValueError("A route needs at least two points.")
        if loop and points[0] != points[-1]:
            points = [*points, points[0]]
        await self.stop_route()

        legs = _split_legs(points, dwell)
        route = _RouteState(
            speed_mps=speed_kmh / 3.6,
            variation=variation,
            loop=loop,
            legs=legs,
            total_m=sum(path_length_m(pts) for pts, _ in legs),
            total_wait_s=sum(w for _, w in legs),
        )
        self._route = route
        self.mode = "route"
        self._route_task = asyncio.create_task(self._run_route(route))
        self._publish_route()
        return {"total_m": route.total_m, "eta_s": route.eta_s(), "stops": len(legs)}

    async def _run_route(self, route: "_RouteState") -> None:
        def next_step() -> float:
            base = route.speed_mps * self.interval
            v = route.variation
            return base * (1 + self.rng.uniform(-v, v)) if v else base

        try:
            first = route.legs[0][0][0]
            if self.position is None or distance_m(self.position, first) > 1:
                await self._set(first)
            while True:
                route.start_lap()
                for leg_index, (pts, wait_s) in enumerate(route.legs):
                    route.leg = leg_index
                    prev = pts[0]
                    for pos in walk_path(pts, next_step):
                        await self._sleep(self.interval)
                        await route.resumed.wait()
                        await self._set(pos)
                        route.done_m += distance_m(prev, pos)
                        prev = pos
                        self._publish_route()
                    if wait_s > 0:
                        await self._wait_at_stop(route, wait_s)
                if not route.loop:
                    break
                route.lap += 1
            if self.store and self.position:
                self.store.add_history(*self.position)
        except asyncio.CancelledError:
            raise
        except DeviceError as exc:
            self.error = str(exc)
            log.error("route stopped: %s", exc)
        finally:
            if self._route_task is asyncio.current_task():
                self._route_task = None
                self._route = None
                self.mode = "holding" if self.position else "idle"
                self.route_progress = None
                self._notify()

    async def _wait_at_stop(self, route: "_RouteState", wait_s: float) -> None:
        route.waiting_s = wait_s
        self._publish_route()
        while route.waiting_s > 0:
            tick = min(self.interval, route.waiting_s)
            await self._sleep(tick)
            await route.resumed.wait()  # a paused wait doesn't count down
            route.waiting_s = max(0.0, route.waiting_s - tick)
            route.waited_s += tick
            self._publish_route()
        route.waiting_s = 0.0

    def _publish_route(self) -> None:
        """Refresh the route's progress and push it to every open page."""
        if self._route is not None:
            self.route_progress = self._route.progress()
        self._notify()

    def _require_route(self) -> "_RouteState":
        if self._route is None or self.mode != "route":
            raise ValueError("No route is running.")
        return self._route

    async def pause_route(self) -> None:
        route = self._require_route()
        route.resumed.clear()
        self._publish_route()

    async def resume_route(self) -> None:
        route = self._require_route()
        route.resumed.set()
        self._publish_route()

    async def set_route_speed(self, speed_kmh: float) -> None:
        if speed_kmh <= 0:
            raise ValueError("Speed must be above 0.")
        route = self._require_route()
        route.speed_mps = speed_kmh / 3.6  # takes effect on the next step
        self._publish_route()

    async def stop_route(self) -> None:
        task, self._route_task = self._route_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._route = None
        if self.mode == "route":
            self.mode = "holding" if self.position else "idle"
            self.route_progress = None
            self._notify()

    # ----- keepalive -----
    def start_keepalive(self) -> asyncio.Task:
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        return self._keepalive_task

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(self.keepalive_interval)
            if self.position is None or self.mode == "idle":
                continue
            # A moving route sends every second (so `recent` skips it); a paused route or a wait
            # at a stop sends nothing, so it gets re-sent here like a held teleport.
            recent = self._last_set_at is not None and (
                self._clock() - self._last_set_at < self.keepalive_interval * 0.8
            )
            if self.connection == "connected" and recent:
                continue
            try:
                await self._set(self.position)
                self._notify()
            except DeviceError as exc:
                self.connection = "error"
                self.error = f"Lost the iPhone: {exc} aiGo keeps retrying every {self.keepalive_interval:g} s."
                log.warning("keepalive: %s", exc)
                self._notify()

    async def wait_route(self) -> None:
        if self._route_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._route_task

    async def shutdown(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
        await self.stop_route()
        with contextlib.suppress(Exception):
            async with self._lock:
                if self.position is not None:
                    await self.device.clear_location()
        with contextlib.suppress(Exception):
            await self.device.close()


def _split_legs(points: list[LatLon], dwell: dict[int, float]) -> list[tuple[list[LatLon], float]]:
    """Cut a path into legs that end where the phone waits. Returns [(leg points, wait seconds)]."""
    last = len(points) - 1
    cuts = sorted(i for i, secs in dwell.items() if 0 < i <= last and secs > 0)
    legs: list[tuple[list[LatLon], float]] = []
    prev = 0
    for c in cuts:
        legs.append((points[prev : c + 1], float(dwell[c])))
        prev = c
    if prev < last:
        legs.append((points[prev:], 0.0))
    return legs


def _new_event() -> asyncio.Event:
    e = asyncio.Event()
    e.set()
    return e


@dataclass
class _RouteState:
    speed_mps: float
    variation: float
    loop: bool
    legs: list[tuple[list[LatLon], float]]
    total_m: float
    total_wait_s: float
    done_m: float = 0.0
    waited_s: float = 0.0
    waiting_s: float = 0.0
    leg: int = 0
    lap: int = 0
    resumed: asyncio.Event = field(default_factory=_new_event)  # cleared while paused

    def start_lap(self) -> None:
        self.done_m = 0.0
        self.waited_s = 0.0
        self.waiting_s = 0.0

    def eta_s(self) -> float:
        moving = max(self.total_m - self.done_m, 0.0) / self.speed_mps
        waiting = max(self.total_wait_s - self.waited_s, 0.0)
        return moving + waiting

    def progress(self) -> dict[str, Any]:
        return {
            "done_m": self.done_m,
            "total_m": self.total_m,
            "speed_kmh": self.speed_mps * 3.6,
            "loop": self.loop,
            "eta_s": self.eta_s(),
            "paused": not self.resumed.is_set(),
            "waiting_s": self.waiting_s,
            "leg": self.leg + 1,
            "legs": len(self.legs),
            "lap": self.lap + 1,
        }
