from datetime import datetime, timedelta

import pytest

from aigo.controller import Controller
from aigo.device import FakeDevice
from aigo.routing import RoutingError, Stop
from aigo.store import Store
from aigo.trips import Scheduler, play_saved_route, start_trip

HOME = (40.7580, -73.9855)
GYM = (40.7484, -73.9857)


async def no_sleep(_):
    import asyncio

    await asyncio.sleep(0)


async def offline(url):
    raise RoutingError("Couldn't reach the road routing service.")


def make(tmp_path):
    ctl = Controller(FakeDevice(), Store(tmp_path / "d.json"), sleep=no_sleep, fake=True)
    return ctl, ctl.device, ctl.store


async def test_start_trip_waits_at_stops_and_reports_note(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*HOME)
    res = await start_trip(ctl, [Stop(*GYM, wait_s=2)], 360, fetch_json=offline, variation=0)
    assert "straight lines" in res["note"]
    assert res["path"][0] == HOME and res["path"][-1] == GYM
    await ctl.wait_route()
    assert dev.location == GYM


async def test_play_saved_route(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*HOME)
    route = store.add("routes", {"name": "Gym", "stops": [{"lat": GYM[0], "lon": GYM[1], "wait_min": 0}],
                                 "speed_kmh": 360, "follow_roads": False, "loop": False, "vary": False})
    await play_saved_route(ctl, route)
    await ctl.wait_route()
    assert dev.location == GYM


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


async def test_scheduler_fires_once_in_window_on_right_day(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    fav = store.add_favorite("Gym", *GYM)
    monday_9 = datetime(2026, 11, 2, 9, 0, 30)  # a Monday
    clock = Clock(monday_9)
    sched = Scheduler(ctl, now=clock)
    entry = store.add("schedules", {"name": "Gym time", "enabled": True, "time": "09:00", "days": [0],
                                    "action": "teleport", "favorite_id": fav["id"]})
    assert await sched.check() == [entry["id"]]
    assert dev.location == GYM
    assert sched.last_runs[entry["id"]]["ok"] is True
    assert "Gym time" in ctl.status()["event"]["text"]
    clock.t = monday_9 + timedelta(seconds=40)
    assert await sched.check() == []  # same slot doesn't fire twice
    clock.t = monday_9 + timedelta(days=1)  # Tuesday: not a scheduled day
    assert await sched.check() == []
    clock.t = monday_9 + timedelta(days=7)  # next Monday fires again
    assert await sched.check() == [entry["id"]]


async def test_scheduler_skips_disabled_and_out_of_window(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    fav = store.add_favorite("Gym", *GYM)
    store.add("schedules", {"time": "09:00", "days": list(range(7)), "enabled": False,
                            "action": "teleport", "favorite_id": fav["id"]})
    store.add("schedules", {"time": "08:00", "days": list(range(7)), "enabled": True,
                            "action": "teleport", "favorite_id": fav["id"]})
    sched = Scheduler(ctl, now=Clock(datetime(2026, 11, 2, 9, 0, 10)))
    assert await sched.check() == []
    assert dev.location is None


async def test_scheduler_reports_failures(tmp_path):
    ctl, _, store = make(tmp_path)
    await ctl.connect()
    entry = store.add("schedules", {"name": "Bad", "time": "09:00", "days": [0], "action": "teleport",
                                    "favorite_id": "gone"})
    sched = Scheduler(ctl, now=Clock(datetime(2026, 11, 2, 9, 0)))
    await sched.check()
    assert sched.last_runs[entry["id"]] == {"at": "2026-11-02T09:00", "ok": False,
                                           "message": "That favorite was deleted."}
    assert ctl.status()["event"]["ok"] is False


async def test_scheduler_actions_travel_route_stop(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*HOME)
    fav = store.add_favorite("Gym", *GYM)
    route = store.add("routes", {"name": "Back", "stops": [{"lat": HOME[0], "lon": HOME[1]}],
                                 "speed_kmh": 360, "follow_roads": False})
    sched = Scheduler(ctl, now=Clock(datetime(2026, 11, 2, 9, 0)), fetch_json=offline)
    rec = await sched.run({"id": "t", "action": "travel", "favorite_id": fav["id"], "speed_kmh": 360})
    assert rec["ok"] and "Heading to Gym" in rec["message"]
    await ctl.wait_route()
    assert dev.location == GYM
    rec = await sched.run({"id": "r", "action": "route", "route_id": route["id"]})
    assert rec["ok"]
    await ctl.wait_route()
    assert dev.location == HOME
    rec = await sched.run({"id": "s", "action": "stop"})
    assert rec["ok"] and dev.location is None
    rec = await sched.run({"id": "x", "action": "dance"})
    assert not rec["ok"]


async def test_trips_start_from_home_when_set(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*GYM)  # phone is at the gym...
    store.set_setting("home", {"lat": HOME[0], "lon": HOME[1], "name": "Home"})
    far = (GYM[0] + 0.001, GYM[1])
    res = await start_trip(ctl, [Stop(*far)], 360, follow_roads=False, variation=0)
    assert res["path"][0] == HOME  # ...but the trip starts at home
    await ctl.wait_route()
    sets = [c[1:] for c in dev.calls if c[0] == "set"]
    assert HOME in sets and dev.location == far


async def test_home_without_position_lets_travel_start(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()  # real GPS, never teleported
    store.set_setting("home", {"lat": HOME[0], "lon": HOME[1], "name": "Home"})
    await start_trip(ctl, [Stop(*GYM)], 360, follow_roads=False, variation=0)
    await ctl.wait_route()
    assert dev.calls[1] == ("set", *HOME) and dev.location == GYM


async def test_start_from_home_can_be_turned_off(tmp_path):
    ctl, _, store = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*GYM)
    store.set_setting("home", {"lat": HOME[0], "lon": HOME[1], "name": "Home"})
    store.set_setting("start_from_home", False)
    res = await start_trip(ctl, [Stop(*HOME)], 360, follow_roads=False, variation=0)
    assert res["path"][0] == GYM
    await ctl.stop_route()
