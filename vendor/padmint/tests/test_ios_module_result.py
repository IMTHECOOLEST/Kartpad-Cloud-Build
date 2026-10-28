"""Revision-selected module packaging, using synthetic non-runnable files only."""
import argparse
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from padmint import cli, ios_module, manifest
from fixtures import entries, macho, write_ipa


class ModuleResultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="PadMint module result ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.module = self.work / "selected.dylib"
        self.module.write_bytes(macho(filetype=6))
        self.identity = self.work / "selected.identity"
        self.identity.write_text("a" * 64 + "\n", encoding="utf-8")
        self.result = self.work / "result.json"
        self.data = self.item(self.module, "game-r2.dylib")
        self.data["companions"] = [self.item(self.identity, "game-r2.dylib.identity")]
        self.app = self.root / "base.ipa"
        members = entries()
        members.pop("KartPadBuilderProvenance.json")
        write_ipa(self.app, members)
        self.llvm = self.root / "llvm"
        (self.llvm / "bin").mkdir(parents=True)
        for name in ("llvm-strip", "llvm-install-name-tool"):
            (self.llvm / "bin" / (name + (".exe" if os.name == "nt" else ""))).touch()

    def item(self, path, into):
        return {"file": path.relative_to(self.work).as_posix(), "into": into,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def read(self):
        self.result.write_text(json.dumps(self.data), encoding="utf-8")
        return ios_module.read_result(self.result, self.work)

    def test_selected_module_and_identity_keep_base_bytes_and_metadata(self):
        module, into, companions = self.read()
        output = self.root / "personal.ipa"
        with mock.patch.object(ios_module.subprocess, "run"):
            ios_module.insert(self.app, module, into, output, self.llvm, self.work / "insert", companions)
        with zipfile.ZipFile(self.app) as before, zipfile.ZipFile(output) as after:
            self.assertEqual(set(after.namelist()) - set(before.namelist()),
                             {"Payload/Synthetic.app/game-r2.dylib", "Payload/Synthetic.app/game-r2.dylib.identity"})
            for info in before.infolist():
                actual = after.getinfo(info.filename)
                self.assertEqual(after.read(info.filename), before.read(info))
                self.assertEqual((actual.external_attr, actual.create_system, actual.date_time),
                                 (info.external_attr, info.create_system, info.date_time))
            identity = after.getinfo("Payload/Synthetic.app/game-r2.dylib.identity")
            self.assertEqual(after.read(identity), self.identity.read_bytes())
            self.assertEqual(stat.S_IMODE(identity.external_attr >> 16), 0o644)
            self.assertEqual(stat.S_IMODE(after.getinfo("Payload/Synthetic.app/game-r2.dylib").external_attr >> 16), 0o755)

    def test_stale_hash_missing_file_and_malformed_records_fail(self):
        original = copy.deepcopy(self.data)
        for mutate in (
                lambda data: data.update(sha256="0" * 64),
                lambda data: data.update(file="missing.dylib"),
                lambda data: data.update(sha256=True),
                lambda data: data.update(unknown=True),
                lambda data: data.update(companions={}),
                lambda data: data["companions"][0].update(sha256="0" * 64),
                lambda data: data["companions"][0].pop("file")):
            self.data = copy.deepcopy(original)
            mutate(self.data)
            with self.subTest(data=self.data), self.assertRaises(ios_module.ModuleError):
                self.read()

    def test_paths_stay_inside_work_and_destinations_are_unambiguous(self):
        original = copy.deepcopy(self.data)
        for key, value in (("file", "../outside"), ("file", "C:/outside"),
                           ("file", "folder\\file"), ("into", "../escape.dylib"),
                           ("into", "/absolute.dylib"), ("into", "C:/absolute.dylib"),
                           ("into", "folder//game.dylib")):
            self.data = copy.deepcopy(original)
            self.data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ios_module.ModuleError):
                self.read()
        self.data = original
        self.data["companions"][0]["into"] = "GAME-R2.DYLIB"
        with self.assertRaises(ios_module.ModuleError):
            self.read()
        self.data["companions"] = [self.item(self.identity, "identity"), self.item(self.identity, "IDENTITY")]
        with self.assertRaisesRegex(ios_module.ModuleError, "duplicate"):
            self.read()
        self.result = self.root / "outside.json"
        with self.assertRaisesRegex(ios_module.ModuleError, "inside this build"):
            self.read()

    @unittest.skipIf(os.name == "nt", "Windows symlink privilege is not required for player builds")
    def test_symlink_cannot_escape_work(self):
        outside = self.root / "outside.dylib"
        outside.write_bytes(self.module.read_bytes())
        self.module.unlink()
        self.module.symlink_to(outside)
        with self.assertRaisesRegex(ios_module.ModuleError, "outside this workspace"):
            self.read()

    def test_companions_cannot_smuggle_unchecked_libraries(self):
        for signature in ("feedface", "cefaedfe", "feedfacf", "cffaedfe",
                          "cafebabe", "bebafeca", "cafebabf", "bfbafeca"):
            self.identity.write_bytes(bytes.fromhex(signature) + b"synthetic")
            self.data["companions"] = [self.item(self.identity, "looks-like-data")]
            with self.subTest(signature=signature), self.assertRaisesRegex(ios_module.ModuleError, "extra libraries"):
                self.read()

    def test_archive_collisions_preserve_previous_output(self):
        output = self.root / "personal.ipa"
        output.write_bytes(b"previous personal output")
        for destination in ("Info.plist", "info.PLIST", "Synthetic/child", "Frameworks"):
            into = "Frameworks/game.dylib"
            with self.subTest(destination=destination), mock.patch.object(ios_module.subprocess, "run"), \
                    self.assertRaises(ios_module.ModuleError):
                ios_module.insert(self.app, self.module, into, output, self.llvm, self.work / "insert",
                                  [(self.identity, destination)])
            self.assertEqual(output.read_bytes(), b"previous personal output")
            self.assertFalse(output.with_name(output.name + ".partial").exists())

    def test_module_staging_does_not_overwrite_companion_or_source(self):
        staging = self.work / "padmint-ios-module"
        staging.mkdir()
        identity = staging / "game-r2.dylib"
        identity.write_bytes(b"private input identity\n")
        output = self.root / "personal.ipa"
        with mock.patch.object(ios_module.subprocess, "run"):
            ios_module.insert(self.app, self.module, "game-r2.dylib", output, self.llvm, staging,
                              [(identity, "identity.txt")])
            # An existing backend output in the old fixed staging path is safe too.
            ios_module.insert(self.app, self.module, self.module.name, output, self.llvm, self.work)
        self.assertEqual(identity.read_bytes(), b"private input identity\n")
        self.assertEqual(self.module.read_bytes(), macho(filetype=6))

    def test_package_staging_does_not_overwrite_companion(self):
        output = self.root / "personal.ipa"
        companion = output.with_name(output.name + ".partial")
        companion.write_bytes(b"private input identity\n")
        with mock.patch.object(ios_module.subprocess, "run"):
            ios_module.insert(self.app, self.module, "game-r2.dylib", output, self.llvm, self.work,
                              [(companion, "identity.txt")])
        self.assertEqual(companion.read_bytes(), b"private input identity\n")
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("Payload/Synthetic.app/identity.txt"), companion.read_bytes())

    def test_final_output_cannot_replace_an_input(self):
        for output in (self.app, self.module, self.identity):
            before = output.read_bytes()
            with self.subTest(output=output), self.assertRaisesRegex(ios_module.ModuleError, "must not replace"):
                ios_module.insert(self.app, self.module, "game-r2.dylib", output, self.llvm, self.work,
                                  [(self.identity, "identity.txt")])
            self.assertEqual(output.read_bytes(), before)

    def recipe(self):
        return {"schema_version": 2, "id": "resultpad", "name": "ResultPad", "game": "Synthetic",
                "kind": "disc-translation", "status": "experimental", "inputs": [{"type": "disc"}],
                "targets": {"ios": {"hosts": {manifest.host_id(): "experimental"}, "tools": ["libcxx"],
                    "output": "ipa", "check": "ipa", "published_app": "ResultPad-v{version}.ipa",
                    "ios_module": {"result": "{work}/module-result.json"},
                    "command": ["{python}", "{repo}/builder.py", "{disc}", "{work}"]}},
                "publication": {"public_binaries": False}}

    def test_recipe_requires_schema_two_and_one_packaging_mode(self):
        data = self.recipe()
        manifest.validate_manifest(data)
        data["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "requires schema_version 2"):
            manifest.validate_manifest(data)
        data["schema_version"] = 2
        data["targets"]["ios"]["ios_module"]["file"] = "{work}/game.dylib"
        with self.assertRaisesRegex(ValueError, "not both"):
            manifest.validate_manifest(data)

    def test_actual_backend_selects_each_revision_and_shared_runner_packages_it(self):
        repo = self.root / "backend"
        repo.mkdir()
        (repo / ".gitignore").write_text("build/\n")
        (repo / "library.bin").write_bytes(macho(filetype=6))
        recipe = self.recipe()
        recipe["targets"]["ios"]["hosts"] = {"windows-arm64": "experimental", "linux-x86_64": "experimental"}
        recipe["targets"]["ios"]["ios_module"]["on"] = ["windows", "linux"]
        (repo / "padmint.json").write_text(json.dumps(recipe))
        (repo / "builder.py").write_text('''from pathlib import Path
import hashlib, json, shutil, sys
disc, work = Path(sys.argv[1]), Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
revision = disc.read_text().strip()
assert revision in ('r0', 'r2')
module = work / 'selected.dylib'
shutil.copyfile(Path(__file__).with_name('library.bin'), module)
identity = work / 'selected.identity'
identity.write_bytes((hashlib.sha256(disc.read_bytes()).hexdigest() + '\\n').encode())
def item(path, into):
    return {'file': path.name, 'into': into, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
result = item(module, 'game-' + revision + '.dylib')
result['companions'] = [item(identity, result['into'] + '.identity')]
(work / 'module-result.json').write_text(json.dumps(result))
''')
        for args in (["init", "-q"], ["config", "user.email", "test@example.invalid"],
                     ["config", "user.name", "Test"], ["add", "."], ["commit", "-qm", "synthetic"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        prepared = {"sdk": self.work, "toolchain": self.work / "toolchain.cmake", "executable": self.module}
        real_run = subprocess.run
        def run(command, *args, **kwargs):
            if Path(command[0]).name.removesuffix(".exe") in ("llvm-strip", "llvm-install-name-tool"):
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)
        for selected, host in (("r0", "windows-arm64"), ("r2", "linux-x86_64")):
            disc = self.root / (selected + ".disc")
            disc.write_text(selected)
            args = argparse.Namespace(game="resultpad", repo=repo, disc=disc, revision=revision,
                                      target="ios", source_only=False, no_mods=False, jobs=1, app=self.app)
            with mock.patch.object(cli, "host_id", return_value=host), \
                    mock.patch.object(ios_module, "prepare", return_value=prepared), \
                    mock.patch.object(ios_module, "llvm_root", return_value=self.llvm), \
                    mock.patch.object(ios_module, "check_imports") as check, \
                    mock.patch.object(cli, "backend_env", return_value=dict(os.environ)), \
                    mock.patch.object(subprocess, "run", side_effect=run), redirect_stdout(io.StringIO()):
                validated_repo, checked_disc = cli.validate(args)
                self.assertEqual(cli.execute(args, validated_repo, checked_disc), 0)
                check.assert_called_once()
        records = list((repo / "build/padmint").glob("*/runs/*/record.json"))
        self.assertEqual(len(records), 2)
        names = set()
        for record_path in records:
            record = json.loads(record_path.read_text())
            self.assertEqual(record["status"], "completed")
            receipt = record_path.parent / "module-result.json"
            self.assertEqual(record["module_result_sha256"], hashlib.sha256(receipt.read_bytes()).hexdigest())
            with zipfile.ZipFile(record_path.parent / "personal.ipa") as archive:
                module = next(name for name in archive.namelist() if name.endswith(".dylib"))
                names.add(Path(module).name)
                identity = archive.read(module + ".identity").decode().strip()
                selected = Path(module).name.removeprefix("game-").removesuffix(".dylib")
                self.assertEqual(identity, hashlib.sha256(selected.encode()).hexdigest())
        self.assertEqual(names, {"game-r0.dylib", "game-r2.dylib"})
        earlier_outputs = {path.parent / "personal.ipa": (path.parent / "personal.ipa").read_bytes()
                           for path in records}
        # Reusing the same result format still cannot bypass the shared import check.
        with mock.patch.object(cli, "host_id", return_value=host), \
                mock.patch.object(ios_module, "prepare", return_value=prepared), \
                mock.patch.object(ios_module, "llvm_root", return_value=self.llvm), \
                mock.patch.object(ios_module, "check_imports", side_effect=ios_module.ModuleError("missing callback")), \
                mock.patch.object(cli, "backend_env", return_value=dict(os.environ)), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.execute(args, validated_repo, checked_disc), 1)
        for path, before in earlier_outputs.items():
            self.assertEqual(path.read_bytes(), before)
        failed = [path for path in (repo / "build/padmint").glob("*/runs/*/record.json")
                  if json.loads(path.read_text())["status"] != "completed"]
        self.assertEqual(len(failed), 1)
        self.assertFalse((failed[0].parent / "personal.ipa").exists())


if __name__ == "__main__":
    unittest.main()
