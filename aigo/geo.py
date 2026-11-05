"""Geographic helpers: distances, bearings and evenly spaced points along a route."""

from __future__ import annotations

import math
from typing import Callable, Iterator, Optional

LatLon = tuple[float, float]

EARTH_RADIUS_M = 6_371_008.8


def distance_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance between two points, in meters (haversine)."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: LatLon, b: LatLon) -> float:
    """Initial bearing from a to b, in degrees clockwise from north (0-360)."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def destination(start: LatLon, bearing: float, meters: float) -> LatLon:
    """The point reached by travelling `meters` from `start` on `bearing` degrees."""
    lat1, lon1 = map(math.radians, start)
    brg = math.radians(bearing)
    d = meters / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    lon_deg = (math.degrees(lon2) + 540) % 360 - 180
    return (math.degrees(lat2), lon_deg)


def _lerp(a: LatLon, b: LatLon, f: float) -> LatLon:
    # Linear interpolation is accurate enough for the short segments of a route.
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def walk_path(points: list[LatLon], next_step: Callable[[], float]) -> Iterator[LatLon]:
    """Yield positions along the polyline `points`, each `next_step()` meters past the previous one.

    The first point is not yielded (callers already stand on it); the last point always is.
    """
    if len(points) < 2:
        yield from points[-1:]
        return

    last: Optional[LatLon] = None
    need = _positive(next_step())  # meters still to go before the next position
    for a, b in zip(points, points[1:]):
        seg = distance_m(a, b)
        if seg == 0:
            continue
        pos = 0.0
        while seg - pos >= need:
            pos += need
            last = _lerp(a, b, pos / seg)
            yield last
            need = _positive(next_step())
        need -= seg - pos
    if last is None or distance_m(last, points[-1]) > 1e-6:
        yield points[-1]


def _positive(step: float) -> float:
    if step <= 0:
        raise ValueError("step must be positive")
    return step


def route_positions(points: list[LatLon], step_m: float) -> list[LatLon]:
    """Positions spaced `step_m` apart along `points`, ending exactly on the last point."""
    if step_m <= 0:
        raise ValueError("step_m must be positive")
    return list(walk_path(points, lambda: step_m))


def path_length_m(points: list[LatLon]) -> float:
    return sum(distance_m(a, b) for a, b in zip(points, points[1:]))
