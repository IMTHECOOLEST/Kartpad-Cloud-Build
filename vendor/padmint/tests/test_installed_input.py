"""Installed private inputs are local builds without a selected disc image."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from padmint import cli, game_file, manifest, ui


def recipe():
    return {"schema_version": 2, "id": "installedpad", "name": "InstalledPad", "game": "Synthetic",
            "kind": "clean-engine", "status": "experimental",
            "inputs": [{"type": "installed-game", "when": "build", "selection": "installed"}],
            "targets": {"ios": {"hosts": {"macos-arm64": "experimental"}, "command": ["synthetic"],
                                "output": "ipa", "check": "ipa"}},
            "publication": {"public_binaries": False}}


def entry():
    return {"id": "installedpad", "name": "InstalledPad", "repo_url": "https://example.invalid/game",
            "player_targets": ["ios"], "player_game_file": "installed", "free_space_gb": 1}


class InstalledInputTests(unittest.TestCase):
    def test_installed_is_private_but_does_not_select_a_file(self):
        data = manifest.validate_manifest(recipe())
        self.assertTrue(manifest.needs_build_input(data))
        self.assertEqual(manifest.selected_inputs(data), [])
        self.assertEqual(manifest.player_input_mode(data), "installed")
        stale = dict(entry(), github_build=True, player_game_file="in-app")
        with self.assertRaisesRegex(ValueError, "keep this build local"):
            manifest.validate_github_build(stale, data)

    def test_default_file_and_in_app_modes_are_unchanged(self):
        data = recipe()
        data["schema_version"] = 1
        data["inputs"] = [{"type": "rom"}]
        manifest.validate_manifest(data)
        self.assertEqual(manifest.player_input_mode(data), "build")
        self.assertEqual(manifest.selected_inputs(data), data["inputs"])
        data["inputs"][0]["when"] = "in-app"
        self.assertEqual(manifest.player_input_mode(data), "in-app")
        self.assertFalse(manifest.needs_build_input(data))

    def test_installed_selection_requires_a_schema_old_padmint_rejects(self):
        data = recipe()
        data["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "requires schema_version 2"):
            manifest.validate_manifest(data)
        data["schema_version"] = 3
        with self.assertRaises(manifest.NeedsNewerPadMint):
            manifest.validate_manifest(data)

    def test_mixed_installed_and_file_inputs_still_need_the_selected_file_hash(self):
        data = recipe()
        data["inputs"].append({"type": "rom", "accepted_sha256": ["a" * 64]})
        self.assertEqual(manifest.player_input_mode(data), "build")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.rom"
            path.write_bytes(b"synthetic")
            with self.assertRaisesRegex(ValueError, "does not match"):
                game_file.check_file_hash(data, path)

    def test_selection_is_valid_only_for_build_inputs(self):
        for selection, when in ((True, "build"), ("folder", "build"), ("installed", "in-app")):
            data = recipe()
            data["inputs"][0].update(selection=selection, when=when)
            with self.subTest(selection=selection, when=when), self.assertRaises(ValueError):
                manifest.validate_manifest(data)

    def test_catalog_accepts_installed_input_guidance(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "installedpad.json").write_text(json.dumps(entry()))
            with mock.patch.object(manifest, "CATALOG", Path(folder)):
                self.assertEqual(manifest.catalog()["installedpad"]["player_game_file"], "installed")

    def test_page_and_terminal_explain_local_installation_without_asking_for_disc(self):
        data = {"installedpad": entry()}
        with mock.patch.object(cli, "catalog", return_value=data), mock.patch.object(ui, "catalog", return_value=data), \
                mock.patch.object(cli, "host_id", return_value="macos-arm64"), \
                mock.patch.object(cli, "on_android", return_value=False), \
                mock.patch.dict("os.environ", {"PADMINT_LANG": "en"}), \
                mock.patch.object(cli, "make", return_value=0) as make:
            game = ui.player_data("en")["games"][0]
            self.assertFalse(game["needs_file"])
            self.assertIn("local installation", game["input_note"])
            self.assertIn("installed game files", game["build_note"])
            self.assertNotIn("afterwards", game["build_note"])
            stream = io.StringIO()
            self.assertEqual(cli.start(lambda prompt: self.fail("unexpected prompt: " + prompt), stream), 0)
            self.assertIn("local installation", stream.getvalue())
            self.assertNotIn("inside the app", stream.getvalue())
            self.assertEqual(make.call_args.args[:3], ("installedpad", "ios", None))
        steps, _note, _guide = cli.next_step_text(entry(), "ios", Path("personal.ipa"), "en")
        self.assertFalse(any("add your own game file" in step for step in steps))

    def test_make_accepts_installed_inputs_without_disc_and_rejects_unused_disc(self):
        data = recipe()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(cli, "catalog", return_value={"installedpad": entry()}), \
                    mock.patch.object(cli, "host_id", return_value="macos-arm64"), \
                    mock.patch.object(cli, "release_source", return_value=(root, "test-ref", {})), \
                    mock.patch.object(cli, "manifest_for", return_value=(data, "source")), \
                    mock.patch.object(cli.tools, "tools_root", return_value=root / "home/tools"), \
                    mock.patch.object(cli.tools, "install") as install, \
                    mock.patch.object(cli, "finish_submodules"), \
                    mock.patch.object(cli, "git", return_value="0" * 40), \
                    mock.patch.object(cli, "execute", return_value=23) as execute:
                self.assertEqual(cli._make("installedpad", "ios", None, root / "out", ref="test-ref"), 23)
                self.assertIsNone(execute.call_args.args[2])
                install.reset_mock()
                execute.reset_mock()
                with self.assertRaisesRegex(ValueError, "--disc is not used"):
                    cli._make("installedpad", "ios", root / "unused", root / "out", ref="test-ref")
                install.assert_not_called()
                execute.assert_not_called()

    def test_real_local_backend_reads_installed_input_without_disc(self):
        with tempfile.TemporaryDirectory(prefix="PadMint installed input ") as folder:
            root = Path(folder).resolve()
            repo = root / "backend"
            repo.mkdir()
            (root / "installed.data").write_bytes(b"synthetic installed game")
            (repo / "builder.py").write_text(
                "from pathlib import Path\nimport sys, zipfile\n"
                "data = (Path(__file__).resolve().parent.parent / 'installed.data').read_bytes()\n"
                "with zipfile.ZipFile(sys.argv[1], 'w') as z: z.writestr('synthetic.txt', data)\n")
            data = recipe()
            data["targets"] = {"android": {"hosts": {manifest.host_id(): "experimental"},
                "command": ["{python}", "{repo}/builder.py", "{output}"], "output": "zip", "check": "none"}}
            (repo / "padmint.json").write_text(json.dumps(data))
            (repo / ".gitignore").write_text("build/\n")
            for command in (["init", "-q"], ["config", "user.email", "test@example.invalid"],
                            ["config", "user.name", "Test"], ["add", "."], ["commit", "-qm", "synthetic"]):
                subprocess.run(["git", "-C", str(repo), *command], check=True)
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            args = argparse.Namespace(game="installedpad", repo=repo, disc=None, revision=revision,
                                      target="android", source_only=False, no_mods=False, jobs=1)
            selected_repo, disc = cli.validate(args)
            self.assertIsNone(disc)
            self.assertEqual(cli.execute(args, selected_repo, disc), 0)
            args.disc = root / "installed.data"
            with self.assertRaisesRegex(ValueError, "--disc is not used"):
                cli.validate(args)


if __name__ == "__main__":
    unittest.main()
