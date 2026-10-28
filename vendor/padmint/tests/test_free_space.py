"""Games offered to players must say how much free space their first build needs; without it
doctor said "0 GB needed" (GoldenPad: "14 GB free, 0 GB needed") and make's check did nothing."""
import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from padmint import cli, manifest, ui


class FreeSpaceTests(unittest.TestCase):
    def test_a_game_offered_to_players_needs_free_space_gb(self):
        entry = {"schema_version": 1, "id": "somepad", "repo_url": "https://example.invalid/somepad",
                 "player_targets": ["ios"], "manifest": None}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(manifest, "CATALOG", Path(folder)):
            path = Path(folder) / "somepad.json"
            path.write_text(json.dumps(entry))
            with self.assertRaisesRegex(ValueError, "free_space_gb .*required for games with player_targets"):
                manifest.catalog()
            path.write_text(json.dumps(dict(entry, free_space_gb=6)))
            self.assertEqual(manifest.catalog()["somepad"]["free_space_gb"], 6)
            path.write_text(json.dumps(dict(entry, player_targets=[])))  # not offered: optional
            self.assertNotIn("free_space_gb", manifest.catalog()["somepad"])
        for game, entry in manifest.catalog().items():
            if entry.get("player_targets"):
                self.assertGreaterEqual(entry["free_space_gb"], 3, game)

    def test_target_overrides_require_advertised_targets_and_positive_whole_gb(self):
        entry = {"id": "somepad", "repo_url": "https://example.invalid/somepad",
                 "player_targets": ["ios", "macos"], "free_space_gb": 16, "manifest": None}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(manifest, "CATALOG", Path(folder)):
            path = Path(folder) / "somepad.json"
            for invalid in (None, [], {"android": 30}, {"macos": 0}, {"macos": -1},
                            {"macos": True}, {"macos": 1.5}, {"macos": "60"}):
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps(dict(entry, free_space_gb_by_target=invalid)))
                    with self.assertRaisesRegex(ValueError, "free_space_gb_by_target"):
                        manifest.catalog()
            path.write_text(json.dumps(dict(entry, free_space_gb_by_target={"macos": 12})))
            valid = manifest.catalog()["somepad"]
            self.assertEqual(cli.player_free_space(valid, "macos"), 12)
            self.assertEqual(cli.player_free_space(valid, "ios"), 16)
            self.assertEqual(cli.player_free_space(valid), 16)

    def test_mac_plan_and_player_doctor_use_the_measured_12_gb_mac_budget(self):
        # A clean KartPad 0.8.3 Mac build peaked at 9.3 GB (chrissotraidis/kartpad#489).
        recipe = {"name": "KartPad", "status": "experimental", "requirements": {"disk_gb": 16},
                  "targets": {target: {"hosts": {"macos-arm64": "experimental"}}
                              for target in ("macos", "ios", "android")}}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(ui, "release_recipe", return_value=recipe), \
                mock.patch.object(cli, "published_recipe", return_value=(recipe, "synthetic release")), \
                mock.patch.object(cli, "host_id", return_value="macos-arm64"), \
                mock.patch.object(cli.tools, "tools_root", return_value=Path(folder) / "tools"), \
                mock.patch.object(cli.shutil, "disk_usage", return_value=SimpleNamespace(free=14 * (1 << 30))):
            for target, needed, expected_code in (("macos", 12, 0), ("ios", 16, 1), ("android", 16, 1)):
                with self.subTest(target=target):
                    self.assertEqual(ui.plan("kartpad", target, "en")["space_gb"], needed)
                    stream = io.StringIO()
                    self.assertEqual(cli.doctor("kartpad", target, stream=stream), expected_code)
                    self.assertIn(f"14 GB free, {needed} GB needed", stream.getvalue())

    def test_fresh_source_checks_the_selected_target_budget_before_downloading(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(cli.tools, "tools_root", return_value=Path(folder) / "tools"), \
                mock.patch.object(cli, "source_complete", return_value=False), \
                mock.patch.object(cli.shutil, "disk_usage", return_value=SimpleNamespace(free=14 * (1 << 30))), \
                mock.patch.object(cli, "fetch_source") as fetch:
            with self.assertRaisesRegex(ValueError, "16 GB free"):
                cli.release_source("kartpad", ref="v0.8.1", platform_name="ios")
            fetch.assert_not_called()
            cli.release_source("kartpad", ref="v0.8.1", platform_name="macos")
            fetch.assert_called_once()

    def test_cached_source_can_resume_without_the_initial_free_space_budget(self):
        with mock.patch.object(cli, "source_complete", return_value=True), \
                mock.patch.object(cli, "check_free_space") as check, \
                mock.patch.object(cli, "fetch_source") as fetch:
            cli.release_source("kartpad", ref="v0.8.1", platform_name="macos")
        check.assert_not_called()
        fetch.assert_not_called()

    def test_tools_command_passes_its_selected_target_to_the_source_check(self):
        recipe = {"name": "Synthetic", "targets": {"macos": {"tools": []}}}
        with mock.patch.object(cli, "release_source", return_value=(Path("/synthetic"), "v1", {})) as source, \
                mock.patch.object(cli, "manifest_for", return_value=(recipe, "repository")), \
                mock.patch.object(cli.tools, "install"), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["tools", "kartpad", "--target", "macos"]), 0)
        source.assert_called_once_with("kartpad", platform_name="macos")


if __name__ == "__main__":
    unittest.main()
