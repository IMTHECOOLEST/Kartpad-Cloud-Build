"""An interrupted download of a game's source is fetched again (padmint#8)."""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from padmint import cli


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class SourceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git("init", "-q", cwd=self.origin)
        (self.origin / "padmint.json").write_text("{}")
        (self.origin / "README.md").write_text("game")
        git("add", ".", cwd=self.origin)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one", cwd=self.origin)

    def tearDown(self):
        self.temporary.cleanup()

    def clone(self, dest, ref=None):
        git("clone", "-q", str(self.origin), str(dest), cwd=self.root)
        return 0

    def test_a_finished_download_is_reused(self):
        source = self.root / "games/game-v1"
        source.parent.mkdir()
        self.clone(source)
        self.assertTrue(cli.source_complete(source))

    def test_missing_files_or_no_git_mean_unfinished(self):
        source = self.root / "games/game-v1"
        source.parent.mkdir()
        self.assertFalse(cli.source_complete(source))
        (source / ".git").mkdir(parents=True)
        self.assertFalse(cli.source_complete(source))  # the state that caused padmint#8
        cli._remove_tree(source)
        self.clone(source)
        (source / "padmint.json").unlink()
        self.assertFalse(cli.source_complete(source))

    def test_an_unfinished_download_and_leftover_partial_are_preserved(self):
        source = self.root / "games/game-v1"
        (source / ".git").mkdir(parents=True)
        leftover = self.root / "games/game-v1.partial"
        leftover.mkdir()
        (leftover / "junk").write_text("x")
        output = io.StringIO()
        with mock.patch.object(cli, "get_game", side_effect=lambda game, dest, ref, **_: self.clone(dest)), \
                redirect_stdout(output):
            cli.fetch_source("game", source, "v1")
        self.assertTrue(cli.source_complete(source))
        self.assertFalse(leftover.exists())
        kept = list(source.parent.glob("game-v1.partial.recovered-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "junk").read_text(), "x")
        self.assertEqual(len(list(source.parent.glob("game-v1.recovered-*"))), 1)
        self.assertIn("unfinished; downloading it again", output.getvalue())
        self.assertIn("Previous files kept in", output.getvalue())

    def test_tracked_deletions_do_not_destroy_private_untracked_or_ignored_files(self):
        source = self.root / "games/game-v1"
        source.parent.mkdir()
        self.clone(source)
        (source / "README.md").unlink()
        (source / "private-save.txt").write_text("unique save")
        (source / ".git/info/exclude").write_text("build/\n")
        (source / "build").mkdir()
        (source / "build/own-input.bin").write_bytes(b"private synthetic input")
        self.assertFalse(cli.source_complete(source))
        with mock.patch.object(cli, "get_game", side_effect=lambda game, dest, ref, **_: self.clone(dest)):
            cli.fetch_source("game", source, "v1")
        kept = next(source.parent.glob("game-v1.recovered-*"))
        self.assertFalse((kept / "README.md").exists())
        self.assertEqual((kept / "private-save.txt").read_text(), "unique save")
        self.assertEqual((kept / "build/own-input.bin").read_bytes(), b"private synthetic input")
        self.assertTrue(cli.source_complete(source))
        newer = source.parent / "game-v2.0.0"
        newer.mkdir()
        cli.tidy_older_builds("game", newer, io.StringIO())
        self.assertEqual((kept / "build/own-input.bin").read_bytes(), b"private synthetic input")

    def test_failed_replacement_keeps_the_original_at_its_existing_path(self):
        source = self.root / "games/game-v1"
        source.mkdir(parents=True)
        (source / "private-save.txt").write_text("unique save")
        partial = source.with_name(source.name + ".partial")
        partial.mkdir()
        (partial / "private-partial.txt").write_text("unique partial")
        with mock.patch.object(cli, "get_game", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                cli.fetch_source("game", source, "v1")
        self.assertEqual((source / "private-save.txt").read_text(), "unique save")
        self.assertFalse(list(source.parent.glob("game-v1.recovered-*")))
        kept_partial = next(source.parent.glob("game-v1.partial.recovered-*"))
        self.assertEqual((kept_partial / "private-partial.txt").read_text(), "unique partial")

    def test_incomplete_new_clone_does_not_replace_the_original(self):
        source = self.root / "games/game-v1"
        source.mkdir(parents=True)
        (source / "private-save.txt").write_text("unique save")
        with mock.patch.object(cli, "get_game", side_effect=lambda game, dest, ref, **_: dest.mkdir()):
            with self.assertRaisesRegex(ValueError, "new source download is incomplete"):
                cli.fetch_source("game", source, "v1")
        self.assertEqual((source / "private-save.txt").read_text(), "unique save")
        self.assertTrue(source.with_name(source.name + ".partial").is_dir())

    def test_a_failed_download_leaves_only_the_partial_folder(self):
        source = self.root / "games/game-v1"
        with mock.patch.object(cli, "get_game", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                cli.fetch_source("game", source, "v1")
        self.assertFalse(source.exists())

    def test_a_computer_without_git_gets_padmints_copy_before_the_check(self):
        source = self.root / "games/game-v1"
        (source / ".git").mkdir(parents=True)
        with mock.patch.object(cli.shutil, "which", return_value=None), \
                mock.patch.object(cli.tools, "install") as install, \
                mock.patch.object(cli.tools, "executable", return_value="git"):
            self.assertFalse(cli.source_complete(source))
        install.assert_called_once_with(["git"], cli.host_id())

    def test_read_only_files_are_removed_for_real(self):
        folder = self.root / "ro"
        folder.mkdir()
        (folder / "object").write_text("x")
        os.chmod(folder / "object", 0o444)
        cli._remove_tree(folder)
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
