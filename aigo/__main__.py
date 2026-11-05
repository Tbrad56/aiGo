"""Run aiGo: `python -m aigo` (real iPhone) or `python -m aigo --fake` (pretend phone)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import urllib.request
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
    p.add_argument("--self-check", action="store_true", help="check that every phone component loads, then exit")
    return p.parse_args(argv)


def self_check() -> bool:
    """Import everything the real-phone path loads lazily (catches gaps in a packaged build)."""
    from aigo.device import REAL_DEVICE_MODULES

    import importlib

    ok = True
    for name in REAL_DEVICE_MODULES:
        try:
            importlib.import_module(name)
            print(f"  ok      {name}")
        except Exception as exc:  # report every failure, not just the first
            ok = False
            print(f"  FAILED  {name}: {type(exc).__name__}: {exc}")
    print("Self-check passed." if ok else "Self-check FAILED.")
    return ok


def already_running(port: int) -> bool:
    """True if an aiGo server already answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.5) as res:
            return "connection" in json.load(res)
    except (OSError, ValueError):
        return False


def main(argv=None) -> None:
    try:
        run(argv)
    except Exception:
        if getattr(sys, "frozen", False):
            # Double-clicked exe: keep the window open so the error can be read.
            import traceback

            traceback.print_exc()
            input("\naiGo hit an error (details above). Press Enter to close this window...")
        raise


def run(argv=None) -> None:
    args = parse_args(argv)
    if args.self_check:
        raise SystemExit(0 if self_check() else 1)
    url = f"http://127.0.0.1:{args.port}/"
    if already_running(args.port):
        print(f"aiGo is already running; opening {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

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

    print(f"\n  aiGo is running at {url}\n  Press Ctrl+C to stop (your phone returns to its real location).\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # The graceful-shutdown timeout guarantees Ctrl+C reaches the cleanup that clears the location.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
