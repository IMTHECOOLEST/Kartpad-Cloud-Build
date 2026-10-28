import unittest
from unittest import mock

from padmint import cli


class HomebrewPathTests(unittest.TestCase):
    def test_inaccessible_optional_tools_do_not_prevent_startup(self):
        env = {"PATH": "/usr/bin:/bin"}
        with mock.patch.object(cli.sys, "platform", "darwin"), \
                mock.patch.object(cli.Path, "is_dir", side_effect=[PermissionError("restricted"), True]):
            cli.homebrew_on_path(env)
        self.assertEqual(env["PATH"], "/usr/bin:/bin:/usr/local/bin")

    def test_missing_homebrew_and_existing_path_are_preserved(self):
        env = {"PATH": "/usr/bin:/opt/homebrew/bin"}
        with mock.patch.object(cli.sys, "platform", "darwin"), \
                mock.patch.object(cli.Path, "is_dir", return_value=False) as check:
            cli.homebrew_on_path(env)
        self.assertEqual(env["PATH"], "/usr/bin:/opt/homebrew/bin")
        self.assertEqual(check.call_count, 1)
