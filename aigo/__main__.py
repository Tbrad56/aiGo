"""Run aiGo: `python -m aigo` (real iPhone) or `python -m aigo --fake` (pretend phone)."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from aigo.controller import Controller
from aigo.device import FakeDevice, RealDevice
from aigo.server import create_app
from aigo.store import Store


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="aigo", description="Set your iPhone's location from a map.")
    p.add_argument("--fake", action="store_true", help="use a pretend phone (no iPhone needed)")
    p.add_argument("--udid", help="pick a specific iPhone when several are plugged in")
    p.add_argument(
        "--rsd",
        nargs=2,
        metavar=("HOST", "PORT"),
        help="use a tunnel started separately with `pymobiledevice3 lockdown start-tunnel`",
    )
    p.add_argument("--no-mount", action="store_true", help="skip mounting the developer disk image")
    p.add_argument("--port", type=int, default=8765, help="local web port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    p.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("pymobiledevice3").setLevel(logging.WARNING)

    if args.fake:
        device = FakeDevice()
    else:
        rsd = (args.rsd[0], int(args.rsd[1])) if args.rsd else None
        device = RealDevice(udid=args.udid, rsd=rsd, mount=not args.no_mount)

    controller = Controller(device, Store(), fake=args.fake)
    app = create_app(controller)

    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  aiGo is running at {url}\n  Press Ctrl+C to stop (your phone returns to its real location).\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # The graceful-shutdown timeout guarantees Ctrl+C reaches the cleanup that clears the location.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
