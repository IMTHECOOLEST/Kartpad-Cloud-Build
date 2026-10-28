"""Reject incompatible modules before replacing a player's personal IPA."""
import plistlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

from padmint import ios_module
from fixtures import macho, write_ipa


class ModuleCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / "base.ipa"
        self.module = self.root / "game.dylib"
        self.output = self.root / "personal.ipa"
        self.module.write_bytes(macho(filetype=6))
        self.base()

    def base(self, declared="15.0", minimum=15, platform=2):
        info = {"CFBundleExecutable": "Synthetic"}
        if declared is not None:
            info["MinimumOSVersion"] = declared
        write_ipa(self.app, {"Payload/Synthetic.app/Info.plist": plistlib.dumps(info),
                             "Payload/Synthetic.app/Synthetic": macho(minimum=minimum, platform=platform)})

    def insert(self):
        with mock.patch.object(ios_module, "llvm_tool", return_value=Path("unused")):
            return ios_module.insert(self.app, self.module, "game.dylib", self.output,
                                     self.root, self.root / "work")

    def test_equal_and_older_modules_preserve_the_entire_base(self):
        for minimum in (15, 16):
            self.base(declared="16", minimum=16)
            self.module.write_bytes(macho(filetype=6, minimum=minimum))
            with self.subTest(minimum=minimum), mock.patch.object(ios_module.subprocess, "run"):
                self.insert()
                with zipfile.ZipFile(self.app) as base, zipfile.ZipFile(self.output) as final:
                    for item in base.infolist():
                        self.assertEqual(final.read(item.filename), base.read(item))
                    self.assertEqual(final.read("Payload/Synthetic.app/game.dylib"), self.module.read_bytes())

    def test_rejects_newer_module_before_running_tools_or_replacing_output(self):
        self.module.write_bytes(macho(filetype=6, minimum=16))
        self.output.write_bytes(b"previous personal build")
        original = self.app.read_bytes(), self.module.read_bytes()
        with mock.patch.object(ios_module.subprocess, "run") as run, \
                self.assertRaisesRegex(ios_module.ModuleError, "requires iOS 16.*supports iOS 15"):
            self.insert()
        run.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"previous personal build")
        self.assertEqual(original, (self.app.read_bytes(), self.module.read_bytes()))

    def test_finalized_module_is_checked_again(self):
        def corrupt(command, **_kwargs):
            if "-id" in command:
                Path(command[-1]).write_bytes(macho(filetype=6, minimum=17))
        self.output.write_bytes(b"previous personal build")
        with mock.patch.object(ios_module.subprocess, "run", side_effect=corrupt), \
                self.assertRaisesRegex(ios_module.ModuleError, "requires iOS 17"):
            self.insert()
        self.assertEqual(self.output.read_bytes(), b"previous personal build")
        self.assertEqual(ios_module.linked(self.module)["minimum_os"], "15.0.0")

    def test_rejects_wrong_architecture_type_platform_and_malformed_commands(self):
        good = macho(filetype=6)
        def integer(offset, value):
            data = bytearray(good)
            struct.pack_into("<I", data, offset, value)
            return bytes(data)
        duplicate = bytearray(good[:56] + good[32:56])
        struct.pack_into("<2I", duplicate, 16, 2, 48)
        cases = {
            "x64": integer(4, 0x1000007), "arm64e": integer(8, 2),
            "executable": macho(), "macOS": macho(filetype=6, platform=1),
            "simulator": macho(filetype=6, platform=7), "tvOS": macho(filetype=6, platform=3),
            "truncated header": good[:20], "truncated table": good[:50],
            "short command": integer(36, 4), "oversized command": integer(36, 1000),
            "oversized table": integer(20, 0xFFFFFFFF), "too many commands": integer(16, 200),
            "short build command": integer(36, 16), "truncated tool list": integer(52, 2),
            "undeclared bytes": integer(20, 32), "duplicate platform": bytes(duplicate),
            "missing platform": integer(16, 0), "missing SDK": integer(48, 0),
            "missing minimum": integer(44, 0),
        }
        for name, data in cases.items():
            with self.subTest(name=name), mock.patch.object(ios_module.subprocess, "run") as run:
                self.module.write_bytes(data)
                self.output.write_bytes(b"previous personal build")
                with self.assertRaises(ios_module.ModuleError):
                    self.insert()
                run.assert_not_called()
                self.assertEqual(self.output.read_bytes(), b"previous personal build")

    def test_app_floor_uses_plist_or_linked_minimum_and_rejects_false_claims(self):
        for declared, minimum, expected in ((None, 15, "15.0.0"), ("16.1", 15, "16.1.0"),
                                             ("15.0.1", 15, "15.0.1")):
            with self.subTest(declared=declared):
                self.base(declared, minimum)
                self.assertEqual(ios_module.app_minimum(self.app), expected)
        for declared in ("14", "bad", "15;set(X)", "", "0", "15.256", 15):
            with self.subTest(declared=declared):
                self.base(declared)
                with self.assertRaises(ios_module.ModuleError):
                    ios_module.app_minimum(self.app)
        self.base(platform=3)
        with self.assertRaisesRegex(ios_module.ModuleError, "physical device"):
            ios_module.app_minimum(self.app)

    def test_prepare_keeps_older_app_support_and_existing_default_for_newer_apps(self):
        for minimum, target in ((15, "15.0.0"), (16, "16.0"), (17, "16.0")):
            self.base(str(minimum), minimum)
            sdk = self.root / f"sdk-{minimum}"
            sdk.mkdir()
            with self.subTest(minimum=minimum), \
                    mock.patch.object(ios_module, "source_roots", return_value={}), \
                    mock.patch.object(ios_module, "llvm_root", return_value=self.root), \
                    mock.patch.object(ios_module, "assemble", return_value=sdk), \
                    mock.patch.object(ios_module, "app_thread_locals", return_value=[]), \
                    mock.patch.object(ios_module, "llvm_tool", side_effect=lambda root, name: root / name):
                prepared = ios_module.prepare(sdk, self.app)
            text = prepared["toolchain"].read_text()
            self.assertIn(f"CMAKE_OSX_DEPLOYMENT_TARGET {target} CACHE", text)
            for language in ("C", "CXX", "ASM"):
                self.assertIn(f"CMAKE_{language}_COMPILER_TARGET arm64-apple-ios{target})", text)


if __name__ == "__main__":
    unittest.main()
