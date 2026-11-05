# PyInstaller recipe for aiGo.exe. Build with build_exe.bat (or:
#   .build-venv\Scripts\python -m PyInstaller packaging\aigo.spec --noconfirm).
# Produces dist\aiGo\aiGo.exe (a folder build: starts fast, fewer antivirus false alarms).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "aigo" / "static"), "aigo/static")]
binaries = []
hiddenimports = collect_submodules("aigo") + collect_submodules("uvicorn")

# pymobiledevice3 imports much of itself lazily and ships data files; pytun_pmd3 carries the wintun
# DLLs; pmd_pytcp is the no-admin userspace network stack; pykdebugparser ships a data table.
for pkg in ("pymobiledevice3", "pytun_pmd3", "pmd_pytcp", "pykdebugparser"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Packages that read their own installed metadata at import time (pyimg4 does
# `version(__package__)`; without its metadata the disk-image mounter fails to load).
for dist in ("pyimg4", "pymobiledevice3"):
    datas += copy_metadata(dist)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="aiGo",
    icon=str(ROOT / "packaging" / "aigo.ico"),
    console=True,  # the window shows status; closing it quits aiGo
)
coll = COLLECT(exe, a.binaries, a.datas, name="aiGo")
