"""Road routing (FOSSGIS OSRM) and trip planning from a list of stops.

Planning lives on the server so saved routes and schedules work with no browser page open.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from aigo.geo import LatLon

ROUTER_URL = "https://routing.openstreetmap.de"
USER_AGENT = "aiGo/0.1 (personal location simulator)"
MAX_ROAD_WAYPOINTS = 100

FetchJson = Callable[[str], Awaitable[Any]]


class RoutingError(Exception):
    """Road routing failed; the message is fit to show the user."""


@dataclass
class Stop:
    lat: float
    lon: float
    wait_s: float = 0.0


def profile_for(speed_kmh: float) -> str:
    """Walking and running use footpaths, biking bike lanes, anything faster the roads."""
    if speed_kmh <= 12:
        return "routed-foot"
    if speed_kmh <= 25:
        return "routed-bike"
    return "routed-car"


async def _fetch_json(url: str) -> Any:
    def get() -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.load(res)

    try:
        return await asyncio.to_thread(get)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RoutingError("Couldn't reach the road routing service.") from exc


async def road_path(
    waypoints: list[LatLon], speed_kmh: float, fetch_json: Optional[FetchJson] = None
) -> tuple[list[LatLon], list[int]]:
    """A path along real roads through `waypoints`, plus where each waypoint falls in that path."""
    if len(waypoints) < 2:
        raise RoutingError("A road route needs at least two points.")
    if len(waypoints) > MAX_ROAD_WAYPOINTS:
        raise RoutingError("Too many stops to follow roads.")
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints)
    url = f"{ROUTER_URL}/{profile_for(speed_kmh)}/route/v1/driving/{coords}?overview=full&geometries=geojson"
    data = await (fetch_json or _fetch_json)(url)
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError("No road route found there.")
    points: list[LatLon] = [(lat, lon) for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    snapped: list[LatLon] = [(w["location"][1], w["location"][0]) for w in data.get("waypoints", [])]
    if len(points) < 2 or len(snapped) != len(waypoints):
        raise RoutingError("The road routing service sent an unexpected answer.")
    return points, _locate(points, snapped)


def _locate(points: list[LatLon], targets: list[LatLon]) -> list[int]:
    """Index in `points` of each target, searching forward so loops and revisits stay in order."""
    out: list[int] = []
    start = 0
    for t in targets:
        best, best_d = start, float("inf")
        for k in range(start, len(points)):
            d = (points[k][0] - t[0]) ** 2 + (points[k][1] - t[1]) ** 2  # degrees²: fine for nearest
            if d < best_d:
                best, best_d = k, d
            if d < 1e-12:  # OSRM puts the snapped waypoint on a vertex; first exact hit wins
                break
        out.append(best)
        start = best
    out[-1] = len(points) - 1
    return out


async def plan_trip(
    start: Optional[LatLon],
    stops: list[Stop],
    speed_kmh: float,
    follow_roads: bool = True,
    loop: bool = False,
    fetch_json: Optional[FetchJson] = None,
) -> dict[str, Any]:
    """Turn stops into a path the controller can drive: {points, dwell, note}.

    The path starts at `start` (the phone's current position) when given. `dwell` maps path indices
    to seconds to wait. If road routing fails the trip falls back to straight lines and `note`
    says why.
    """
    if not stops:
        raise ValueError("Add at least one stop.")
    waypoints: list[LatLon] = ([start] if start else []) + [(s.lat, s.lon) for s in stops]
    if len(waypoints) < 2:
        raise ValueError("Teleport somewhere first, then travel from there.")
    if loop:
        waypoints.append(waypoints[0])
    offset = 1 if start else 0

    note = None
    points, where = waypoints, list(range(len(waypoints)))
    if follow_roads:
        try:
            points, where = await road_path(waypoints, speed_kmh, fetch_json)
        except RoutingError as exc:
            note = f"{exc} Using straight lines."
    dwell = {where[i + offset]: s.wait_s for i, s in enumerate(stops) if s.wait_s > 0}
    return {"points": points, "dwell": dwell, "note": note}
