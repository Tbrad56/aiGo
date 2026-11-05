"""Device backends: the real iPhone (via pymobiledevice3) and a fake one for development.

Only this module imports pymobiledevice3, so an iOS or library change is fixed here.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol

log = logging.getLogger(__name__)


# Modules RealDevice imports lazily. Kept in one list so `--self-check` can verify a packaged build.
REAL_DEVICE_MODULES = (
    "pymobiledevice3.exceptions",
    "pymobiledevice3.remote.remote_service_discovery",
    "pymobiledevice3.remote.remotexpc",
    "pymobiledevice3.remote.userspace_tunnel",
    "pymobiledevice3.services.mobile_image_mounter",
    "pymobiledevice3.services.dvt.instruments.dvt_provider",
    "pymobiledevice3.services.dvt.instruments.location_simulation",
    "pmd_pytcp",
)


class DeviceError(Exception):
    """A problem talking to the phone, with a message fit to show the user."""


@dataclass
class DeviceInfo:
    name: str = ""
    ios: str = ""
    model: str = ""
    udid: str = ""


class LocationDevice(Protocol):
    info: DeviceInfo

    async def connect(self) -> None: ...

    async def set_location(self, lat: float, lon: float) -> None: ...

    async def clear_location(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class FakeDevice:
    """Pretend phone: records every call. Used for tests and `--fake` mode."""

    info: DeviceInfo = field(default_factory=lambda: DeviceInfo("Pretend iPhone", "26.1", "iPhone15,2", "FAKE"))
    fail_connect: Optional[str] = None
    fail_next_set: int = 0  # number of upcoming set_location calls that should fail
    connected: bool = False
    location: Optional[tuple[float, float]] = None
    calls: list[tuple] = field(default_factory=list)

    async def connect(self) -> None:
        self.calls.append(("connect",))
        if self.fail_connect:
            raise DeviceError(self.fail_connect)
        self.connected = True

    async def set_location(self, lat: float, lon: float) -> None:
        self.calls.append(("set", lat, lon))
        if not self.connected:
            raise DeviceError("Not connected")
        if self.fail_next_set > 0:
            self.fail_next_set -= 1
            self.connected = False
            raise DeviceError("Connection lost")
        self.location = (lat, lon)

    async def clear_location(self) -> None:
        self.calls.append(("clear",))
        if not self.connected:
            raise DeviceError("Not connected")
        self.location = None

    async def close(self) -> None:
        self.calls.append(("close",))
        self.connected = False


def _friendly(exc: BaseException) -> str:
    """Turn pymobiledevice3 exceptions into plain instructions."""
    name = type(exc).__name__
    hints = {
        "NoDeviceConnectedError": "No iPhone found. Plug it in over USB, unlock it, and tap Trust if asked.",
        "DeviceNotFoundError": "That iPhone isn't connected. Check the cable and that the phone is unlocked.",
        "DeveloperModeIsNotEnabledError": "Developer Mode is off. Turn it on in Settings > Privacy & Security.",
        "PasswordRequiredError": "Unlock your iPhone and try again.",
        "PairingDialogResponsePendingError": "Tap Trust on your iPhone, then try again.",
        "UserDeniedPairingError": "The iPhone refused to trust this PC. Unplug, replug and tap Trust.",
        "ConnectionTerminatedError": "The connection to the iPhone dropped. Keep it unlocked and on USB.",
        "ConnectionFailedToUsbmuxdError": (
            "Apple's device service isn't running. Open the Apple Devices app, or run "
            "`Get-Service *apple* | Restart-Service` in an admin PowerShell."
        ),
    }
    if name in hints:
        return hints[name]
    text = str(exc).strip()
    return f"{name}: {text}" if text else name


class RealDevice:
    """An iPhone on iOS 17.4+ reached through pymobiledevice3.

    By default the tunnel is opened in-process (no admin rights). Pass `rsd=(host, port)` to use a
    tunnel started separately with `pymobiledevice3 lockdown start-tunnel`.

    The DVT location channel stays open for the life of the connection, because iOS drops the
    simulated location when that session closes.
    """

    def __init__(self, udid: Optional[str] = None, rsd: Optional[tuple[str, int]] = None, mount: bool = True):
        self.udid = udid
        self.rsd_address = rsd
        self.mount = mount
        self.info = DeviceInfo()
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._location_sim = None

    async def connect(self) -> None:
        await self.close()
        stack = contextlib.AsyncExitStack()
        try:
            rsd = await self._open_rsd(stack)
            self.info = DeviceInfo(
                name=await _safe_value(rsd, "DeviceName"),
                ios=getattr(rsd, "product_version", "") or "",
                model=getattr(rsd, "product_type", "") or "",
                udid=getattr(rsd, "udid", "") or "",
            )
            if self.mount:
                await self._auto_mount(rsd)

            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

            dvt = await stack.enter_async_context(DvtProvider(rsd))
            self._location_sim = await stack.enter_async_context(LocationSimulation(dvt))
        except DeviceError:
            await stack.aclose()
            raise
        except Exception as exc:  # pymobiledevice3 raises many exception types
            log.error("connect failed: %r", exc)
            log.debug("connect failure details", exc_info=True)
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise DeviceError(_friendly(exc)) from exc
        self._stack = stack
        log.info("connected to %s (iOS %s)", self.info.name or self.info.udid, self.info.ios)

    async def _open_rsd(self, stack: contextlib.AsyncExitStack):
        if self.rsd_address is not None:
            from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
            from pymobiledevice3.remote.remotexpc import rsd_handshake_uuid

            host, port = self.rsd_address
            rsd = RemoteServiceDiscoveryService((host, port), handshake_uuid=rsd_handshake_uuid(host))
            await rsd.connect()
            stack.push_async_callback(rsd.close)
            return rsd

        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

        tunnel = UserspaceRsdTunnel(serial=self.udid)
        rsd = await tunnel.aopen()
        stack.push_async_callback(tunnel.aclose)
        return rsd

    @staticmethod
    async def _auto_mount(rsd) -> None:
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import auto_mount

        try:
            await auto_mount(rsd)
            log.info("developer disk image mounted")
        except AlreadyMountedError:
            log.info("developer disk image already mounted")

    async def set_location(self, lat: float, lon: float) -> None:
        if self._location_sim is None:
            raise DeviceError("Not connected to an iPhone")
        try:
            await self._location_sim.set(lat, lon)
        except Exception as exc:
            raise DeviceError(_friendly(exc)) from exc

    async def clear_location(self) -> None:
        if self._location_sim is None:
            raise DeviceError("Not connected to an iPhone")
        try:
            await self._location_sim.clear()
        except Exception as exc:
            raise DeviceError(_friendly(exc)) from exc

    async def close(self) -> None:
        self._location_sim = None
        if self._stack is not None:
            stack, self._stack = self._stack, None
            with contextlib.suppress(Exception):
                await stack.aclose()


async def _safe_value(rsd, key: str) -> str:
    with contextlib.suppress(Exception):
        value = await rsd.get_value(key=key)
        if isinstance(value, str):
            return value
    return ""
