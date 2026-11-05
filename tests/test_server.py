import pytest
from fastapi.testclient import TestClient

from aigo.controller import Controller
from aigo.device import FakeDevice
from aigo.server import create_app
from aigo.store import Store

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path):
    dev = FakeDevice()
    ctl = Controller(dev, Store(tmp_path / "data.json"), fake=True)
    app = create_app(ctl)
    with TestClient(app, base_url=BASE) as c:
        c.post("/api/connect")
        c.device = dev
        yield c


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "aiGo" in r.text


def test_status(client):
    s = client.get("/api/status").json()
    assert s["connection"] == "connected"
    assert s["fake"] is True


def test_teleport_and_clear(client):
    r = client.post("/api/teleport", json={"lat": 40.758, "lon": -73.9855})
    assert r.status_code == 200
    assert r.json()["status"]["position"]["lat"] == 40.758
    assert client.device.location == (40.758, -73.9855)
    r = client.post("/api/clear")
    assert r.json()["position"] is None


def test_teleport_validates(client):
    assert client.post("/api/teleport", json={"lat": 91, "lon": 0}).status_code == 422


def test_step_without_position_is_400(client):
    r = client.post("/api/step", json={"bearing": 0, "meters": 5})
    assert r.status_code == 400


def test_route_start_and_stop(client):
    client.post("/api/teleport", json={"lat": 40.758, "lon": -73.9855})
    r = client.post("/api/route", json={"points": [{"lat": 40.7484, "lon": -73.9857}], "speed_kmh": 5})
    assert r.status_code == 200
    assert r.json()["status"]["mode"] == "route"
    r = client.post("/api/route/stop")
    assert r.json()["mode"] == "holding"


def test_favorites_crud(client):
    fav = client.post("/api/favorites", json={"name": "Home-ish", "lat": 1, "lon": 2}).json()
    assert client.get("/api/favorites").json()[0]["name"] == "Home-ish"
    assert client.delete(f"/api/favorites/{fav['id']}").status_code == 200
    assert client.get("/api/favorites").json() == []
    assert client.delete("/api/favorites/nope").status_code == 404


def test_history_records_teleports(client):
    client.post("/api/teleport", json={"lat": 10, "lon": 20})
    assert client.get("/api/history").json()[0]["lat"] == 10


def test_websocket_sends_status(client):
    with client.websocket_connect("/ws", headers={"Host": "127.0.0.1:8765"}) as ws:
        first = ws.receive_json()
        assert first["connection"] == "connected"
        client.post("/api/teleport", json={"lat": 5, "lon": 6})
        msg = ws.receive_json()
        while msg["position"] is None:
            msg = ws.receive_json()
        assert msg["position"] == {"lat": 5, "lon": 6}


def test_route_accepts_variation_and_track_start(client):
    client.post("/api/teleport", json={"lat": 40.758, "lon": -73.9855})
    body = {
        "points": [{"lat": 40.75, "lon": -73.98}, {"lat": 40.7484, "lon": -73.9857}],
        "speed_kmh": 5,
        "variation": 0.15,
        "from_current": False,
    }
    r = client.post("/api/route", json=body)
    assert r.status_code == 200
    assert client.post("/api/route", json={**body, "variation": 0.9}).status_code == 422
    client.post("/api/route/stop")


# ----- trips, route controls, saved routes, schedules -----
from aigo.routing import RoutingError  # noqa: E402


async def _offline(url):
    raise RoutingError("Couldn't reach the road routing service.")


@pytest.fixture
def offline_client(tmp_path):
    dev = FakeDevice()
    ctl = Controller(dev, Store(tmp_path / "data.json"), fake=True)
    with TestClient(create_app(ctl, fetch_json=_offline), base_url=BASE) as c:
        c.post("/api/connect")
        c.post("/api/teleport", json={"lat": 40.758, "lon": -73.9855})
        c.device = dev
        yield c


GYM = {"lat": 40.7484, "lon": -73.9857}


