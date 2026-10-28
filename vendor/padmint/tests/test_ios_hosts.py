"""Exact iOS build hosts never imply support for another architecture or system."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from padmint import cli, manifest, ui


class IosHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.entry = {"id": "testpad", "name": "TestPad", "game": "Synthetic test game",
                      "repo_url": "https://example.invalid/testpad", "player_targets": ["ios"],
                      "player_game_file": "in-app", "free_space_gb": 1,
                      "ios_hosts": ["macos-arm64", "windows-arm64"]}
        patcher = mock.patch.object(manifest, "CATALOG", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.save()

    def save(self):
        (self.folder / "testpad.json").write_text(json.dumps(self.entry))

    def games(self, host, phone=False):
        with mock.patch.object(cli, "host_id", return_value=host), \
                mock.patch.object(cli, "on_android", return_value=phone):
            return cli.player_games()

    def data(self, host, lang="en", phone=False):
        with mock.patch.object(cli, "host_id", return_value=host), \
                mock.patch.object(cli, "on_android", return_value=phone), \
                mock.patch.object(cli, "player_folder", return_value=self.folder), \
                mock.patch.object(ui, "library", return_value=[]), \
                mock.patch.object(ui.installed, "snapshot", return_value={}):
            return ui.player_data(lang)

    def test_exact_hosts_do_not_enable_other_windows_architecture_or_linux(self):
        for host in manifest.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(bool(self.games(host)), host in self.entry["ios_hosts"])

    def test_exact_list_overrides_both_legacy_flags(self):
        self.entry.update(ios_hosts=["windows-arm64"], ios_off_mac=True, ios_intel_mac=True)
        self.save()
        self.assertEqual(manifest.ios_build_hosts(self.entry), ["windows-arm64"])
        for host in manifest.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(bool(self.games(host)), host == "windows-arm64")

    def test_omitting_exact_hosts_preserves_the_legacy_policy(self):
        self.entry.pop("ios_hosts")
        self.save()
        self.assertEqual(manifest.ios_build_hosts(self.entry), ["macos-arm64"])
        self.assertTrue(self.games("macos-arm64"))
        self.assertFalse(self.games("windows-arm64"))
        self.entry.update(ios_off_mac=True, ios_intel_mac=True)
        self.save()
        self.assertEqual(set(manifest.ios_build_hosts(self.entry)), manifest.HOSTS)
        for host in manifest.HOSTS:
            with self.subTest(host=host):
                self.assertTrue(self.games(host))

    def test_android_phone_still_cannot_build_ios(self):
        self.entry.update(ios_hosts=["linux-arm64"], player_targets=["android", "ios"])
        self.save()
        self.assertEqual(self.games("linux-arm64", phone=True), [("testpad", "TestPad", ["android"])])
        data = self.data("linux-arm64", phone=True)
        self.assertEqual(data["games"][0]["held"], [])
        self.assertEqual(data["host_name"], "Linux ARM64")

    def test_an_unrecognised_host_still_opens_the_page(self):
        self.assertEqual(self.data("android", phone=True)["host_name"], "android")

    def test_catalog_requires_unique_known_hosts_for_an_ios_target(self):
        for hosts in ([], "windows-arm64", ["windows-arm64", "windows-arm64"],
                      ["windows"], [None], [{"host": "windows-arm64"}]):
            with self.subTest(hosts=hosts):
                self.entry["ios_hosts"] = hosts
                self.save()
                with self.assertRaisesRegex(ValueError, "ios_hosts"):
                    manifest.catalog()
        self.entry.update(ios_hosts=["windows-arm64"], player_targets=["android"])
        self.save()
        with self.assertRaisesRegex(ValueError, "ios_hosts"):
            manifest.catalog()

    def test_unavailable_card_and_library_name_the_actual_build_computers(self):
        for host in ("windows-x86_64", "linux-x86_64", "macos-x86_64"):
            with self.subTest(host=host):
                data = self.data(host)
                self.assertEqual(data["games"], [])
                card = data["later"][0]
                self.assertEqual(card["tag"], "Other build computer")
                self.assertIn("Mac with Apple Silicon, Windows ARM64", card["text"])
                self.assertNotIn("Windows x64", card["text"])
                self.assertEqual(data["text"]["needs_mac_head"], "Other build computer")
                self.assertEqual(data["text"]["later_head"], "Other build computer")
                self.assertNotIn("built on a Mac", data["text"]["here_other"])
        self.assertEqual(self.data("windows-arm64")["games"][0]["platforms"][0]["id"], "ios")

    def test_other_available_targets_keep_disabled_ios_choice_with_explanation(self):
        self.entry["player_targets"] = ["android", "ios"]
        self.save()
        game = self.data("windows-x86_64")["games"][0]
        self.assertEqual([p["id"] for p in game["platforms"]], ["android"])
        self.assertEqual([p["id"] for p in game["held"]], ["ios"])
        self.assertIn("Windows ARM64", game["held"][0]["text"])
        self.assertEqual(game["held"][0]["tag"], "Other build computer")

    def test_exact_host_help_is_translated_and_legacy_cards_are_unchanged(self):
        for lang, word in (("es", "Crea"), ("pt", "Crie")):
            with self.subTest(lang=lang):
                card = self.data("linux-x86_64", lang)["later"][0]
                self.assertTrue(card["text"].startswith(word))
                self.assertIn("Windows ARM64", card["text"])
        self.entry.pop("ios_hosts")
        self.save()
        data = self.data("windows-x86_64")
        self.assertEqual(data["later"][0]["tag"], "Needs Apple Silicon Mac")
        self.assertEqual(data["text"]["needs_mac_head"], "Apple Silicon Mac")

    def test_http_plan_and_build_refuse_other_hosts_but_accept_the_allowed_host(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler("test-token", ui.Builds()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        routes = (("/api/plan?game=testpad&platform=ios", None),
                  ("/api/make", json.dumps({"game": "testpad", "platform": "ios"}).encode()))
        with mock.patch.object(cli, "on_android", return_value=False), \
                mock.patch.object(ui, "plan", return_value={"ok": True}) as plan, \
                mock.patch.object(ui.Builds, "start") as build:
            for host in ("windows-x86_64", "linux-arm64", "windows-arm64"):
                with mock.patch.object(cli, "host_id", return_value=host):
                    for route, body in routes:
                        request = urllib.request.Request(base + route, data=body,
                                                         headers={"X-PadMint-Token": "test-token"})
                        with self.subTest(host=host, route=route):
                            if host == "windows-arm64":
                                with urllib.request.urlopen(request, timeout=5) as response:
                                    self.assertEqual(response.status, 200)
                            else:
                                with self.assertRaises(urllib.error.HTTPError) as error:
                                    urllib.request.urlopen(request, timeout=5)
                                self.assertEqual(error.exception.code, 400)
                                error.exception.close()
                                plan.assert_not_called()
                                build.assert_not_called()
        plan.assert_called_once()
        build.assert_called_once()

    def test_terminal_explains_exact_hosts_even_when_no_game_is_available_here(self):
        self.entry["ios_hosts"] = ["windows-arm64"]
        self.save()
        stream = io.StringIO()
        with mock.patch.object(cli, "host_id", return_value="linux-x86_64"), \
                mock.patch.object(cli, "on_android", return_value=False), \
                mock.patch.dict("os.environ", {"PADMINT_LANG": "en"}), \
                mock.patch.object(cli, "make") as make:
            with self.assertRaisesRegex(ValueError, "no game"):
                cli.start(lambda _prompt: self.fail("must not ask to build here"), stream)
        self.assertIn("Build TestPad for iPhone or iPad on: Windows ARM64.", stream.getvalue())
        self.assertNotIn("Apple Silicon", stream.getvalue())
        make.assert_not_called()

    def test_terminal_keeps_legacy_mac_group_separate_and_builds_the_available_game(self):
        self.entry["ios_hosts"] = ["windows-arm64"]
        self.save()
        for game, target in (("otherpad", "android"), ("macpad", "ios")):
            data = dict(self.entry, id=game, name=game.title(), player_targets=[target])
            data.pop("ios_hosts")
            (self.folder / f"{game}.json").write_text(json.dumps(data))
        stream = io.StringIO()
        with mock.patch.object(cli, "host_id", return_value="linux-x86_64"), \
                mock.patch.object(cli, "on_android", return_value=False), \
                mock.patch.dict("os.environ", {"PADMINT_LANG": "en"}), \
                mock.patch.object(cli, "player_folder", return_value=self.folder), \
                mock.patch.object(cli, "make", return_value=1) as make:
            self.assertEqual(cli.start(lambda _prompt: self.fail("one available choice needs no question"), stream), 1)
            self.assertIn(cli.t("elsewhere", names="Macpad"), stream.getvalue())
            self.assertNotIn(cli.t("elsewhere", names="Macpad, TestPad"), stream.getvalue())
        self.assertIn("Build TestPad for iPhone or iPad on: Windows ARM64.", stream.getvalue())
        self.assertEqual(make.call_args.args[:3], ("otherpad", "android", None))


if __name__ == "__main__":
    unittest.main()
