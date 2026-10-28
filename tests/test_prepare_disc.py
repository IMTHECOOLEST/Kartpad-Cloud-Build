"""Tests for scripts/prepare-disc.py. Every zip here holds dummy bytes, never disc data.

Run:  python3 -m unittest discover -s tests -v
"""
import hashlib
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_disc", str(ROOT / "scripts" / "prepare-disc.py"))
pd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pd)

SECRET_URL = "https://example.invalid/private/token-abc123/disc.zip"
SECRET_HEADER = "Authorization: Bearer s3cr3t-token-xyz"

# Stand-ins for curl, wit, install-wit.sh and nodtool. They run for real as processes.
FAKE_CURL = """#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
config = sys.stdin.read()
open(os.environ["FAKE_CURL_ARGS"], "w").write(" ".join(args) + "\\n---\\n" + config)
if os.environ.get("FAKE_CURL_FAIL"):
    sys.stderr.write("curl: (22) The requested URL returned error: 403\\n")
    sys.exit(22)
shutil.copyfile(os.environ["FAKE_CURL_SOURCE"], args[args.index("--output") + 1])
"""
FAKE_INSTALL_WIT = """#!/bin/sh
if [ -n "$FAKE_WIT_MISSING" ]; then echo "install-wit: not available" >&2; exit 3; fi
mkdir -p "$1/bin"
cp "$FAKE_WIT_SOURCE" "$1/bin/wit"
chmod 755 "$1/bin/wit"
"""
# wit copy DIR --dest FILE --iso: succeeds only for a folder that has sys/main.dol directly
# (or when FAKE_WIT_ANY is set), and writes a marker the fake nodtool reads.
FAKE_WIT = """#!/bin/sh
case "$1" in
  --version) echo "Wiimms ISO Tools (fake) v0"; exit 0 ;;
esac
if [ "$1" = copy ] && [ "$2" = "--help" ]; then
  if [ -n "$FAKE_WIT_NO_DIR_HELP" ]; then echo "copy images"; else echo "copy images or a FST directory"; fi
  exit 0
fi
if [ "$1" = copy ]; then
  src="$2"; dest="$4"
  echo "$src" >> "$FAKE_WIT_CALLS"
  if [ -n "$FAKE_WIT_ANY" ] || [ -f "$src/sys/main.dol" ]; then
    printf '%s' "$FAKE_WIT_IMAGE_MARKER" > "$dest"
    exit 0
  fi
  echo "wit: not a valid FST" >&2
  exit 1
fi
exit 1
"""
FAKE_NODTOOL = """#!/bin/sh
if [ "$1" = info ]; then
  case "$(cat "$2")" in
    GOOD) printf 'Title: dummy\\nGame ID: RMCP01\\nDisc 0, Revision 0\\n' ;;
    REV1) printf 'Game ID: RMCP01\\nDisc 0, Revision 1\\n' ;;
    *) exit 1 ;;
  esac
  exit 0
fi
exit 1
"""


def make_zip(path, entries):
    """entries: {name: bytes | ('symlink', target) | ('mode', bytes, mode)}"""
    with zipfile.ZipFile(str(path), "w") as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            if isinstance(value, tuple) and value[0] == "symlink":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, value[1])
            elif isinstance(value, tuple) and value[0] == "mode":
                info.external_attr = value[2] << 16
                archive.writestr(info, value[1])
            elif name.endswith("/"):
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, b"")
            else:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, value)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("curl", FAKE_CURL), ("install-wit.sh", FAKE_INSTALL_WIT),
                           ("wit", FAKE_WIT), ("nodtool", FAKE_NODTOOL)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.env_file = self.tmp / "github_env"
        self.zip = self.tmp / "source.zip"
        self.saved = dict(os.environ)
        os.environ.update({
            "DISC_ZIP_URL": SECRET_URL,
            "FAKE_CURL_SOURCE": str(self.zip),
            "FAKE_CURL_ARGS": str(self.tmp / "curl-args.txt"),
            "FAKE_WIT_SOURCE": str(self.bin / "wit"),
            "FAKE_WIT_CALLS": str(self.tmp / "wit-calls.txt"),
            "FAKE_WIT_IMAGE_MARKER": "GOOD",
        })
        for name in ("DISC_ZIP_SHA256", "DISC_ZIP_AUTH_HEADER", "FAKE_CURL_FAIL", "FAKE_WIT_MISSING",
                     "FAKE_WIT_ANY", "FAKE_WIT_NO_DIR_HELP", "GITHUB_ENV", "PADMINT_HOME"):
            os.environ.pop(name, None)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def run_main(self, *extra):
        argv = ["--work-dir", str(self.tmp / "work"), "--iso-path", str(self.tmp / "mkw.iso"),
                "--wit-dir", str(self.tmp / "wit"), "--install-wit", str(self.bin / "install-wit.sh"),
                "--nodtool", str(self.bin / "nodtool"), "--curl", str(self.bin / "curl"),
                "--github-env", str(self.env_file)] + list(extra)
        out = io.StringIO()
        with redirect_stdout(out):
            code = pd.main(argv)
        return code, out.getvalue()

    def exported(self):
        values = {}
        if self.env_file.exists():
            for line in self.env_file.read_text().splitlines():
                name, _, value = line.partition("=")
                values[name] = value
        return values


