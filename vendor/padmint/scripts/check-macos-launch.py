#!/usr/bin/env python3
"""Exercise the shipped Mac launcher and menu, without game files or downloads.

Run with Apple's Python: /usr/bin/python3 scripts/check-macos-launch.py
CI also supplies --expect-machine arm64 or x86_64, so a changed runner cannot
silently skip the intended coverage. Apple Silicon checks both native launch
and a real Rosetta parent; Intel checks the explanation for the unavailable Mac
copy. This is startup/menu evidence, not a game build or gameplay check.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/usr/bin/python3"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_launch(package, scratch, machine, translated, expected_host):
    """Run the unmodified launcher from outside the source tree and package."""
    scratch.mkdir()
    # Empty PadMint state, no inherited Python packages, and no network dependency:
    # recipe prefetch can fail immediately while the built-in menu stays usable.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "PADMINT_", "PADFORGE_"))
           and key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin",
               PADMINT_HOME=str(scratch / "padmint"), PADMINT_LANG="en",
               PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONVERBOSE="1",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
               NO_PROXY="127.0.0.1,localhost")
    prefix = ["/usr/bin/arch", "-" + machine]
    probe = subprocess.run(prefix + [PYTHON, "-c",
        "import json, platform, subprocess; "
        "p = subprocess.run(['/usr/sbin/sysctl', '-in', 'sysctl.proc_translated'], capture_output=True, text=True); "
        "print(json.dumps([platform.machine(), p.stdout.strip() == '1']))"],
        env=env, cwd=scratch, capture_output=True, text=True, timeout=15)
    require(probe.returncode == 0,
            f"Cannot exercise {machine} startup. Rosetta must be installed for the Apple Silicon check.\n{probe.stderr[-2000:]}")
    require(json.loads(probe.stdout) == [machine, translated],
            f"Expected actual {machine} process, translated={translated}; got {probe.stdout.strip()}")
    print(f"Starting real {machine} process (Rosetta: {translated})", flush=True)
    log = scratch / "launcher.log"
    url = None
    with log.open("w") as output:
        child = subprocess.Popen(prefix + ["/bin/bash", str(package / "PadMint.command"), "ui", "--no-open"],
                                 env=env, cwd=scratch, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                found = re.search(r"http://127\.0\.0\.1:\d+/\?token=[\w-]+", log.read_text())
                if found:
                    url = found.group()
                    break
                require(child.poll() is None, "Packaged launcher exited before serving its page")
                time.sleep(0.1)
            require(url, "Packaged launcher did not print its page address within 30 seconds")
            with urllib.request.urlopen(url.replace("/?", "/api/player?") + "&lang=en", timeout=10) as reply:
                data = json.load(reply)
            require(data["host"] == expected_host,
                    f"Wrong running process host: {data['host']}, expected {expected_host}")
            game = next(game for game in data["games"] if game["id"] == "kartpad")
            enabled = {target["id"] for target in game["platforms"]}
            require({"android", "ios"} <= enabled, f"Lost existing KartPad choices: {enabled}")
            if expected_host == "macos-arm64":
                require("macos" in enabled, "Apple Silicon player cannot choose This Mac")
                require(not any(target["id"] == "macos" for target in game["held"]),
                        "This Mac is both enabled and unavailable")
            else:
                require("macos" not in enabled, "Intel player was offered an unsupported Mac build")
                held = next((target for target in game["held"] if target["id"] == "macos"), {})
                require("Intel" in held.get("text", "") and "Apple Silicon" in held.get("text", ""),
                        "Intel player needs an explicit Apple Silicon requirement in the device chooser")
                require(held.get("label") == "This Mac", "Unavailable Mac copy is missing its device label")
            # The actual served page must carry the menu data, and Python's import
            # trace must show the extracted package, not the maintainer's checkout.
            with urllib.request.urlopen(url + "&lang=en", timeout=10) as reply:
                page = reply.read().decode()
            require('"host": "' + expected_host + '"' in page and '"id": "kartpad"' in page,
                    "The served page did not carry the checked menu")
            for module in ("__init__.py", "cli.py"):
                require(str(package / "app/padmint" / module) in log.read_text(),
                        f"Python did not import {module} from the extracted player package")
            print(f"PASS: {machine} launch -> {expected_host}; KartPad choices: {', '.join(sorted(enabled))}", flush=True)
        except Exception as error:
            raise RuntimeError(f"{error}\nLauncher log tail:\n{log.read_text()[-5000:]}") from error
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            if url:
                port = urllib.parse.urlsplit(url).port
                deadline = time.monotonic() + 5
                while True:
                    try:
                        connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    except OSError:
                        break
                    connection.close()
                    require(time.monotonic() < deadline, "Launcher cleanup left its server running")
                    time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-machine", choices=("arm64", "x86_64"))
    args = parser.parse_args()
    require(platform.system() == "Darwin", "This check must run on macOS")
    machine = subprocess.check_output([PYTHON, "-c", "import platform; print(platform.machine())"], text=True).strip()
    require(machine in ("arm64", "x86_64"), f"Unexpected Apple Python architecture: {machine}")
    if args.expect_machine:
        require(machine == args.expect_machine, f"Expected {args.expect_machine} runner; got {machine}")
    spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts/package-release.py")
    packaging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaging)
    with tempfile.TemporaryDirectory(prefix="padmint-mac-launch-") as folder:
        scratch = Path(folder).resolve()
        archive = scratch / "PadMint-macos.zip"
        packaging.make_zip(archive, "PadMint-player-check", ROOT / "launchers/PadMint.command")
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(scratch)], check=True)
        package = scratch / "PadMint-player-check"
        print(f"Extracted player package: {package}", flush=True)
        check_launch(package, scratch / "native", machine, False, "macos-" + machine)
        if machine == "arm64":
            check_launch(package, scratch / "rosetta", "x86_64", True, "macos-arm64")
    print("Mac package startup checks passed; temporary servers and folders removed.")


if __name__ == "__main__":
    main()
