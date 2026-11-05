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


async def test_route_speed_variation_changes_step_sizes(tmp_path):
    import random

    ctl, dev, _ = make(tmp_path)
    ctl.rng = random.Random(42)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.start_route([EMPIRE_STATE], speed_kmh=36, variation=0.2)  # base 10 m per tick
    await ctl.wait_route()
    sets = [c[1:] for c in dev.calls if c[0] == "set"]
    steps = [distance_m(a, b) for a, b in zip(sets, sets[1:])][:-1]  # last step is a remainder
    assert dev.location == EMPIRE_STATE
    assert min(steps) >= 8 - 1e-6 and max(steps) <= 12 + 1e-6
    assert max(steps) - min(steps) > 1  # actually varies


async def test_route_rejects_bad_variation(tmp_path):
    ctl, _, _ = make(tmp_path)
    await ctl.connect()
    with pytest.raises(ValueError):
        await ctl.start_route([TIMES_SQUARE, EMPIRE_STATE], speed_kmh=5, variation=0.9)


async def test_route_from_track_start_jumps_there_first(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*LONDON)
    await ctl.start_route([TIMES_SQUARE, EMPIRE_STATE], speed_kmh=360, from_current=False)
    await ctl.wait_route()
    sets = [c[1:] for c in dev.calls if c[0] == "set"]
    assert sets[1] == TIMES_SQUARE  # jumped to the track start, not walked from London
    assert dev.location == EMPIRE_STATE


async def test_keepalive_resends_held_position(tmp_path):
    ctl, dev, _ = make(tmp_path)
    ctl.keepalive_interval = 0.02
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    task = ctl.start_keepalive()
    await asyncio.sleep(0.15)
    task.cancel()
    sets = [c for c in dev.calls if c[0] == "set"]
    assert len(sets) >= 3  # teleport + several keepalive re-sends
    assert all(c[1:] == TIMES_SQUARE for c in sets)


async def test_keepalive_reconnects_after_drop_and_restores_location(tmp_path):
    ctl, dev, _ = make(tmp_path)
    ctl.keepalive_interval = 0.02
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    task = ctl.start_keepalive()

    # Phone drops off Wi-Fi and can't be reached for a while.
    dev.connected = False
    dev.location = None
    dev.fail_connect = "No iPhone found."
    await asyncio.sleep(0.1)
    assert ctl.connection == "error"
    assert "retrying" in ctl.error.lower()

    # Phone comes back: keepalive reconnects and puts the fake location back.
    dev.fail_connect = None
    await asyncio.sleep(0.1)
    task.cancel()
    assert ctl.connection == "connected"
    assert ctl.error is None
    assert dev.location == TIMES_SQUARE


async def test_keepalive_idle_does_nothing(tmp_path):
    ctl, dev, _ = make(tmp_path)
    ctl.keepalive_interval = 0.02
    await ctl.connect()
    task = ctl.start_keepalive()
    await asyncio.sleep(0.1)
    task.cancel()
    assert not [c for c in dev.calls if c[0] == "set"]


async def test_status_includes_transport(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    assert ctl.status()["device"]["transport"] == "pretend"


def test_split_legs():
    from aigo.controller import _split_legs

    pts = [(0.0, float(i)) for i in range(5)]
    legs = _split_legs(pts, {2: 60, 0: 99, 9: 5})  # start and out-of-range indices are ignored
    assert legs == [(pts[0:3], 60.0), (pts[2:5], 0.0)]
    assert _split_legs(pts, {4: 30}) == [(pts, 30.0)]
    assert _split_legs(pts, {}) == [(pts, 0.0)]


async def test_route_waits_at_stop(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    seen_waiting = []
    q = ctl.subscribe()

    async def watch():
        while True:
            r = (await q.get())["route"]
            if r and r["waiting_s"] > 0:
                seen_waiting.append((r["waiting_s"], r["leg"]))

    watcher = asyncio.create_task(watch())
    # index 0 = EMPIRE_STATE in the given points; wait 3 s there, then continue
    far = destination(EMPIRE_STATE, 180, 50)
    info = await ctl.start_route([EMPIRE_STATE, far], speed_kmh=360, dwell={0: 3})
    assert info["stops"] == 2
    assert info["eta_s"] == pytest.approx(info["total_m"] / 100 + 3)
    await ctl.wait_route()
    await asyncio.sleep(0)
    watcher.cancel()
    assert dev.location == far
    assert seen_waiting and seen_waiting[0] == (3, 1)
    assert min(w for w, _ in seen_waiting) == 1  # counted down 3, 2, 1


async def test_pause_and_resume_route(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.start_route([destination(TIMES_SQUARE, 0, 100_000)], speed_kmh=36)
    for _ in range(5):
        await asyncio.sleep(0)
    await ctl.pause_route()
    assert ctl.status()["route"]["paused"] is True
    for _ in range(3):
        await asyncio.sleep(0)
    frozen = dev.location
    for _ in range(20):
        await asyncio.sleep(0)
    assert dev.location == frozen  # no movement while paused
    await ctl.resume_route()
    assert ctl.status()["route"]["paused"] is False
    for _ in range(20):
        await asyncio.sleep(0)
    assert dev.location != frozen
    await ctl.stop_route()


async def test_change_speed_mid_route(tmp_path):
    ctl, dev, _ = make(tmp_path)
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.start_route([destination(TIMES_SQUARE, 90, 100_000)], speed_kmh=36)  # 10 m/tick
    for _ in range(10):
        await asyncio.sleep(0)
    await ctl.set_route_speed(72)  # 20 m/tick
    assert ctl.status()["route"]["speed_kmh"] == pytest.approx(72)
    for _ in range(10):
        await asyncio.sleep(0)
    sets = [c[1:] for c in dev.calls if c[0] == "set"]
    last_step = distance_m(sets[-2], sets[-1])
    assert last_step == pytest.approx(20, rel=0.01)
    await ctl.stop_route()


async def test_route_controls_need_a_route(tmp_path):
    ctl, _, _ = make(tmp_path)
    await ctl.connect()
    with pytest.raises(ValueError):
        await ctl.pause_route()
    with pytest.raises(ValueError):
        await ctl.set_route_speed(10)


async def test_keepalive_covers_paused_route(tmp_path):
    ctl, dev, _ = make(tmp_path)
    ctl.keepalive_interval = 0.02
    await ctl.connect()
    await ctl.teleport(*TIMES_SQUARE)
    await ctl.start_route([destination(TIMES_SQUARE, 0, 100_000)], speed_kmh=5)
    await asyncio.sleep(0)
    await ctl.pause_route()
    n = len([c for c in dev.calls if c[0] == "set"])
    task = ctl.start_keepalive()
    await asyncio.sleep(0.12)
    task.cancel()
    assert len([c for c in dev.calls if c[0] == "set"]) > n  # held position re-sent while paused
    await ctl.stop_route()
