import threading

import uvicorn

from aigo.__main__ import already_running, parse_args
from aigo.controller import Controller
from aigo.device import FakeDevice
from aigo.server import create_app


def test_parse_args_defaults():
    a = parse_args([])
    assert a.port == 8765 and not a.fake and a.rsd is None


def test_already_running_false_on_free_port():
    assert already_running(1) is False


def test_already_running_detects_aigo(tmp_path):
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(Controller(FakeDevice(), None, fake=True), connect_on_start=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.05)
        assert already_running(port) is True
    finally:
        server.should_exit = True
        t.join(5)


def test_self_check_passes_in_dev_env(capsys):
    from aigo.__main__ import self_check

    assert self_check() is True
    assert "Self-check passed." in capsys.readouterr().out
