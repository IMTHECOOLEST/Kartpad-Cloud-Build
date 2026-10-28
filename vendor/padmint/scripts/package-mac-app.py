#!/usr/bin/env python3
"""Make PadMint for Mac as an app: PadMint.app and PadMint.dmg.

Usage: scripts/package-mac-app.py OUTPUT_FOLDER

The app is a native window (macos/PadMint.swift) around the same PadMint the ZIP
ships: the same payload goes in PadMint.app/Contents/Resources/app, and Apple's
/usr/bin/python3 runs it. Games, tools and builds stay in ~/.padmint. The app is
signed with PADMINT_SIGN_IDENTITY when set (a Developer ID for public downloads,
followed by notarization), otherwise ad hoc, which only suits this Mac.
"""
import importlib.util
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/package-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
__version__ = release.__version__


def run(*argv):
    subprocess.run(argv, check=True)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = out / "PadMint.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    resources = contents / "Resources"
    for name, source in release.payload():
        target = resources / "app" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        parts = []
        for arch in ("arm64", "x86_64"):
            part = work / f"PadMint-{arch}"
            run("swiftc", "-O", "-target", f"{arch}-apple-macos13.0", "-o", str(part), str(ROOT / "macos/PadMint.swift"))
            parts.append(str(part))
        run("lipo", "-create", "-output", str(contents / "MacOS/PadMint"), *parts)
        run("swift", str(ROOT / "macos/icon.swift"), str(ROOT / "macos/AppIcon.svg"), str(work / "AppIcon.iconset"))
        run("iconutil", "-c", "icns", "-o", str(resources / "AppIcon.icns"), str(work / "AppIcon.iconset"))
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({
            "CFBundleName": "PadMint", "CFBundleDisplayName": "PadMint", "CFBundleExecutable": "PadMint",
            "CFBundleIdentifier": "com.chrissotraidis.padmint", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
            "CFBundleIconFile": "AppIcon", "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSDownloadsFolderUsageDescription": "PadMint looks in Downloads for your own game files and saves the copies it makes there.",
            "NSHumanReadableCopyright": "Free and open source: github.com/chrissotraidis/padmint",
        }, stream)
    identity = os.environ.get("PADMINT_SIGN_IDENTITY", "-")
    sign = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        sign += ["--options", "runtime", "--timestamp"]
    run(*sign, str(app))
    run("codesign", "--verify", "--deep", "--strict", str(app))
    dmg = out / "PadMint.dmg"
    with tempfile.TemporaryDirectory() as stage:
        stage = Path(stage)
        shutil.copytree(app, stage / "PadMint.app", symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        if dmg.exists():
            dmg.unlink()
        run("hdiutil", "create", "-quiet", "-volname", f"PadMint {__version__}", "-srcfolder", str(stage),
            "-ov", "-format", "UDZO", str(dmg))
    print(app)
    print(dmg)


if __name__ == "__main__":
    main()

