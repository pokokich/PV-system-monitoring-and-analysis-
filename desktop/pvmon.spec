# PyInstaller spec for the desktop app: pyinstaller desktop/pvmon.spec (from the repository root)
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "web" / "templates"), "web/templates"), (str(ROOT / "web" / "static"), "web/static"),
         (str(ROOT / "migrations"), "migrations")] + collect_data_files("certifi")
hidden = collect_submodules("uvicorn") + collect_submodules("web") + collect_submodules("collector") + ["pystray"] + collect_submodules("tzdata") + ["certifi"]

a = Analysis([str(ROOT / "desktop" / "launch.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PV Monitor", console=False,
          icon=str(ROOT / "desktop" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")))
coll = COLLECT(exe, a.binaries, a.datas, name="PV Monitor")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="PV Monitor.app", icon=str(ROOT / "desktop" / "icon.icns"), bundle_identifier="com.pvmonitor.app",
                 info_plist={"LSUIElement": True, "CFBundleShortVersionString": "0.1.4"})
