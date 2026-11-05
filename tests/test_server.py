import pytest
from fastapi.testclient import TestClient

from aigo.controller import Controller
from aigo.device import FakeDevice
from aigo.server import create_app
from aigo.store import Store


@pytest.fixture
def client(tmp_path):
    dev = FakeDevice()
    ctl = Controller(dev, Store(tmp_path / "data.json"), fake=True)
    app = create_app(ctl)
    with TestClient(app) as c:
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
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["connection"] == "connected"
        client.post("/api/teleport", json={"lat": 5, "lon": 6})
        msg = ws.receive_json()
        while msg["position"] is None:
            msg = ws.receive_json()
        assert msg["position"] == {"lat": 5, "lon": 6}
