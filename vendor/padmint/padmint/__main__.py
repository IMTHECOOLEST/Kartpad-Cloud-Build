import os
import signal

from .macos_startup import prefer_native_macos

prefer_native_macos()

from .update import switch

switch()  # a newer PadMint installed from the window runs instead of this one

from .cli import main

if os.name == "nt":
    # The PadMint window's Cancel button sends Ctrl-Break: cancel the way Ctrl-C does.
    signal.signal(signal.SIGBREAK, signal.default_int_handler)
raise SystemExit(main())
