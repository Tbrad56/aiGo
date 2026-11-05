# aiGo

Personal iPhone location simulator. Click a point on a map and your phone reports that location to
every app (Find My, Life360, Maps). Built on Apple's developer location-simulation service through
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

## Requirements

- Windows with Python 3.12 (`py -3.12`) and the Apple Devices app (USB driver)
- iPhone on iOS 17.4 or later, trusted by this PC, with Developer Mode on
- Internet for the first run (the developer disk image is signed by Apple)

## Install

From this folder, in PowerShell:

```
py -3.12 -m pip install -e .
```

## Run

Plug in the iPhone, unlock it, then double-click `run_aigo.bat` or run:

```
py -3.12 -m aigo
```

Your browser opens at http://127.0.0.1:8765. No admin rights are needed: aiGo opens the connection
to the phone itself. Press Ctrl+C in the terminal to quit; the phone goes back to its real location.

| Option | What it does |
| --- | --- |
| `--fake` | Pretend phone, no iPhone needed (for trying the UI) |
| `--rsd HOST PORT` | Fallback only: use a tunnel from `py -3.12 -m pymobiledevice3 lockdown start-tunnel` (run as admin) instead of the built-in one |
| `--udid UDID` | Choose a phone when several are plugged in |
| `--no-mount` | Skip mounting the developer disk image |
| `--port N` | Use another local port |
| `--no-browser` | Don't open the browser |
| `-v` | Debug logs |

If the built-in connection ever fails, use the admin tunnel route: start `lockdown start-tunnel` in an
Administrator PowerShell, then run `py -3.12 -m aigo --rsd <address> <port>` with the address and port it prints.

## Using it

- **Teleport:** click the map (or search a place, or paste `lat, lon`) and choose *Teleport here*.
- **Travel here:** moves from the current spot at the chosen speed instead of jumping.
- **Route:** on the Route tab, click stops on the map, pick a speed, and press *Start route*.
  - *Follow roads* plans along real streets for the chosen speed (walk, bike or drive). It also
    applies to *Travel here*.
  - *Vary speed slightly* changes each one-second step by up to ±15% so the pace isn't robotic.
  - *Import GPX file* replays a recorded track, starting at its first point.
- **Joystick:** on the Joystick tab, hold the arrow keys or W A S D.
- **Stop spoofing:** returns the phone to its real GPS.

Favorites and history are saved in `%APPDATA%\aiGo\data.json`.

Online services: place search uses [Nominatim](https://nominatim.openstreetmap.org/) and *Follow
roads* uses the [FOSSGIS OSRM](https://routing.openstreetmap.de/) router, so the search text and
route points you choose are sent to them. Map tiles come from OpenStreetMap. Nothing else leaves
your PC.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Phone only shows as `Network` in `usbmux list` | Admin PowerShell: `Get-Service *apple* \| Restart-Service` |
| "Apple's device service isn't running" | Same as above, or open the Apple Devices app |
| "Developer Mode is off" | `py -3.12 -m pymobiledevice3 amfi reveal-developer-mode`, then turn it on in Settings > Privacy & Security |
| `No module named pymobiledevice3` | You ran a different Python; always use `py -3.12` |

## Development

```
py -3.12 -m pip install -e ".[dev]"
py -3.12 -m pytest
```

Layout: `aigo/device.py` (the only code that talks to the phone), `aigo/controller.py` (teleport,
routes, joystick, reconnects), `aigo/server.py` (local API + WebSocket), `aigo/static/` (map UI).
