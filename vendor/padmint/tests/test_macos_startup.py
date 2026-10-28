import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from padmint import macos_startup


class MacStartupTests(unittest.TestCase):
    def setUp(self):
        self.platform = self.enterContext(mock.patch.object(macos_startup.sys, "platform", "darwin"))
        self.machine = self.enterContext(mock.patch.object(macos_startup.platform, "machine", return_value="x86_64"))
        self.run = self.enterContext(mock.patch.object(macos_startup.subprocess, "run"))
        self.exec = self.enterContext(mock.patch.object(macos_startup.os, "execve"))
        self.enterContext(mock.patch.dict(os.environ, {}, clear=True))

    # unittest.TestCase.enterContext is newer than Apple's Python 3.9.
    def enterContext(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def result(self, code=0, out="", error=""):
        return subprocess.CompletedProcess([], code, out, error)

    def test_native_mac_and_other_systems_do_not_restart(self):
        for system, machine in (("darwin", "arm64"), ("linux", "x86_64"), ("win32", "AMD64")):
            with self.subTest(system=system), mock.patch.object(macos_startup.sys, "platform", system):
                self.machine.return_value = machine
                macos_startup.prefer_native_macos()
        self.run.assert_not_called()
        self.exec.assert_not_called()

    def test_intel_mac_with_absent_or_zero_translation_flag_does_not_restart(self):
        for code, flag in ((0, ""), (0, "0\n")):
            with self.subTest(flag=flag):
                self.run.return_value = self.result(code, flag)
                macos_startup.prefer_native_macos()
        self.exec.assert_not_called()

    def test_rosetta_restarts_before_cli_with_same_arguments_package_and_environment(self):
        self.run.side_effect = [self.result(out="1\n"), self.result()]
        os.environ.update(PYTHONPATH="/another/package", PADMINT_HOME="/private/builds")
        arguments = ["/somewhere/padmint/__main__.py", "make", "kartpad", "macos", "--disc", "/a disc.iso"]
        cwd = Path.cwd()
        with mock.patch.object(macos_startup.sys, "argv", arguments):
            macos_startup.prefer_native_macos()
        executable, command, environ = self.exec.call_args.args
        self.assertEqual(executable, "/usr/bin/arch")
        self.assertEqual(command, ["/usr/bin/arch", "-arm64", "/usr/bin/python3", "-m", "padmint", *arguments[1:]])
        self.assertEqual(environ["PYTHONPATH"].split(os.pathsep),
                         [str(Path(macos_startup.__file__).resolve().parent.parent), "/another/package"])
        self.assertEqual(environ["PADMINT_HOME"], "/private/builds")
        self.assertEqual(environ["PADMINT_NATIVE_RESTART"], "1")
        self.assertEqual(Path.cwd(), cwd)

    def test_failed_restart_cannot_loop(self):
        self.run.return_value = self.result(out="1\n")
        os.environ["PADMINT_NATIVE_RESTART"] = "1"
        with self.assertRaisesRegex(SystemExit, "still running through Rosetta"):
            macos_startup.prefer_native_macos()
        self.exec.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_native_python_failure_stops_before_build_tools(self):
        self.run.side_effect = [self.result(out="1\n"), self.result(1, error="Bad CPU type")]
        with self.assertRaisesRegex(SystemExit, "xcode-select --install"):
            macos_startup.prefer_native_macos()
        self.exec.assert_not_called()

    def test_exec_failure_has_actionable_error(self):
        self.run.side_effect = [self.result(out="1\n"), self.result()]
        self.exec.side_effect = OSError("cannot execute")
        with self.assertRaisesRegex(SystemExit, "couldn't start Apple's Python natively"):
            macos_startup.prefer_native_macos()

    def test_detection_errors_stop_instead_of_selecting_intel_tools(self):
        for result in (self.result(1), self.result(1, error="sysctl: failure"), self.result(out="unexpected")):
            with self.subTest(result=result):
                self.run.return_value = result
                with self.assertRaisesRegex(SystemExit, "couldn't identify"):
                    macos_startup.prefer_native_macos()
        self.run.side_effect = OSError("sysctl unavailable")
        with self.assertRaisesRegex(SystemExit, "couldn't check"):
            macos_startup.prefer_native_macos()
        self.exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
