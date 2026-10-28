"""After a newer version builds, older versions' build folders are emptied (KartPad #489)."""
import io
import tempfile
import unittest
from pathlib import Path

from padmint import cli

MB = 1 << 20


def write(path, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


class TidyOlderBuildsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.games = Path(self.temporary.name) / "games"
        self.old = self.games / "kartpad-v0.8.2"
        write(self.old / "build/padmint/abc/backend/pack.o", 3 * MB)
        write(self.old / "build/ios-runtime/lib.a", MB)
        # Game files a guide may have told the player to use stay.
        self.kept = write(self.old / "private/self-build/disc/files/a.szs")
        self.current = self.games / "kartpad-v0.8.3"
        self.current_build = write(self.current / "build/padmint/def/backend/pack.o")
        self.newer = write(self.games / "kartpad-v0.9.0/build/padmint/x.o")
        self.other_game = write(self.games / "kartpad-extra-v0.1.0/build/x.o")
        self.partial = write(self.games / "kartpad-v0.8.1.partial/build/x.o")

    def tearDown(self):
        self.temporary.cleanup()

    def test_only_older_versions_build_folders_are_emptied(self):
        output = io.StringIO()
        freed = cli.tidy_older_builds("kartpad", self.current, stream=output)
        self.assertGreaterEqual(freed, 4 * MB)
        self.assertEqual([p.name for p in (self.old / "build").rglob("*")], ["padmint", "runner.lock"])
        self.assertTrue(self.kept.exists())
        for untouched in (self.current_build, self.newer, self.other_game, self.partial):
            self.assertTrue(untouched.exists(), untouched)
        self.assertIn("older versions of kartpad", output.getvalue())

    def test_a_folder_another_padmint_is_using_is_left_alone(self):
        lock = self.old / "build/padmint/runner.lock"
        with cli.workspace_lock(lock):
            freed = cli.tidy_older_builds("kartpad", self.current, stream=io.StringIO())
        self.assertEqual(freed, 0)
        self.assertTrue((self.old / "build/ios-runtime/lib.a").exists())

    def test_a_branch_build_tidies_nothing(self):
        branch = self.games / "kartpad-codex_test"
        branch.mkdir()
        self.assertEqual(cli.tidy_older_builds("kartpad", branch, stream=io.StringIO()), 0)
        self.assertTrue((self.old / "build/ios-runtime/lib.a").exists())

    def test_a_folder_outside_padmints_games_folder_is_never_touched(self):
        elsewhere = Path(self.temporary.name) / "checkouts"
        old = write(elsewhere / "kartpad-v0.8.2/build/x.o")
        self.assertEqual(cli.tidy_older_builds("kartpad", elsewhere / "kartpad-v0.8.3", stream=io.StringIO()), 0)
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
