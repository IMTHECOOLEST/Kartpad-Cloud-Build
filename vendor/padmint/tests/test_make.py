import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from padmint import cli
from padmint.cli import published_app


class PublishedAppTests(unittest.TestCase):
    def test_download_is_checked_against_sha256sums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Game-v1.0.0-android.apk"
            app.write_bytes(b"empty app")
            sums = root / "SHA256SUMS"
            sums.write_text(f"{hashlib.sha256(b'empty app').hexdigest()}  {app.name}\n")
            assets = {app.name: app.as_uri(), "SHA256SUMS": sums.as_uri()}
            path = published_app(app.name, assets, root / "cache")
            self.assertEqual(path.read_bytes(), b"empty app")
            app.write_bytes(b"tampered")
            (root / "cache" / app.name).unlink()
            with self.assertRaisesRegex(ValueError, "does not match"):
                published_app(app.name, assets, root / "cache")
            self.assertFalse((root / "cache" / app.name).exists())


class MakeTests(unittest.TestCase):
    def test_preselected_release_is_used_for_recipe_source_and_base_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "version.json").write_text(json.dumps({"version": "1.2.3"}))
            recipe = {"name": "Game", "inputs": [{"when": "in-app"}], "targets": {"ios": {
                "hosts": {cli.host_id(): "experimental"}, "steps": [],
                "published_app": "Game-v{version}.ipa"}}}
            release = ("v1.2.3", {"Game-v1.2.3.ipa": "https://example.invalid/app"})
            with mock.patch.object(cli, "catalog", return_value={"game": {"repo_url": "https://x/game"}}), \
                    mock.patch.object(cli, "latest_release") as latest, \
                    mock.patch.object(cli, "published_recipe", return_value=(recipe, "release")) as published, \
                    mock.patch.object(cli, "release_source", return_value=(source, release[0], release[1])) as fetch, \
                    mock.patch.object(cli, "manifest_for", return_value=(recipe, "repository")), \
                    mock.patch.object(cli.tools, "tools_root", return_value=root / "home/tools"), \
                    mock.patch.object(cli.tools, "install"), \
                    mock.patch.object(cli, "published_app", return_value=root / "app.ipa") as app, \
                    mock.patch.object(cli, "finish_submodules"), \
                    mock.patch.object(cli, "git", return_value="0" * 40), \
                    mock.patch.object(cli, "execute", return_value=23):
                self.assertEqual(cli.make("game", "ios", None, root / "out", release=release), 23)
            latest.assert_not_called()
            published.assert_called_once_with("game", release=release)
            fetch.assert_called_once_with("game", None, release=release, platform_name="ios")
            app.assert_called_once_with("Game-v1.2.3.ipa", release[1], root / "home/apps/game")

    def test_source_ref_and_release_cannot_conflict(self):
        with mock.patch.object(cli, "catalog", return_value={"game": {}}):
            with self.assertRaisesRegex(ValueError, "not both"):
                cli.make("game", "ios", None, Path("out"), ref="main", release=("v1", {}))

    def test_player_path_names_the_published_app_and_result_by_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "home/games/game-v1.2.3"
            source.mkdir(parents=True)
            (source / "version.json").write_text(json.dumps({"version": "1.2.3", "build": 7}))
            manifest = {"name": "Game", "inputs": [{"type": "disc"}], "targets": {"android": {
                "published_app": "Game-v{version}-android.apk", "steps": [], "tools": [],
                "hosts": {cli.host_id(): "experimental"}}}}
            built = root / "built.so"
            built.write_bytes(b"pack")

            def execute(args, _repo, _disc):
                args.output_path = built
                return 0

            with mock.patch.object(cli, "catalog", return_value={"game": {"repo_url": "https://x/game"}}), \
                    mock.patch.object(cli.tools, "tools_root", return_value=root / "home/tools"), \
                    mock.patch.object(cli.tools, "install"), \
                    mock.patch.object(cli, "latest_release", return_value=("v1.2.3", {})), \
                    mock.patch.object(cli, "published_recipe", return_value=(manifest, "release")), \
                    mock.patch.object(cli, "source_complete", return_value=True), \
                    mock.patch.object(cli, "manifest_for", return_value=(manifest, "repository")), \
                    mock.patch.object(cli, "git", return_value="0" * 40), \
                    mock.patch.object(cli, "published_app", return_value=root / "app.apk") as fetch, \
                    mock.patch.object(cli, "execute", side_effect=execute):
                code = cli.make("game", "android", root / "disc.iso", root / "out")
            self.assertEqual(code, 0)
            self.assertEqual(fetch.call_args.args[0], "Game-v1.2.3-android.apk")
            self.assertTrue((root / "out/Game-v1.2.3-android-personal.so").is_file())
            manifest["inputs"] = [{"type": "rom", "when": "in-app"}]
            with mock.patch.object(cli, "catalog", return_value={"game": {"repo_url": "https://x/game"}}), \
                    mock.patch.object(cli.tools, "tools_root", return_value=root / "home/tools"), \
                    mock.patch.object(cli.tools, "install"), \
                    mock.patch.object(cli, "latest_release", return_value=("v1.2.3", {})), \
                    mock.patch.object(cli, "published_recipe", return_value=(manifest, "release")), \
                    mock.patch.object(cli, "source_complete", return_value=True), \
                    mock.patch.object(cli, "manifest_for", return_value=(manifest, "repository")), \
                    mock.patch.object(cli, "git", return_value="0" * 40), \
                    mock.patch.object(cli, "published_app", return_value=root / "app.apk"), \
                    mock.patch.object(cli, "execute", side_effect=execute) as run:
                self.assertEqual(cli.make("game", "android", None, root / "out"), 0)
            self.assertIsNone(run.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
