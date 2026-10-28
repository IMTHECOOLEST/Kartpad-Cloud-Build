"""Redirected player output must preserve filenames outside the Windows code page."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from padmint import cli
from padmint.manifest import catalog


class CliEncodingTests(unittest.TestCase):
    def test_git_preserves_a_unicode_checkout_root(self):
        value = "C:/Users/player/PadMint checkout 雪"
        with mock.patch.object(cli.subprocess, "check_output", return_value=value) as command, \
                mock.patch.object(cli.tools, "executable", return_value="git"):
            self.assertEqual(cli.git(Path("checkout"), "rev-parse", "--show-toplevel"), value)
        self.assertEqual(command.call_args.kwargs["encoding"], "utf-8")

    def test_windows_success_and_error_paths_use_utf8(self):
        with tempfile.TemporaryDirectory(prefix="PadMint CLI \u96ea ") as temporary:
            recipe = Path(temporary) / "padmint.json"
            recipe.write_text(json.dumps(catalog()["kartpad"]["manifest"]), encoding="utf-8")
            for path, expected in ((recipe, 0), (recipe.with_name("missing \u96ea.json"), 1)):
                with self.subTest(exit=expected):
                    output, errors = io.BytesIO(), io.BytesIO()
                    stdout = io.TextIOWrapper(output, encoding="cp1252")
                    stderr = io.TextIOWrapper(errors, encoding="cp1252")
                    with mock.patch.object(cli, "os", SimpleNamespace(name="nt")), \
                            mock.patch.object(cli, "homebrew_on_path"), \
                            mock.patch.object(cli.sys, "stdout", stdout), \
                            mock.patch.object(cli.sys, "stderr", stderr):
                        code = cli.main(["check-manifest", str(path)])
                    stdout.flush(); stderr.flush()
                    self.assertEqual(code, expected)
                    shown_path = str(path) if expected == 0 else repr(str(path))
                    self.assertIn(shown_path.encode("utf-8"),
                                  output.getvalue() if expected == 0 else errors.getvalue())
                    self.assertEqual(errors.getvalue() if expected == 0 else output.getvalue(), b"")

    def test_custom_capture_streams_still_work(self):
        with mock.patch.object(cli, "os", SimpleNamespace(name="nt")), \
                mock.patch.object(cli.sys, "stdout", io.StringIO()), \
                mock.patch.object(cli.sys, "stderr", io.StringIO()):
            cli.configure_output()
            print("captured \u96ea")
            self.assertEqual(cli.sys.stdout.getvalue(), "captured \u96ea\n")


if __name__ == "__main__":
    unittest.main()
