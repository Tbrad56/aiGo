import pytest

from aigo.routing import RoutingError, Stop, _locate, plan_trip, profile_for, road_path

A, B, C = (40.0, -73.0), (40.01, -73.0), (40.01, -73.01)


def osrm_reply(points, waypoints):
    return {
        "code": "Ok",
        "routes": [{"geometry": {"coordinates": [[lon, lat] for lat, lon in points]}}],
        "waypoints": [{"location": [lon, lat]} for lat, lon in waypoints],
    }


def test_profile_for_speed():
    assert profile_for(5) == "routed-foot"
    assert profile_for(18) == "routed-bike"
    assert profile_for(50) == "routed-car"


async def test_road_path_parses_and_locates_waypoints():
    geometry = [A, (40.005, -73.001), B, (40.01, -73.005), C]
    urls = []

    async def fetch(url):
        urls.append(url)
        return osrm_reply(geometry, [A, B, C])

    pts, where = await road_path([A, B, C], 50, fetch)
    assert pts == geometry
    assert where == [0, 2, 4]
    assert "routed-car" in urls[0] and "-73.000000,40.000000;" in urls[0]


async def test_road_path_errors():
    async def no_route(url):
        return {"code": "NoRoute"}

    with pytest.raises(RoutingError):
        await road_path([A, B], 5, no_route)
    with pytest.raises(RoutingError):
        await road_path([A], 5, no_route)


def test_locate_keeps_order_on_loops():
    # A path that passes the same corner twice: the second target must match the later visit.
    path = [A, B, C, B, A]
    assert _locate(path, [A, B, A]) == [0, 1, 4]


async def test_plan_trip_maps_waits_to_path_indices():
    geometry = [A, (40.005, -73.0), B, (40.01, -73.005), C]

    async def fetch(url):
        return osrm_reply(geometry, [A, B, C])

    plan = await plan_trip(A, [Stop(*B, wait_s=600), Stop(*C)], 5, fetch_json=fetch)
    assert plan["points"] == geometry
    assert plan["dwell"] == {2: 600}
    assert plan["note"] is None


async def test_plan_trip_falls_back_to_straight_lines():
    async def down(url):
        raise RoutingError("Couldn't reach the road routing service.")

    plan = await plan_trip(A, [Stop(*B, wait_s=60)], 5, fetch_json=down)
    assert plan["points"] == [A, B]
    assert plan["dwell"] == {1: 60}
    assert "straight lines" in plan["note"]


async def test_plan_trip_without_roads_and_without_start():
    plan = await plan_trip(None, [Stop(*A), Stop(*B, wait_s=5)], 5, follow_roads=False)
    assert plan["points"] == [A, B] and plan["dwell"] == {1: 5}
    with pytest.raises(ValueError):
        await plan_trip(None, [Stop(*A)], 5, follow_roads=False)


async def test_plan_trip_loop_returns_to_start():
    plan = await plan_trip(A, [Stop(*B)], 5, follow_roads=False, loop=True)
    assert plan["points"] == [A, B, A]
