"""The three attacks found in the security review, each of which must now be refused."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aigo.controller import Controller
from aigo.device import FakeDevice
from aigo.security import host_allowed, origin_allowed
from aigo.server import create_app
from aigo.store import Store

BASE = "http://127.0.0.1:8765"
EVIL = "https://evil.example"
HOST = {"Host": "127.0.0.1:8765"}  # TestClient sends "testserver" for WebSockets unless told


@pytest.fixture
def client(tmp_path):
    ctl = Controller(FakeDevice(), Store(tmp_path / "d.json"), fake=True)
    with TestClient(create_app(ctl), base_url=BASE) as c:
        c.post("/api/connect")
        c.post("/api/teleport", json={"lat": 40.758, "lon": -73.9855})
        c.put("/api/home", json={"lat": 40.7, "lon": -74.0, "name": "Home"})
        c.ctl = ctl
        yield c


def test_dns_rebinding_foreign_host_is_refused(client):
    r = client.get("/api/home", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403
    assert "40.7" not in r.text  # the home address doesn't leak


@pytest.mark.parametrize("path", ["/api/clear", "/api/connect", "/api/route/stop", "/api/route/pause"])
def test_cross_site_post_is_refused(client, path):
    r = client.post(path, headers={"Origin": EVIL})
    assert r.status_code == 403
    assert client.ctl.position is not None  # spoofing was not stopped


def test_cross_site_put_and_delete_are_refused(client):
    assert client.put("/api/home", json={"lat": 1, "lon": 2}, headers={"Origin": EVIL}).status_code == 403
    assert client.delete("/api/home", headers={"Origin": EVIL}).status_code == 403
    assert client.post("/api/teleport", json={"lat": 1, "lon": 2}, headers={"Origin": "null"}).status_code == 403


def test_cross_site_websocket_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={**HOST, "Origin": EVIL}) as ws:
            ws.receive_json()


def test_own_page_still_works(client):
    own = {"Origin": BASE}
    assert client.post("/api/clear", headers=own).status_code == 200
    assert client.post("/api/teleport", json={"lat": 1, "lon": 2},
                       headers={"Origin": "http://localhost:8765"}).status_code == 200
    with client.websocket_connect("/ws", headers={**HOST, **own}) as ws:
        assert ws.receive_json()["connection"] == "connected"
    assert client.get("/").status_code == 200


def test_same_host_different_port_is_refused(client):
    # Another local web app on a different port is a different site.
    assert client.post("/api/clear", headers={"Origin": "http://127.0.0.1:3000"}).status_code == 403


def test_helpers():
    assert host_allowed("127.0.0.1:8765") and host_allowed("localhost:8765") and host_allowed("[::1]:8765")
    assert not host_allowed("evil.example:8765") and not host_allowed("") and not host_allowed(None)
    assert origin_allowed(None, "127.0.0.1:8765")
    assert origin_allowed("http://127.0.0.1:8765", "127.0.0.1:8765")
    assert origin_allowed("http://[::1]:8765", "[::1]:8765")
    assert not origin_allowed("https://127.0.0.1:8765", "127.0.0.1:8765")
    assert not origin_allowed("http://127.0.0.1.evil.example:8765", "127.0.0.1:8765")
    assert not origin_allowed("null", "127.0.0.1:8765")