def test_directions_preview_and_trip(offline_client):
    c = offline_client
    body = {"stops": [{**GYM, "wait_min": 10}], "speed_kmh": 5}
    d = c.post("/api/directions", json=body).json()
    assert d["points"][0] == [40.758, -73.9855] and d["dwell"] == [[1, 600.0]]
    assert "straight lines" in d["note"]
    r = c.post("/api/trip", json=body).json()
    assert r["status"]["mode"] == "route"
    assert r["eta_s"] > 600
    assert r["note"]
    c.post("/api/route/stop")


def test_route_pause_resume_speed(offline_client):
    c = offline_client
    assert c.post("/api/route/pause").status_code == 400  # nothing running
    c.post("/api/trip", json={"stops": [GYM], "speed_kmh": 5})
    assert c.post("/api/route/pause").json()["route"]["paused"] is True
    assert c.post("/api/route/resume").json()["route"]["paused"] is False
    assert c.post("/api/route/speed", json={"speed_kmh": 50}).json()["route"]["speed_kmh"] == pytest.approx(50)
    c.post("/api/route/stop")


def test_saved_routes_crud_and_play(offline_client):
    c = offline_client
    body = {"name": "Gym run", "stops": [GYM], "speed_kmh": 10, "follow_roads": False}
    r = c.post("/api/routes", json=body).json()
    assert c.get("/api/routes").json()[0]["name"] == "Gym run"
    assert c.put(f"/api/routes/{r['id']}", json={**body, "name": "Gym jog"}).json()["name"] == "Gym jog"
    played = c.post(f"/api/routes/{r['id']}/play").json()
    assert played["status"]["mode"] == "route" and played["note"] is None
    c.post("/api/route/stop")
    assert c.post("/api/routes", json={**body, "name": ""}).status_code == 422
    assert c.delete(f"/api/routes/{r['id']}").status_code == 200
    assert c.post(f"/api/routes/{r['id']}/play").status_code == 404


def test_schedules_crud_validation_and_run(offline_client):
    c = offline_client
    fav = c.post("/api/favorites", json={"name": "Gym", **GYM}).json()
    body = {"name": "Gym", "time": "09:00", "days": [0, 2, 4], "action": "teleport", "favorite_id": fav["id"]}
    e = c.post("/api/schedules", json=body).json()
    assert e["enabled"] is True and e["last_run"] is None
    assert c.post("/api/schedules", json={**body, "time": "25:00"}).status_code == 422
    assert c.post("/api/schedules", json={**body, "favorite_id": None}).status_code == 422
    assert c.post("/api/schedules", json={**body, "days": [7]}).status_code == 422
    off = c.put(f"/api/schedules/{e['id']}", json={**body, "enabled": False}).json()
    assert off["enabled"] is False
    run = c.post(f"/api/schedules/{e['id']}/run").json()
    assert run["ok"] is True
    assert c.device.location == (GYM["lat"], GYM["lon"])
    assert c.get("/api/schedules").json()[0]["last_run"]["ok"] is True
    assert c.delete(f"/api/schedules/{e['id']}").status_code == 200


def test_home_endpoints_and_preview(offline_client):
    c = offline_client
    assert c.get("/api/home").json() == {"home": None, "start_from_home": True}
    h = c.put("/api/home", json={"lat": 40.70, "lon": -74.0, "name": ""}).json()
    assert h["home"] == {"lat": 40.70, "lon": -74.0, "name": "Home"}
    d = c.post("/api/directions", json={"stops": [GYM], "speed_kmh": 5}).json()
    assert d["points"][0] == [40.70, -74.0]  # preview starts at home
    assert c.put("/api/home/settings", json={"start_from_home": False}).json()["start_from_home"] is False
    d = c.post("/api/directions", json={"stops": [GYM], "speed_kmh": 5}).json()
    assert d["points"][0] == [40.758, -73.9855]
    assert c.delete("/api/home").json()["home"] is None
