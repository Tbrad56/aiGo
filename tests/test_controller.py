import asyncio

import pytest

from aigo.controller import Controller
from aigo.device import DeviceError, FakeDevice
from aigo.geo import destination, distance_m
from aigo.store import Store

TIMES_SQUARE = (40.7580, -73.9855)
EMPIRE_STATE = (40.7484, -73.9857)
LONDON = (51.5072, -0.1276)


async def no_sleep(_):
    await asyncio.sleep(0)


def make(tmp_path, **kw):
    dev = FakeDevice(**kw)
    store = Store(tmp_path / "data.json")
    ctl = Controller(dev, store, interval=1.0, sleep=no_sleep, fake=True)
    return ctl, dev, store


async def test_connect_and_teleport(tmp_path):
    ctl, dev, store = make(tmp_path)
    await ctl.connect()
    assert ctl.connection == "connected"
    result = await ctl.teleport(*TIMES_SQUARE)
    assert result["warning"] is None
    assert dev.location == TIMES_SQUARE
    assert ctl.status()["position"] == {"lat": TIMES_SQUARE[0], "lon": TIMES_SQUARE[1]}
    assert store.history()[0]["lat"] == TIMES_SQUARE[0]


async def test_connect_failure_sets_error(tmp_path):
    ctl, _, _ = make(tmp_path, fail_connect="No iPhone found.")
    with pytest.raises(DeviceError):
        await ctl.connect()
    assert ctl.connection == "error"
    assert ctl.error == "No iPhone found."


async def test_big_quick_jump_warns(tmp_path):
    ctl, _, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    result = await ctl.teleport(*LONDON)
    assert result["warning"] and "km jump" in result["warning"]


async def test_clear_resets(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.clear()
    assert dev.location is None
    assert ctl.position is None
    assert ctl.mode == "idle"


async def test_reconnects_once_when_set_fails(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    dev.fail_next_set = 1
    await ctl.teleport(*TIMES_SQUARE)
    assert dev.location == TIMES_SQUARE
    assert [c[0] for c in dev.calls].count("connect") == 2
    assert ctl.connection == "connected"


async def test_joystick_requires_position(tmp_path):
    ctl, _, _ = make(tmp_path)
    await ctl.connect()
    with pytest.raises(ValueError):
        await ctl.step(0, 10)


async def test_joystick_moves(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.step(90, 50)
    assert distance_m(TIMES_SQUARE, dev.location) == pytest.approx(50, rel=1e-3)


async def test_route_walks_to_end(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    info = await ctl.start_route([EMPIRE_STATE], speed_kmh=36)  # 10 m per 1 s tick
    assert info["total_m"] == pytest.approx(1070, rel=0.02)
    await ctl.wait_route()
    assert dev.location == EMPIRE_STATE
    sets = [c for c in dev.calls if c[0] == "set"]
    assert len(sets) > 100  # ~107 steps of 10 m
    assert ctl.mode == "holding"
    assert ctl.route_progress is None


async def test_route_can_be_stopped(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    far = destination(TIMES_SQUARE, 0, 100_000)
    await ctl.start_route([far], speed_kmh=5)
    for _ in range(5):
        await asyncio.sleep(0)
    await ctl.stop_route()
    assert ctl.mode == "holding"
    assert dev.location != far


async def test_route_without_position_starts_at_first_point(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.start_route([TIMES_SQUARE, EMPIRE_STATE], speed_kmh=360)
    await ctl.wait_route()
    assert dev.calls[1] == ("set", *TIMES_SQUARE)
    assert dev.location == EMPIRE_STATE


async def test_route_rejects_bad_input(tmp_path):
    ctl, _, _ = make(tmp_path)
    await ctl.connect()
    with pytest.raises(ValueError):
        await ctl.start_route([TIMES_SQUARE], speed_kmh=5)
    with pytest.raises(ValueError):
        await ctl.start_route([TIMES_SQUARE, EMPIRE_STATE], speed_kmh=0)


async def test_subscribers_get_updates(tmp_path):
    ctl, _, _ = make(tmp_path)
    q = ctl.subscribe()
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    seen = []
    while not q.empty():
        seen.append(q.get_nowait())
    assert seen[-1]["position"]["lat"] == TIMES_SQUARE[0]
    ctl.unsubscribe(q)


async def test_shutdown_clears_and_closes(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.shutdown()
    names = [c[0] for c in dev.calls]
    assert names[-2:] == ["clear", "close"]
