"""Start natively before choosing a Mac's devices or downloading its build tools."""

import os
from pathlib import Path
import platform
import subprocess
import sys


def prefer_native_macos():
    """A Rosetta Terminal must not make an Apple Silicon Mac look like an Intel Mac."""
    if sys.platform != "darwin" or platform.machine().lower() != "x86_64":
        return
    try:
        translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
                                    capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit("PadMint couldn't check whether this Mac is using Rosetta. "
                         "Open Terminal without Open using Rosetta and try again.") from error
    # The optional key is absent on Intel Macs; sysctl -i returns no output there.
    if translated.returncode == 0 and translated.stdout.strip() in ("", "0") and not translated.stderr.strip():
        return
    if translated.returncode != 0 or translated.stdout.strip() != "1":
        raise SystemExit("PadMint couldn't identify this Mac's processor. "
                         "Open Terminal without Open using Rosetta and try again.")
    if os.environ.get("PADMINT_NATIVE_RESTART"):
        raise SystemExit("PadMint is still running through Rosetta after restarting. "
                         "Open Terminal without Open using Rosetta and try again.")

    # Apple's universal Python is also what PadMint.command uses. A Python installed
    # for Intel alone cannot be restarted as arm64, even on an Apple Silicon Mac.
    command = ["/usr/bin/arch", "-arm64", "/usr/bin/python3"]
    try:
        native = subprocess.run(command + ["-c", "import platform, sys; "
                                "sys.exit(platform.machine() != 'arm64' or sys.version_info < (3, 9))"],
                                capture_output=True, text=True, timeout=30)
        if native.returncode != 0:
            raise OSError("native Python is unavailable")
        environ = dict(os.environ, PADMINT_NATIVE_RESTART="1")
        root = str(Path(__file__).resolve().parent.parent)
        environ["PYTHONPATH"] = os.pathsep.join(filter(None, (root, environ.get("PYTHONPATH"))))
        os.execve(command[0], command + ["-m", "padmint", *sys.argv[1:]], environ)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit("PadMint couldn't start Apple's Python natively on this Mac. "
                         "Install or update Apple's command line tools (xcode-select --install), "
                         "then open PadMint.command from the new PadMint folder.") from error