class ImageCases(Base):
    def test_one_image(self):
        make_zip(self.zip, {"Mario Kart Wii.iso": b"dummy"})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        env = self.exported()
        self.assertEqual(env["DISC_SOURCE_KIND"], "image")
        disc = Path(env["DISC_FILE"])
        self.assertTrue(disc.is_file())
        self.assertEqual(disc.read_bytes(), b"dummy")
        self.assertEqual(disc.name, "mkw.iso")
        self.assertFalse((self.tmp / "work" / "disc.zip").exists())

    def test_extensions_in_any_letter_case(self):
        for ext in ("ISO", "Wbfs", "rVz", "WIA", "gcz", "CISO"):
            with self.subTest(ext=ext):
                self.env_file.unlink() if self.env_file.exists() else None
                make_zip(self.zip, {"disc." + ext: b"dummy"})
                code, out = self.run_main()
                self.assertEqual(code, 0, out)
                self.assertTrue(self.exported()["DISC_FILE"].endswith("mkw." + ext.lower()))

    def test_nested_wrapper_and_macos_junk(self):
        make_zip(self.zip, {"Wrapper/": b"", "Wrapper/game.rvz": b"dummy", "Wrapper/.DS_Store": b"x",
                            "__MACOSX/Wrapper/._game.rvz": b"x", "__MACOSX/": b""})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.exported()["DISC_SOURCE_KIND"], "image")

    def test_apple_double_file_is_not_a_second_image(self):
        make_zip(self.zip, {"game.iso": b"dummy", "._game.iso": b"x"})
        code, out = self.run_main()
        self.assertEqual(code, 0, out)

    def test_multiple_images_stop(self):
        make_zip(self.zip, {"a.iso": b"1", "b.wbfs": b"2"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("2 disc images", out)
        self.assertFalse((self.tmp / "work").exists())

    def test_two_wrapper_levels_are_not_allowed(self):
        make_zip(self.zip, {"a/b/game.iso": b"dummy"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("neither one disc image", out)

    def test_wrong_content_lists_only_top_level_names(self):
        make_zip(self.zip, {"readme.txt": b"hello", "notes/secret-name.txt": b"hello"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("readme.txt", out)
        self.assertIn("notes/", out)
        self.assertNotIn("secret-name.txt", out)
        self.assertNotIn("hello", out)

    def test_empty_zip(self):
        make_zip(self.zip, {})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("empty", out)

    def test_not_a_zip(self):
        self.zip.write_bytes(b"<html>sign in</html>")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("not a zip", out)


class Security(Base):
    def test_zip_slip_dotdot(self):
        make_zip(self.zip, {"../evil.iso": b"x"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("zip-slip", out)
        self.assertFalse((self.tmp / "evil.iso").exists())

    def test_zip_slip_nested_and_backslash(self):
        for name in ("a/../../evil.iso", "..\\evil.iso", "a\\..\\..\\evil.iso"):
            with self.subTest(name=name):
                make_zip(self.zip, {name: b"x"})
                code, out = self.run_main()
                self.assertEqual(code, 1, out)
                self.assertFalse((self.tmp / "evil.iso").exists())

    def test_absolute_paths(self):
        for name in ("/etc/evil.iso", "C:/evil.iso"):
            with self.subTest(name=name):
                make_zip(self.zip, {name: b"x"})
                code, out = self.run_main()
                self.assertEqual(code, 1, out)
                self.assertIn("absolute path", out)

    def test_symlink_rejected(self):
        make_zip(self.zip, {"game.iso": b"dummy", "link": ("symlink", "/etc/passwd")})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("symbolic link", out)

    def test_special_file_rejected(self):
        make_zip(self.zip, {"pipe": ("mode", b"", stat.S_IFIFO | 0o644), "game.iso": b"d"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("special file", out)

    def test_case_insensitive_duplicates_rejected(self):
        make_zip(self.zip, {"Game.iso": b"1", "game.ISO": b"2"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("same path twice", out)

    def test_file_and_folder_clash_rejected(self):
        make_zip(self.zip, {"a": b"1", "a/b.iso": b"2"})
        code, out = self.run_main()
        self.assertEqual(code, 1)

    def test_total_size_cap(self):
        make_zip(self.zip, {"game.iso": b"x" * 200000})
        code, out = self.run_main("--max-bytes", "1000")
        self.assertEqual(code, 1)
        self.assertIn("more than the allowed", out)

    def test_nothing_is_written_with_unsafe_member_later_in_zip(self):
        make_zip(self.zip, {"game.iso": b"dummy", "../evil": b"x"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "work").exists())


class Secrets(Base):
    def test_sha256_match_and_mismatch(self):
        make_zip(self.zip, {"game.iso": b"dummy"})
        digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        os.environ["DISC_ZIP_SHA256"] = digest.upper()
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        os.environ["DISC_ZIP_SHA256"] = "0" * 64
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("does not match DISC_ZIP_SHA256", out)
        self.assertNotIn(digest, out)
        os.environ["DISC_ZIP_SHA256"] = "not-a-hash"
        code, out = self.run_main()
        self.assertEqual(code, 1)

    def test_url_and_header_never_in_argv_or_output_and_are_masked(self):
        make_zip(self.zip, {"game.iso": b"dummy"})
        os.environ["DISC_ZIP_AUTH_HEADER"] = SECRET_HEADER + "\nAccept: application/octet-stream"
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        argv_part, _, config = Path(os.environ["FAKE_CURL_ARGS"]).read_text().partition("---\n")
        self.assertNotIn("example.invalid", argv_part)
        self.assertNotIn("s3cr3t", argv_part)
        self.assertIn("-fLsS", argv_part)
        self.assertIn("--retry 3", argv_part)
        self.assertIn("--proto =https", argv_part)
        self.assertIn('url = "%s"' % SECRET_URL, config)
        self.assertIn('header = "%s"' % SECRET_HEADER, config)
        self.assertIn('header = "Accept: application/octet-stream"', config)
        self.assertIn("::add-mask::" + SECRET_URL, out)
        self.assertIn("::add-mask::" + SECRET_HEADER, out)
        self.assertIn("::add-mask::s3cr3t-token-xyz", out)
        for secret in ("example.invalid", "s3cr3t", "abc123"):
            # outside of the add-mask lines the secret must not appear at all
            visible = "\n".join(line for line in out.splitlines() if not line.startswith("::add-mask::"))
            self.assertNotIn(secret, visible)

    def test_download_failure_is_reported_without_url(self):
        make_zip(self.zip, {"game.iso": b"dummy"})
        os.environ["FAKE_CURL_FAIL"] = "1"
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 403", out)
        visible = "\n".join(line for line in out.splitlines() if not line.startswith("::add-mask::"))
        self.assertNotIn("example.invalid", visible)

    def test_missing_url_and_bad_url(self):
        os.environ["DISC_ZIP_URL"] = ""
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("DISC_ZIP_URL is not set", out)
        for bad in ("http://example.invalid/x.zip", 'https://example.invalid/"x', "https://a b"):
            with self.subTest(bad=bad):
                os.environ["DISC_ZIP_URL"] = bad
                code, out = self.run_main()
                self.assertEqual(code, 1)

    def test_bad_header_line(self):
        make_zip(self.zip, {"game.iso": b"dummy"})
        os.environ["DISC_ZIP_AUTH_HEADER"] = "no colon here"
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("DISC_ZIP_AUTH_HEADER", out)


class ExtractedDisc(Base):
    LAYOUT = {"sys/main.dol": b"dol", "sys/boot.bin": b"b", "files/rel/StaticR.rel": b"rel"}

    def run_extracted(self, entries):
        make_zip(self.zip, entries)
        return self.run_main()

    def test_flat_layout_builds_iso(self):
        code, out = self.run_extracted(self.LAYOUT)
        self.assertEqual(code, 0, out)
        env = self.exported()
        self.assertEqual(env["DISC_SOURCE_KIND"], "rebuilt-from-extracted-folder")
        self.assertEqual(Path(env["DISC_FILE"]), self.tmp / "mkw.iso")
        self.assertEqual((self.tmp / "mkw.iso").read_text(), "GOOD")
        self.assertFalse((self.tmp / "work" / "extract").exists())

    def test_data_folder_and_wrapper(self):
        entries = {"Mario/DATA/" + k: v for k, v in self.LAYOUT.items()}
        entries["Mario/UPDATE/readme.txt"] = b"x"
        code, out = self.run_extracted(entries)
        self.assertEqual(code, 0, out)
        calls = Path(os.environ["FAKE_WIT_CALLS"]).read_text().splitlines()
        # the parent that holds DATA is tried first, then DATA itself, which is the one that works here
        self.assertEqual([Path(c).name for c in calls], ["Mario", "DATA"])

    def test_lowercase_data_folder(self):
        entries = {"data/" + k: v for k, v in self.LAYOUT.items()}
        entries["disc/header.bin"] = b"h"
        code, out = self.run_extracted(entries)
        self.assertEqual(code, 0, out)

    def test_wit_unavailable_stops_with_plain_message(self):
        os.environ["FAKE_WIT_MISSING"] = "1"
        code, out = self.run_extracted(self.LAYOUT)
        self.assertEqual(code, 1)
        self.assertIn("single ISO, WBFS or RVZ", out)
        self.assertEqual(self.exported(), {})
        self.assertFalse((self.tmp / "work").exists())

    def test_invalid_result_stops(self):
        os.environ["FAKE_WIT_IMAGE_MARKER"] = "REV1"
        code, out = self.run_extracted(self.LAYOUT)
        self.assertEqual(code, 1)
        self.assertIn("single ISO, WBFS or RVZ", out)
        self.assertFalse((self.tmp / "mkw.iso").exists())

    def test_wit_failure_stops(self):
        # a layout wit's fake refuses: sys/main.dol only under DATA, and DATA is not tried first
        entries = {"DATA/" + k: v for k, v in self.LAYOUT.items()}
        os.environ["FAKE_WIT_IMAGE_MARKER"] = "garbage"
        code, out = self.run_extracted(entries)
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "mkw.iso").exists())

    def test_help_without_directory_support_stops(self):
        os.environ["FAKE_WIT_NO_DIR_HELP"] = "1"
        code, out = self.run_extracted(self.LAYOUT)
        self.assertEqual(code, 1)
        self.assertIn("does not document a directory", out)

    def test_missing_main_dol_is_not_an_extracted_disc(self):
        code, out = self.run_extracted({"sys/boot.bin": b"b", "files/a": b"a"})
        self.assertEqual(code, 1)
        self.assertIn("neither one disc image", out)

    def test_nodtool_found_under_padmint_home(self):
        home = self.tmp / "home"
        folder = home / "tools" / "nodtool-2.0.0-alpha.9"
        folder.mkdir(parents=True)
        (folder / "nodtool").write_text(FAKE_NODTOOL)
        (folder / "nodtool").chmod(0o755)
        os.environ["PADMINT_HOME"] = str(home)
        self.assertEqual(pd.find_nodtool(None), folder / "nodtool")


class Syntax(unittest.TestCase):
    def test_python39_syntax(self):
        import ast
        source = (ROOT / "scripts" / "prepare-disc.py").read_text()
        ast.parse(source, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
