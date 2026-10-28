#!/usr/bin/env python3
"""Install the Windows MIPS compiler and compile/link a synthetic N64 patch.

Usage: scripts/mips-compiler-probe.py WORK_FOLDER
No game inputs are used. The normal native LLVM linker is kept alongside the
separate MIPS compiler; the latter must not replace PATH or PADMINT_LLVM_ROOT.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from padmint import ios_module, tools  # noqa: E402
from padmint.manifest import host_id  # noqa: E402


def check_elf(path):
    data = path.read_bytes()
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x02\x01":
        raise RuntimeError(f"{path.name} is not big-endian ELF32")
    kind, machine = struct.unpack_from(">HH", data, 16)
    flags, = struct.unpack_from(">I", data, 36)
    if (kind, machine) != (1, 8) or flags & 0xF000F000 != 0x10001000:
        raise RuntimeError(f"{path.name} is not a relocatable MIPS2/o32 object: {flags:#x}")
    return {"sha256": hashlib.sha256(data).hexdigest(), "flags": hex(flags), "size": len(data)}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    host = host_id()
    if not host.startswith("windows-"):
        raise SystemExit("This probe checks the Windows-only managed MIPS compiler.")
    work = Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    tools.install(["llvm", "mips-clang"], host)
    original = tools.environment(["llvm"], host)
    env = tools.environment(["mips-clang"], host, original)
    for key in ("PATH", "PADMINT_LLVM_ROOT"):
        if env.get(key) != original.get(key):
            raise RuntimeError(f"The MIPS compiler changed {key}.")
    compiler = env["PADMINT_MIPS_CC"]
    linker = str(ios_module.llvm_tool(ios_module.llvm_root(env), "ld.lld"))
    version = subprocess.check_output([compiler, "--version"], text=True, env=env)
    # Exercise resource headers too; this target deliberately has no host C library.
    source = work / "probe.c"
    source.write_text("#include <stdint.h>\n#include <stddef.h>\n"
                      "uint32_t patch_probe(uint32_t value) { return value + sizeof(size_t); }\n")
    obj, linked = work / "probe.o", work / "linked.o"
    # A failed rerun must not let old outputs satisfy the checks below.
    obj.unlink(missing_ok=True)
    linked.unlink(missing_ok=True)
    subprocess.run([compiler, "-target", "mips", "-mips2", "-mabi=32", "-mno-abicalls", "-G0",
                    "-mno-odd-spreg", "-mno-check-zero-division", "-ffreestanding", "-c",
                    str(source), "-o", str(obj)], check=True, env=env)
    object_report = check_elf(obj)
    subprocess.run([linker, "-m", "elf32btsmip", "-r", str(obj), "-o", str(linked)],
                   check=True, env=env)
    report = {"host": host, "compiler": compiler, "compiler_version": version,
              "linker": linker, "object": object_report, "linked": check_elf(linked)}
    (work / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Managed MIPS compile/link probe passed on {host}.")


if __name__ == "__main__":
    main()
