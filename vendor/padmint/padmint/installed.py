"""Which PadMint games are on this Mac and on the iPhones and iPads paired with it.

Mac apps come from the Applications folders. iPhone and iPad apps come from Xcode's
devicectl, run directly from Xcode (never through xcrun, which would offer to install
developer tools on a Mac without them). Only apps that match a catalog game are kept.
A paired device that can't be reached right now keeps what PadMint saw last time, with
when that was, in devices.json next to PadMint's other files.
"""
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import tools
from .manifest import catalog

STATE = {"state": "idle", "mac": [], "devices": [], "xcode": False, "checked": None}
LOCK = threading.Lock()


def key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def names():
    """Each catalog game under the names its app goes by ('BlueWake', 'OpenRCT2 Touch')."""
    found = {}
    for game, entry in catalog().items():
        for name in ((entry.get("manifest") or {}).get("name"), entry.get("name"), game):
            if name:
                found.setdefault(key(name), game)
    return found


def mac_apps(known):
    """PadMint games in /Applications and ~/Applications."""
    found = []
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        try:
            apps = [path for path in folder.iterdir() if path.suffix == ".app"]
        except OSError:
            continue
        for app in apps:
            game = known.get(key(app.stem))
            if not game:
                continue
            try:
                with open(app / "Contents" / "Info.plist", "rb") as handle:
                    info = plistlib.load(handle)
                when = int(app.stat().st_mtime)
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            found.append({"game": game, "version": info.get("CFBundleShortVersionString") or "",
                          "path": str(app), "when": when})
    return found


def devicectl():
    """Xcode's devicectl, or None without Xcode."""
    if sys.platform != "darwin":
        return None
    try:
        folder = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True,
                                timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    path = Path(folder) / "usr" / "bin" / "devicectl"
    return str(path) if folder and path.is_file() else None


def ask(tool, args, timeout):
    with tempfile.TemporaryDirectory() as folder:
        out = os.path.join(folder, "out.json")
        subprocess.run([tool, *args, "--json-output", out], capture_output=True, timeout=timeout)
        with open(out) as handle:
            return json.load(handle)["result"]


def saved_path():
    return tools.tools_root().parent / "devices.json"


def read_saved():
    try:
        return json.loads(saved_path().read_text())
    except (OSError, ValueError):
        return {}


def phones(tool, known):
    """Each paired iPhone and iPad with the PadMint games on it."""
    saved = read_saved()
    try:
        listed = ask(tool, ["list", "devices"], 30).get("devices", [])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return list(saved.values())
    found = {}
    for device in listed:
        hardware = device.get("hardwareProperties") or {}
        connection = device.get("connectionProperties") or {}
        udid = hardware.get("udid")
        if (hardware.get("reality") != "physical" or hardware.get("platform") != "iOS" or not udid
                or connection.get("pairingState") != "paired"):
            continue
        entry = dict(saved.get(udid) or {})
        entry.update({"id": udid, "name": (device.get("deviceProperties") or {}).get("name") or udid,
                      "kind": "ipad" if hardware.get("deviceType") == "iPad" else "iphone",
                      "model": hardware.get("marketingName") or "", "reachable": False})
        try:
            apps = ask(tool, ["device", "info", "apps", "--device", udid], 40).get("apps", [])
            entry["apps"] = [{"game": known[key(app.get("name"))], "version": app.get("version") or ""}
                             for app in apps if key(app.get("name")) in known]
            entry["checked"] = int(time.time())
            entry["reachable"] = True
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            entry.setdefault("apps", [])
        found[udid] = entry
    try:
        saved_path().write_text(json.dumps({udid: {k: v for k, v in entry.items() if k != "reachable"}
                                            for udid, entry in found.items()}))
    except OSError:
        pass
    return list(found.values())


def check():
    known = names()
    tool = devicectl()
    mac = mac_apps(known) if sys.platform == "darwin" else []
    devices = phones(tool, known) if tool else []
    with LOCK:
        STATE.update({"state": "ready", "mac": mac, "devices": devices, "xcode": bool(tool),
                      "checked": int(time.time())})


def status(fresh=False):
    """What was found; starts a check when none has run yet, or when asked to look again."""
    with LOCK:
        start = STATE["state"] != "checking" and (fresh or STATE["state"] == "idle")
        if start:
            if STATE["state"] == "idle":
                saved = read_saved()
                STATE["devices"] = [dict(entry, reachable=False) for entry in saved.values()]
            STATE["state"] = "checking"
    if start:
        threading.Thread(target=lambda: _safe_check(), daemon=True).start()
    with LOCK:
        return json.loads(json.dumps(STATE))


def _safe_check():
    try:
        check()
    except Exception:  # never leave the window waiting
        with LOCK:
            STATE["state"] = "ready"


def snapshot():
    """What the window shows before any check: the last check, or the devices seen last time."""
    with LOCK:
        if STATE["state"] != "idle":
            return json.loads(json.dumps(STATE))
    # The Applications folders take a moment to read, so the first view is already right about this Mac.
    return {"state": "idle", "mac": mac_apps(names()) if sys.platform == "darwin" else [], "xcode": False,
            "checked": None, "devices": [dict(entry, reachable=False) for entry in read_saved().values()]}
