"""The app's brain: owns the device connection, the current position and any moving route."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from aigo.device import DeviceError, LocationDevice
from aigo.geo import LatLon, destination, distance_m, path_length_m, route_positions
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

        self.connection = "disconnected"  # disconnected | connecting | connected | error
        self.error: Optional[str] = None
        self.position: Optional[LatLon] = None
        self.mode = "idle"  # idle | holding | route
        self.route_progress: Optional[dict[str, Any]] = None

        self._last_set_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._route_task: Optional[asyncio.Task] = None
        self._listeners: set[asyncio.Queue] = set()

    # ----- status / subscriptions -----
    def status(self) -> dict[str, Any]:
        info = self.device.info
        return {
            "connection": self.connection,
            "error": self.error,
            "fake": self.fake,
            "device": {"name": info.name, "ios": info.ios, "model": info.model, "udid": info.udid},
            "position": {"lat": self.position[0], "lon": self.position[1]} if self.position else None,
            "mode": self.mode,
            "route": self.route_progress,
        }

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

    async def start_route(self, points: list[LatLon], speed_kmh: float, loop: bool = False) -> dict[str, Any]:
        if speed_kmh <= 0:
            raise ValueError("Speed must be above 0.")
        if self.position is not None:
            points = [self.position, *points]
        if len(points) < 2:
            raise ValueError("A route needs at least two points.")
        if loop and points[0] != points[-1]:
            points = [*points, points[0]]
        await self.stop_route()
        speed_mps = speed_kmh / 3.6
        length = path_length_m(points)
        self.mode = "route"
        self.route_progress = {
            "done_m": 0.0,
            "total_m": length,
            "speed_kmh": speed_kmh,
            "loop": loop,
            "eta_s": length / speed_mps,
        }
        self._route_task = asyncio.create_task(self._run_route(points, speed_mps, loop))
        self._notify()
        return {"total_m": length, "eta_s": length / speed_mps}

    async def _run_route(self, points: list[LatLon], speed_mps: float, loop: bool) -> None:
        step_m = speed_mps * self.interval
        try:
            if self.position is None or distance_m(self.position, points[0]) > 1:
                await self._set(points[0])
            while True:
                total = path_length_m(points)
                done = 0.0
                prev = points[0]
                for pos in route_positions(points, step_m):
                    await self._sleep(self.interval)
                    await self._set(pos)
                    done += distance_m(prev, pos)
                    prev = pos
                    self.route_progress = {
                        **(self.route_progress or {}),
                        "done_m": done,
                        "total_m": total,
                        "eta_s": max(total - done, 0) / speed_mps,
                    }
                    self._notify()
                if not loop:
                    break
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
                self.mode = "holding" if self.position else "idle"
                self.route_progress = None
                self._notify()

    async def stop_route(self) -> None:
        task, self._route_task = self._route_task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.mode == "route":
            self.mode = "holding" if self.position else "idle"
            self.route_progress = None
            self._notify()

    async def wait_route(self) -> None:
        if self._route_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._route_task

    async def shutdown(self) -> None:
        await self.stop_route()
        with contextlib.suppress(Exception):
            async with self._lock:
                if self.position is not None:
                    await self.device.clear_location()
        with contextlib.suppress(Exception):
            await self.device.close()
