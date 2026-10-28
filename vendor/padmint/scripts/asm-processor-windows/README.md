# Pinned asm-processor Python tools for Windows

Maintainer preparation only. No game recipe or managed-tool pin consumes this
candidate yet. Full matching game builds and release review remain required.

The official asm-processor 1.0.1 Windows executable invokes `sh -c` to assemble
GLOBAL_ASM blocks. MinGit supplies no shell. The same upstream tag provides a
Python implementation that can run with PadMint's bundled Python, without Rust,
Bash or another installed development environment.

`inputs.json` pins its four source files at commit
`52d6454e9d2ac8ed30fe523c0b58647d65d6199a`. Place the exact files in a read-only
source cache, then run:

```powershell
python prepare.py --source SOURCE_CACHE --output NEW_OUTPUT_FOLDER
```

`prepare(source, output)` returns the prepared `build.py` path. It checks all
original bytes before creating output, preserves the upstream public-domain
license, and records output hashes. Existing output folders are never replaced.
The process-call patch replaces the assembler's `os.system` call with a direct
argument-list invocation and adds its two standard-library imports. Upstream
`build.py` already quotes each assembler argument with `shlex.quote`; the patch
uses `shlex.split` to restore those exact arguments. No assembler command is
passed through a shell, and failure still follows upstream's nonzero-status path.
The Python implementation's parsing and object processing are unchanged.

On Windows, the patch also creates its two temporary files in the build's current
directory and passes their relative ASCII filenames to the assembler. The pinned
GNU assembler cannot open absolute filenames containing characters outside the
Windows code page; a user's non-English temporary directory otherwise breaks even
an ASCII build path. The working directory, include lookup, temporary-file cleanup
and assembler flags stay unchanged. Other hosts keep the original temporary-file
location. This fixes these assembler arguments; it does not establish Unicode
path support for every other tool in a game build.

Pass the returned path to the game's existing Python-tool adapter. Bundled
Windows Python uses an isolated module search path; the adapter must add the
prepared script's parent directory when invoking it. No executable shim is
needed. Keep IDO's `TMPDIR=/tmp` local to the build child and use its isolated
MSYS runtime layout described in `../ido-windows/README.md`.

The optional `probe.py` takes `--tool`, `--compiler`, `--assembler`, and a new
`--work` directory. It uses existing IDO7.1 and GNU MIPS assembler executables to
compile the hand-authored `probe.c`, then checks its MIPS instructions and C data.
It does not download tools or read a game input. Run it in a work path with spaces
and non-English characters, with a non-English `TEMP`, to exercise argument handling.
A successful synthetic probe does not establish
a full matching ROM build or gameplay acceptance.
