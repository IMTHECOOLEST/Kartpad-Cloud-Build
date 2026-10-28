"""Give Windows build tools a private UTF-8 console without changing the caller."""
from pathlib import Path
import subprocess
import sys


def command(argv):
    # MSVC /showIncludes follows the console code page even when stdout is a
    # pipe. A legacy page turns header paths into '?' and poisons Ninja's cache.
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return ([sys.executable, '-I', '-B', str(Path(__file__).resolve()), *argv],
            {'creationflags': subprocess.CREATE_NEW_CONSOLE, 'startupinfo': startup})


def main():
    import ctypes
    kernel = ctypes.windll.kernel32
    if not kernel.SetConsoleCP(65001) or not kernel.SetConsoleOutputCP(65001):
        raise ctypes.WinError()
    # Pass the original argv directly. Paths containing &, %, ! or quotes must
    # never become shell syntax. Descendants inherit only this private console.
    return subprocess.call(sys.argv[1:])


if __name__ == '__main__':
    sys.exit(main())
