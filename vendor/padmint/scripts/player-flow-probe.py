#!/usr/bin/env python3
"""Exercise the real player runner with a tiny local synthetic repository, without downloads.

Usage: python scripts/player-flow-probe.py WORK_FOLDER [--app PADMINT_APP_FOLDER]

--app must name the extracted package's app folder when checking a release package.
The probe keeps its records in WORK_FOLDER and never uses a player's PadMint home.
It tests orchestration and IPA metadata, not playable game or device compatibility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import mock


BACKEND = '''import os
from pathlib import Path
import sys
from .payload import write_ipa

stage, work, output = sys.argv[1:]
work = Path(work)
work.mkdir(parents=True, exist_ok=True)
if stage == "prepare":
    count = work / "attempts.txt"
    count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
    if os.environ.get("PADMINT_PROBE_MODE") == "fail":
        print("Intentional synthetic backend failure", flush=True)
        raise SystemExit(17)
elif stage == "package":
    assert (work / "attempts.txt").is_file()
    write_ipa(Path(output))
else:
    raise SystemExit("unknown probe stage")
'''

PAYLOAD = '''import plistlib
import struct
import zipfile

def write_ipa(path):
    # Valid arm64/iOS load-command metadata, deliberately no runnable machine code.
    command = struct.pack("<6I", 0x32, 24, 2, 16 << 16, 26 << 16, 0)
    binary = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 2, 1, len(command), 0, 0)
    binary += command + b"synthetic non-runnable player-flow fixture"
    info = plistlib.dumps({"CFBundleExecutable": "Synthetic",
                          "CFBundleIdentifier": "invalid.padmint.probe"})
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in (("Info.plist", info), ("Synthetic", binary)):
            entry = zipfile.ZipInfo("Payload/Synthetic.app/" + name, (2026, 1, 1, 0, 0, 0))
            bundle.writestr(entry, data)
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(work, app):
    work, app = Path(work).resolve(), Path(app).resolve()
    require(not work.exists() or not any(work.iterdir()), "Use an empty probe work folder")
    require((app / "padmint" / "__init__.py").is_file(), "--app must contain the padmint package")
    # Explicitly choose the package under test, including under embeddable Python's ._pth isolation.
    sys.path.insert(0, str(app))
    import padmint
    from padmint import cli
    from padmint.manifest import host_id
    cli.configure_output()  # Match the normal CLI entry before calling its runner directly.
    require(Path(padmint.__file__).resolve().parent == app / "padmint", "Imported the wrong PadMint copy")
    work.mkdir(parents=True, exist_ok=True)
    origin = work / "source with spaces & apostrophe's"
    origin.mkdir()
    package = origin / "builder" / "synthetic_builder"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(BACKEND, encoding="utf-8")
    (package / "payload.py").write_text(PAYLOAD, encoding="utf-8")
    (origin / ".gitignore").write_text("build/\n__pycache__/\n", encoding="utf-8")
    (origin / "version.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    recipe = {
        "schema_version": 1, "id": "padmint-probe", "name": "PadMint Probe", "game": "Synthetic",
        "kind": "clean-engine", "status": "experimental",
        "inputs": [{"type": "synthetic", "when": "in-app"}],
        "targets": {"ios": {"hosts": {host_id(): "experimental"}, "output": "ipa", "check": "ipa",
                            "steps": [{"stage": stage, "env": {"PYTHONPATH": "{repo}/builder"},
                                       "command": ["{python}", "-m", "synthetic_builder.cli", stage,
                                                   "{work}", "{output}"]}
                                      for stage in ("prepare", "package")]}},
        "publication": {"public_binaries": False},
    }
    (origin / "padmint.json").write_text(json.dumps(recipe), encoding="utf-8")
    git = shutil.which("git")
    require(git is not None, "The probe needs Git on PATH; it downloads no tools")

    def git_at(folder, *args):
        return subprocess.check_output([git, "-C", str(folder), *args], text=True).strip()

    git_at(origin, "init", "-q")
    git_at(origin, "add", ".")
    git_at(origin, "-c", "user.name=PadMint probe", "-c", "user.email=probe@example.invalid",
           "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic player fixture")
    git_at(origin, "tag", "v1.0.0")
    revision = git_at(origin, "rev-parse", "HEAD")
    home = work / "PadMint home with spaces"
    source = home / "games/padmint-probe-v1.0.0"
    (source / ".git").mkdir(parents=True)
    (source / "private fixture.txt").write_text("preserve original", encoding="utf-8")
    partial = source.with_name(source.name + ".partial")
    partial.mkdir()
    (partial / "private partial.txt").write_text("preserve partial", encoding="utf-8")
    out = work / "player copies & spaces"
    entry = {"repo_url": str(origin), "player_targets": ["ios"], "free_space_gb": 0}
    codes, results = [], []
    # Only the catalog location and environment are substituted. Git cloning, manifest
    # validation, process spawning, stage events, output checks and records are real.
    with mock.patch.dict(os.environ, {"PADMINT_HOME": str(home), "PADMINT_PROBE_MODE": "ok"}), \
            mock.patch.object(cli, "catalog", return_value={"padmint-probe": entry}), \
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("probe must not use the network")):
        for mode, expected in (("ok", 0), ("fail", 17), ("ok", 0), ("ok", 0)):
            os.environ["PADMINT_PROBE_MODE"] = mode
            codes.append(cli.make("padmint-probe", "ios", None, out, ref="v1.0.0", jobs=1, results=results))
            require(codes[-1] == expected, f"{mode} build returned {codes[-1]}, expected {expected}")
            if len(codes) == 1:
                result_hash = hashlib.sha256(results[0].read_bytes()).hexdigest()
            require(hashlib.sha256(results[0].read_bytes()).hexdigest() == result_hash,
                    "A failed or repeated attempt changed the saved copy")
        # The kernel lock must still exclude a second runner and be reusable after release.
        lock = source / "build/padmint/runner.lock"
        with cli.workspace_lock(lock):
            try:
                with cli.workspace_lock(lock):
                    raise RuntimeError("A second runner acquired the workspace lock")
            except ValueError as error:
                require("Another PadMint" in str(error), str(error))
        with cli.workspace_lock(lock):
            pass
    records = [json.loads(path.read_text()) for path in (source / "build/padmint").glob("*/runs/*/record.json")]
    require(len(records) == 4, "Each attempt must have its own build record")
    require(sorted(r["status"] for r in records) == ["completed"] * 3 + ["failed"], "Wrong attempt status")
    require({r["revision"] for r in records} == {revision}, "A build did not use the selected revision")
    require(git_at(source, "status", "--porcelain") == "", "The runner dirtied its source checkout")
    require(len({r["workspace_key"] for r in records}) == 1, "A retry did not reuse the build workspace")
    count = next((source / "build/padmint").glob("*/backend/attempts.txt"))
    require(count.read_text() == "4", "Backend cache was lost between attempts")
    require(all(r["publication_gate"]["result"] == "PASS" for r in records if r["status"] == "completed"),
            "Synthetic output did not pass the publication audit")
    kept = next(source.parent.glob(source.name + ".recovered-*"))
    kept_partial = next(source.parent.glob(partial.name + ".recovered-*"))
    require((kept / "private fixture.txt").read_text() == "preserve original", "Original files were lost")
    require((kept_partial / "private partial.txt").read_text() == "preserve partial", "Partial files were lost")
    report = {"host": host_id(), "python": sys.version.split()[0], "padmint": str(app), "revision": revision,
              "attempt_codes": codes, "source_and_partial_preserved": True, "workspace_reused": True,
              "output_sha256": result_hash, "runtime_launch": "not-tested-synthetic-fixture"}
    (work / "probe-result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("Player flow probe passed (synthetic fixture; no gameplay claim).")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("--app", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    run(args.work, args.app)
