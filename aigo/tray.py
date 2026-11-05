"""System tray mode (Windows): aiGo runs in the background with a tray icon instead of a console.

Menu: Open aiGo (also a double-click), Stop spoofing, Quit aiGo. Quitting shuts the server down
cleanly, which returns the phone to its real location.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

log = logging.getLogger(__name__)
ICON_PATH = Path(__file__).parent / "static" / "icon.png"


def _post(url: str) -> None:
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).close()


def run_tray(server: uvicorn.Server, url: str, open_browser: bool = True) -> None:
    import pystray  # imported here: only needed (and only installed) on Windows
    from PIL import Image

    thread = threading.Thread(target=server.run, name="aigo-server", daemon=True)
    thread.start()

    def open_page(icon=None, item=None) -> None:
        webbrowser.open(url)

    def stop_spoofing(icon, item) -> None:
        try:
            _post(f"{url}api/clear")
            icon.notify("Your phone is back on its real GPS.", "aiGo")
        except OSError as exc:
            log.warning("stop spoofing from tray failed: %s", exc)
            icon.notify("Couldn't reach aiGo to stop spoofing.", "aiGo")

    def quit_app(icon, item) -> None:
        server.should_exit = True  # graceful shutdown clears the simulated location
        thread.join(timeout=15)
        icon.stop()

    icon = pystray.Icon(
        "aiGo",
        Image.open(ICON_PATH),
        "aiGo",
        menu=pystray.Menu(
            pystray.MenuItem("Open aiGo", open_page, default=True),
            pystray.MenuItem("Stop spoofing", stop_spoofing),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit aiGo", quit_app),
        ),
    )

    def on_ready(icon) -> None:
        icon.visible = True
        icon.notify("aiGo is running. Use the tray icon to open it or quit.", "aiGo")
        if open_browser:
            threading.Timer(1.0, open_page).start()

    icon.run(setup=on_ready)
    # The icon loop ended (Quit, or Windows logging off): make sure the server is down too.
    server.should_exit = True
    thread.join(timeout=15)
