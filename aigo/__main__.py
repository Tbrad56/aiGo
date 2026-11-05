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

log = logging.getLogger("aigo")


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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tray", dest="tray", action="store_true", default=None, help="run from a system tray icon")
    mode.add_argument("--console", dest="tray", action="store_false", help="run in this console window")
    args = p.parse_args(argv)
    if args.tray is None:  # default: tray for the packaged Windows app, console otherwise
        args.tray = bool(getattr(sys, "frozen", False)) and sys.platform == "win32"
    return args


LOG_MAX_BYTES = 2_000_000


def log_path():
    return Store().path.parent / "aigo.log"


def ensure_output() -> None:
    """A windowed exe has no console (sys.stdout is None): send all output to a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if sys.platform == "win32":
        import ctypes

        # Started from a terminal (e.g. `aiGo.exe --self-check`): print there.
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            out = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stdout = sys.stdout or out
            sys.stderr = sys.stderr or out
            return
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.unlink()
    f = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


def show_error(text: str) -> None:
    """Report a startup failure where the user can see it."""
    if getattr(sys, "frozen", False) and sys.platform == "win32" and not sys.stdin:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "aiGo couldn't start", 0x10)
    elif getattr(sys, "frozen", False):
        input("\naiGo hit an error (details above). Press Enter to close this window...")


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
    ensure_output()
    try:
        run(argv)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        if getattr(sys, "frozen", False):
            show_error(f"{type(exc).__name__}: {exc}\n\nDetails are in {log_path()}")
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

    # The graceful-shutdown timeout guarantees quitting reaches the cleanup that clears the location.
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", timeout_graceful_shutdown=3)
    if args.tray:
        from aigo.tray import run_tray

        log.info("aiGo running at %s (tray mode)", url)
        run_tray(uvicorn.Server(config), url, open_browser=not args.no_browser)
        return

    print(f"\n  aiGo is running at {url}\n  Press Ctrl+C to stop (your phone returns to its real location).\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
