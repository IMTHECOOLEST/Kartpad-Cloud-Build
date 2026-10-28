"""PadMint updates itself from its own window.

The newest release's app folder (what the ZIP calls app/) is downloaded, checked
against the release's SHA256SUMS and unpacked into PadMint's own folder
(~/.padmint/padmint/<version>). From then on "python -m padmint" switches to the
newest copy it finds there, so the ZIP folder and the Mac app itself never change.
Windows runs PadMint with the Python inside its ZIP, so it keeps the download link.
"""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import urllib.parse
import zipfile

from . import __version__, tools

REPO = "https://github.com/chrissotraidis/padmint"
ZIPS = {"macos": "PadMint-macos.zip", "linux": "PadMint-linux.zip"}


def folder():
    return tools.home() / "padmint"


def platform():
    """Which download this computer updates from, or None where PadMint can't update itself."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux") and not os.environ.get("TERMUX_VERSION"):
        return "linux"
    return None


def parse(version):
    try:
        return tuple(int(part) for part in version.lstrip("v").split("."))
    except ValueError:
        return ()


def newer(version, than=__version__):
    return parse(version) > parse(than)


def installed():
    """(version, folder) of the newest PadMint this folder has installed, or None."""
    try:
        version = (folder() / "current").read_text().strip()
    except OSError:
        return None
    path = folder() / version
    return (version, path) if (path / "padmint" / "__init__.py").is_file() else None


def switch(argv=None):
    """Run the installed update instead of this PadMint when it is newer."""
    if os.environ.get("PADMINT_FROM_UPDATE") or os.environ.get("PADMINT_NO_UPDATE"):
        return
    found = installed()
    if not found or not newer(found[0]):
        return
    os.environ["PADMINT_FROM_UPDATE"] = "1"
    os.chdir(found[1])
    args = sys.argv[1:] if argv is None else argv
    os.execv(sys.executable, [sys.executable, "-m", "padmint", *args])


def latest():
    """The latest release's version (without the v), from where /releases/latest lands."""
    with tools.open_url(REPO + "/releases/latest") as response:
        landed = response.geturl()
    if "/releases/tag/" not in landed:
        return None
    return urllib.parse.unquote(landed.rsplit("/releases/tag/", 1)[1].split("?")[0].strip("/")).lstrip("v")


STATE = {"state": "idle"}
LOCK = threading.Lock()


def start(version):
    """Install version in the background; STATE tells the window how it goes."""
    with LOCK:
        if STATE["state"] in ("downloading", "installing"):
            return
        if not platform():
            raise ValueError("PadMint can't update itself on this computer yet")
        STATE.clear()
        STATE.update(state="downloading", version=version, percent=0)
    threading.Thread(target=_install, args=(version,), daemon=True).start()


def _install(version):
    try:
        install(version, lambda percent: STATE.update(percent=percent))
        STATE.update(state="ready")
    except Exception as error:  # the window shows why, with the manual download link
        STATE.update(state="failed", error=str(error))


def install(version, progress=lambda percent: None):
    name = ZIPS[platform()]
    base = f"{REPO}/releases/download/v{version}/"
    with tools.open_url(base + "SHA256SUMS") as response:
        sums = dict(reversed(line.split(None, 1)) for line in response.read().decode().splitlines() if line.strip())
    expected = sums.get(name) or sums.get("*" + name)
    if not expected:
        raise ValueError(f"PadMint v{version} lists no checksum for {name}")
    folder().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder()) as work:
        download = Path(work) / name
        digest = hashlib.sha256()
        with tools.open_url(base + name) as response, download.open("wb") as stream:
            total, done = int(response.headers.get("Content-Length") or 0), 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    progress(int(100 * done / total))
        if digest.hexdigest() != expected.strip():
            raise ValueError(f"{name} did not match its published checksum; nothing was changed")
        STATE.update(state="installing")
        top = f"PadMint-v{version}/app/"
        unpacked = Path(work) / "app"
        with zipfile.ZipFile(download) as bundle:
            for member in bundle.infolist():
                if not member.filename.startswith(top) or member.is_dir():
                    continue
                relative = member.filename[len(top):]
                if relative.startswith("python/") or ".." in Path(relative).parts:
                    continue
                target = unpacked / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(member))
        marker = (unpacked / "padmint" / "__init__.py").read_text()
        if f'__version__ = "{version}"' not in marker:
            raise ValueError(f"the download is not PadMint {version}")
        final = folder() / version
        if final.exists():
            shutil.rmtree(final)
        os.replace(unpacked, final)
    current = folder() / "current.tmp"
    current.write_text(version)
    os.replace(current, folder() / "current")
    # Older installed copies are listed in Storage; they are not removed here.
    return final

