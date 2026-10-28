# Pinned IDO tools for Windows

This is maintainer tooling, not an enabled PadMint player route. It builds a
local candidate and runs synthetic compiler checks. It never installs tools,
creates a release, uploads binaries, or changes a game's recipe.

The same compiler package can serve SnapPad and AnnePad. Keep the compiler fix
here; each game remains responsible for its source preparation, flags, input
checks and build rules. An upstream source fix/backport is preferable when one
is available; changing the upstream tag alone is not a fix (v1.1 and v1.2 have
the same failing runtime source).

## What is pinned and changed

`inputs.json` pins the exact `decompals/ido-static-recomp` v1.1 source commit,
archive digests, MSYS runtime/development packages, Windows API headers/import
libraries and LLVM compiler-rt stack probe. The original source is
https://github.com/decompals/ido-static-recomp/tree/d6dd7fdea26cfe89dd01d7545618d4a9ae4f690a.
Generated IDO7.1 C is checked against all thirteen recorded hashes before it is
compiled. IDO5.3 compiler executables remain the unchanged official v1.1 Windows
release artifacts.
Its diagnostic catalog is copied from the exact pinned source in LF form: the
official Windows archive contains CRLF bytes, which break IDO's byte-offset
message lookup. The producer checks both source SHA-256 and line-ending-only
equivalence before replacing the catalog; compiler output is unchanged.

`windows-runtime.patch` has only host portability changes:

- IDO7.1's library address is not aligned to MSYS's 64KiB pages. Its existing
  CYGWIN branch already rounds the reserved region for `mprotect`; keep the
  start-alignment assertion in the non-CYGWIN `mmap` branch, with both region
  bounds assertions retained.
- Cygwin/newlib's `__assert` takes file, line, expression, unlike the other
  supported libc implementations. Use that order only under CYGWIN.
- Remove two unused generator includes. `unistd.h` is unavailable in MSVC;
  unused `windows.h` conflicts with rabbitizer's `CONST` macro. The existing
  Unix-only include remains. No generator logic changes.

The generator uses native MSVC, CMake and Ninja. Managed LLVM **21.1.8** compiles
the unchanged generated C for x64 MSYS. The existing MSYS fixed image base and
2MiB stack are retained because LLD's ASLR defaults broke the O3 fork pipeline.
Windows stack probing uses the pinned, unmodified LLVM compiler-rt source.
Assertions remain enabled in both the generator and compiler runtime.

## Build locally

Use Windows with the Visual Studio C++ build tools and a Native Tools command
prompt for that host. Supply the existing managed LLVM bin folder and CMake,
Ninja and Git executables explicitly. Python 3.9 or later is sufficient.
No MSYS installation, Bash, GCC installation, Rust installation or Mac is needed.

Run `python scripts/ido-windows/build.py --help` for the required paths. For
example, with those paths assigned in a Native Tools PowerShell session:

```powershell
python scripts/ido-windows/build.py `
  --work 'V:\Ido source build 1' --cache 'V:\Ido input cache' `
  --llvm $LlvmBin --cmake $CMakeExe --ninja $NinjaExe --git $GitExe `
  --jobs 1 --fetch
```

Use a new **local** work folder; Windows `cmd.exe` cannot link in a UNC working
directory. The archive cache may be a shared folder and is reusable. `--fetch`
permits downloading missing inputs; omit it for an offline build. Every archive
is checked before extraction. Bad cached files are preserved for inspection;
only a failed download's own temporary file is removed. Old work is never reset.

The producer creates `output/ido53` and `output/ido71`, each with compiler stages
and its own MSYS DLL in `usr/bin`, plus `tmp/`. It also writes `build.log`, an
exact file-hash/command receipt in `provenance.json`, and four synthetic probe
results under `probe with spaces`. Original upstream notices, README, pins and
the applied patch are retained in `output/notices`.

The source archive includes unused IRIX `libC.so.1`/`libc.so.1` names that collide
on case-insensitive filesystems. The producer selects only the exact IDO7.1
programs, source files and the IDO5.3 message catalog it uses; it does not extract
or package those libraries.

## Calling the compiler

Use the full path to the selected `usr/bin/cc.exe`, but run under the game's
build working directory and pass normal relative repository source/output names.
Set `TMPDIR=/tmp` and `USR_LIB=/usr/bin` only in that compiler process's
environment. MSYS resolves both inside the selected runtime. `USR_LIB` uses
IDO's existing helper-directory override, avoiding its ANSI-only executable-path
lookup when the runtime's physical root contains non-English characters. Each
isolated compiler version resolves its own helper stages. Do not put `cc.exe` on
the general build PATH or change global environment settings.

A relative native `TMPDIR` alone does not solve spaces: MSYS expands it to an
absolute filename. The isolated `usr/bin` layout solves that without mounts,
8.3 path assumptions or system settings.

Legacy IDO silently truncates an explicit output filename containing spaces
(e.g. `-o 'result output.o'` creates `result`), even on the exact Mac reference.
Always check that the expected output exists; a zero exit code is insufficient.
Workspace paths with spaces and Japanese characters, including the compiler's
own runtime root, pass all four exact-object probes with these child settings.
A full game build in those paths and a fresh physical x64 machine remain
separate validation work.

## Validation and distribution gate

Run `python scripts/ido-windows/test_build.py` for archive/cache/failure checks.
The producer automatically compiles hand-authored `probe.c` at O2 and O3 with
both IDO versions. All four MIPS ELF objects must exactly match the recorded Mac
v1.1 reference objects. The probe also requires an ordinary warning to compile
with the correct message, and a syntax error to fail without an object file.
These checks reject the Windows release's damaged byte-indexed catalog.
The reference cc hashes were:

- IDO5.3: `d88651af7d589c645fe21f7926f16c2979f8af0c80ab6e67f249e4652966111e`
- IDO7.1: `fdc7cbaa936e11b731c9b50118d106efae71a299d59f1e8e2cd126306acbf78f`

A fresh offline run of this maintained producer on Windows ARM64 passed all four
object checks and both versions' diagnostic checks. Its compiler executables
and DLLs matched the previous candidate byte for byte; only the IDO5.3 catalog
and the copied input lock changed. A separate control rejected the old catalog
and verified that the warning's object bytes were unchanged. Existing candidates
and their inventories were preserved. Hosted CI currently runs the offline
producer unit tests; the complete producer build is local validation.

Synthetic parity is not a full ROM-matching build or gameplay acceptance. Before
any recipe consumes a released tool archive, require a complete matching build
for that game, clean Windows x64/ARM64 installation probes, review of the exact
tool/source/notices package, and release authorization.
No binary publishing is implemented here.

After those gates, the existing PadMint tool lock can fetch one immutable,
checksum-pinned archive and expose separate IDO53/71 executable paths. Players
should download the small tested tools; they should not compile this producer or
install a second development environment. Add no build-hook machinery to the
shared installer merely to support IDO.
