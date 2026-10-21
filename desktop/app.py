"""Desktop app: the collector, the guard and the dashboard in one process on the owner's computer.

    python -m desktop.app            # or the packaged "PV Monitor" app (desktop/pvmon.spec)

- data in the user's application folder (macOS ~/Library/Application Support/PV Monitor,
  Windows %APPDATA%\\PV Monitor, Linux ~/.local/share/pv-monitor), override with PVMON_HOME;
- the dashboard on http://127.0.0.1:8088, or the next free port up to 8099 when something else
  (another dashboard, a Docker container) answers there; PVMON_PORT fixes the port. Opened in the
  browser at start; only this computer can reach it unless PVMON_HOST=0.0.0.0 is set;
- an icon in the menu bar / system tray (pystray, optional) with Open, Start at login and Quit;
- while the computer is off or asleep nothing is collected; on the next start the collector
  fills the gap from Deye's history (5-minute data ~90 days back, daily totals further).

A second start only opens the browser on the running instance: it is recognised by the id in
<data folder>/instance, which its /healthz returns, so another service on the same port is never
mistaken for it.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "PV Monitor"
log = logging.getLogger("desktop")


def home() -> Path:
    if os.environ.get("PVMON_HOME"):
        return Path(os.environ["PVMON_HOME"]).expanduser()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pv-monitor"


def configure() -> tuple[str, int | None, Path]:
    root = home()
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DB_PATH", str(root / "data" / "pv.sqlite"))
    os.environ.setdefault("BACKUP_PATH", str(root / "backups"))
    host = os.environ.get("PVMON_HOST", "127.0.0.1")
    port = int(os.environ["PVMON_PORT"]) if os.environ.get("PVMON_PORT") else None
    # an optional .env in the app folder, for the same settings as the Docker setup
    from collector.deye_client import load_env
    load_env(root / ".env")
    handler = logging.handlers.RotatingFileHandler(root / "pvmon.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[handler, logging.StreamHandler()])
    return host, port, root


def running(port: int, instance: str) -> bool:
    """True when this app's own instance answers on the port (not just anything)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
            return resp.status < 500 and json.load(resp).get("instance") == instance
    except Exception:
        return False


def in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def previous(root: Path) -> tuple[int, str] | None:
    try:
        port, instance = (root / "instance").read_text().split()
        return int(port), instance
    except (OSError, ValueError):
        return None


def free_port(first: int = 8088, last: int = 8099) -> int:
    for port in range(first, last + 1):
        if not in_use(port):
            return port
    raise SystemExit(f"no free port in {first}-{last}; set PVMON_PORT")


def start_web(host: str, port: int) -> threading.Thread:
    import uvicorn

    from web.app import app
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, name="web", daemon=True)
    t.start()
    return t


def start_collector(stop: threading.Event) -> threading.Thread:
    from collector.main import run_forever
    t = threading.Thread(target=run_forever, args=(stop,), name="collector", daemon=True)
    t.start()
    return t


def start_guard(stop: threading.Event) -> threading.Thread:
    def run():
        from collector.guard import Guard
        while not stop.is_set():
            try:
                Guard().run(stop)
            except Exception:
                log.exception("guard stopped; starting again in a minute")
                stop.wait(60)
    t = threading.Thread(target=run, name="guard", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- start at login

def _exe() -> list[str]:
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "desktop.app"]


def autostart_path() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.pvmonitor.app.plist"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "PV Monitor.bat"
    if system == "Linux":
        return Path.home() / ".config" / "autostart" / "pv-monitor.desktop"
    return None


def autostart_enabled() -> bool:
    p = autostart_path()
    return bool(p and p.exists())


def set_autostart(on: bool) -> None:
    p = autostart_path()
    if p is None:
        return
    if not on:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    cmd = _exe()
    system = platform.system()
    if system == "Darwin":
        args = "".join(f"<string>{c}</string>" for c in cmd)
        p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.pvmonitor.app</string>
<key>ProgramArguments</key><array>{args}</array>
<key>RunAtLoad</key><true/>
<key>EnvironmentVariables</key><dict><key>PVMON_NO_BROWSER</key><string>1</string></dict>
</dict></plist>
""", encoding="utf-8")
    elif system == "Windows":
        line = " ".join(f'"{c}"' for c in cmd)
        p.write_text(f'@echo off\r\nset PVMON_NO_BROWSER=1\r\nstart "" {line}\r\n', encoding="utf-8")
    else:
        p.write_text("[Desktop Entry]\nType=Application\nName=PV Monitor\n"
                     f"Exec=env PVMON_NO_BROWSER=1 {' '.join(cmd)}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")


# ---------------------------------------------------------------- tray

def tray(url: str, root: Path, stop: threading.Event) -> bool:
    """Menu bar / tray icon; returns False when pystray is not available (then the console keeps it alive)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:   # not installed, or no display to connect to
        return False
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), fill=(245, 166, 35, 255))
    for x0, y0, x1, y1 in ((32, 2, 32, 10), (32, 54, 32, 62), (2, 32, 10, 32), (54, 32, 62, 32)):
        d.line((x0, y0, x1, y1), fill=(245, 166, 35, 255), width=4)

    def quit_(icon, _item):
        stop.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Отвори дашборда / Open dashboard", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Папка с данните / Data folder", lambda *_: webbrowser.open(root.as_uri())),
        pystray.MenuItem("Стартирай с компютъра / Start at login", lambda *_: set_autostart(not autostart_enabled()),
                         checked=lambda _item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Изход / Quit", quit_),
    )
    try:
        pystray.Icon("pvmon", img, APP_NAME, menu).run()
    except Exception as exc:   # no display (a headless server): keep running without the icon
        log.info("no tray icon: %s", exc)
        return False
    return True


def main() -> None:
    host, port, root = configure()
    last = previous(root)
    if last and running(*last):
        url = f"http://127.0.0.1:{last[0]}/"
        log.info("already running on %s", url)
        webbrowser.open(url)
        return
    if port is None:
        port = free_port()
    elif in_use(port):
        raise SystemExit(f"port {port} is used by another program; choose another PVMON_PORT")
    url = f"http://127.0.0.1:{port}/"
    instance = secrets.token_hex(8)
    os.environ["PVMON_INSTANCE"] = instance
    os.environ.setdefault("WEB_HEALTH_URL", f"http://127.0.0.1:{port}/healthz")
    (root / "instance").write_text(f"{port} {instance}\n")
    stop = threading.Event()
    start_web(host, port)
    for _ in range(50):
        if running(port, instance):
            break
        time.sleep(0.2)
    start_collector(stop)
    start_guard(stop)
    log.info("PV Monitor on %s, data in %s", url, root)
    if not os.environ.get("PVMON_NO_BROWSER"):
        webbrowser.open(url)
    if not tray(url, root, stop):
        try:
            while not stop.is_set():
                stop.wait(3600)
        except KeyboardInterrupt:
            pass
    stop.set()


if __name__ == "__main__":
    main()
