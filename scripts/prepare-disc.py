#!/usr/bin/env python3
"""Fetch the disc zip named by the DISC_ZIP_* secrets and hand PadMint one disc image.

Inputs, all from the environment (never from the command line, never echoed):
  DISC_ZIP_URL          required. https:// address of the zip.
  DISC_ZIP_SHA256       optional. 64 hex characters; the download must match.
  DISC_ZIP_AUTH_HEADER  optional. One full header line per line ("Name: value").

What the zip may hold (after at most one wrapper folder):
  1. exactly one disc image: iso, wbfs, rvz, wia, gcz or ciso (any letter case)
     -> used as it is.
  2. an extracted disc: sys/main.dol and files/, at the root or under DATA/
     (Dolphin's "Extract Entire Disc") -> rebuilt into an ISO with Wiimms ISO
     Tools (wit, installed by install-wit.sh) and checked with nodtool.
  3. anything else -> a plain error that lists only top-level names.

On success it appends DISC_FILE=<path> (and DISC_SOURCE_KIND) to $GITHUB_ENV.
Written for Python 3.9 (macOS's /usr/bin/python3): no match statements, no
"X | Y" type syntax.
"""
import argparse
import errno
import glob
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import unicodedata
import zipfile
import zlib
from pathlib import Path
from typing import List, Optional, Tuple

IMAGE_EXTENSIONS = ("iso", "wbfs", "rvz", "wia", "gcz", "ciso")
# Names that zip tools add and that are not part of the disc.
JUNK_NAMES = frozenset(["__macosx", ".ds_store", "thumbs.db", "desktop.ini"])
GIB = 1 << 30
DEFAULT_MAX_BYTES = 12 * GIB  # a Wii disc is at most 8.5 GB; Mario Kart Wii is about 4.7 GB
MAX_ENTRIES = 100000
CHUNK = 1 << 20
WANTED_GAME_ID = "RMCP01"
WANTED_REVISION = 0

NEED_IMAGE_ZIP = ("Please send a zip that holds a single ISO, WBFS or RVZ file of your "
                  "Mario Kart Wii (PAL, RMCP01, revision 0) disc instead.")
HEADER_LINE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+:[ \t]*[\x21-\x7e][\x20-\x7e]*$")
URL_PATTERN = re.compile(r"^https://[\x21\x23-\x26\x28-\x5b\x5d-\x7e]+$")

CURL_MESSAGES = {
    6: "the host name could not be resolved",
    7: "could not connect to the server",
    23: "the download could not be written (is the disk full?)",
    28: "the download timed out",
    35: "a TLS connection problem",
    51: "a TLS certificate problem",
    58: "a TLS certificate problem",
    60: "a TLS certificate problem",
    63: "the zip is larger than the allowed size",
}


class DiscError(Exception):
    """A problem the user can act on; the message is safe to print."""


# --- GitHub Actions workflow commands ---------------------------------------------

def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def gigabytes(count: int) -> str:
    return "%.2f GB" % (count / GIB) if count < GIB else "%.0f GB" % (count / GIB)


def say(message: str) -> None:
    print(message, flush=True)


def report_error(message: str) -> None:
    print("::error title=Disc zip::" + escape_data(message), flush=True)


def mask(value: str, header: bool = False) -> None:
    """Ask the runner to hide every line of a secret (and, for headers, the value after the name)."""
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        print("::add-mask::" + escape_data(line), flush=True)
        if header and ":" in line:
            tail = line.split(":", 1)[1].strip()
            if tail:
                print("::add-mask::" + escape_data(tail), flush=True)
                token = tail.split()[-1]
                if token != tail:
                    print("::add-mask::" + escape_data(token), flush=True)


def export_env(path: Optional[str], values: dict) -> None:
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            raise DiscError("Internal error: a value for the workflow environment has a line break.")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            for name, value in values.items():
                stream.write("%s=%s\n" % (name, value))
    else:
        for name, value in values.items():
            print("%s=%s" % (name, value))


# --- the secrets ---------------------------------------------------------------------

def read_secrets() -> Tuple[str, str, List[str]]:
    url = os.environ.get("DISC_ZIP_URL", "").strip()
    sha = os.environ.get("DISC_ZIP_SHA256", "").strip()
    header_text = os.environ.get("DISC_ZIP_AUTH_HEADER", "")
    # Mask before anything can be printed.
    mask(url)
    mask(sha)
    mask(header_text, header=True)
    if not url:
        raise DiscError("DISC_ZIP_URL is not set. Add it as a secret of the 'disc-build' "
                        "environment (Settings > Environments > disc-build).")
    if not URL_PATTERN.match(url):
        raise DiscError("DISC_ZIP_URL must be a plain https:// address without spaces, quotes or "
                        "backslashes (percent-encode them).")
    if sha:
        sha = sha.lower()
        if sha.startswith("sha256:"):
            sha = sha[len("sha256:"):].strip()
        if not re.match(r"^[0-9a-f]{64}$", sha):
            raise DiscError("DISC_ZIP_SHA256 must be 64 hexadecimal characters.")
    headers = []
    for number, line in enumerate(header_text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not HEADER_LINE.match(line):
            raise DiscError("Line %d of DISC_ZIP_AUTH_HEADER is not a 'Name: value' header line "
                            "(printable ASCII only)." % number)
        headers.append(line)
    return url, sha, headers


# --- download ----------------------------------------------------------------------------

def curl_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def download(curl: str, url: str, headers: List[str], dest: Path, max_bytes: int) -> None:
    """curl -fL --retry 3, with the URL and headers on stdin so they never reach argv."""
    config = "url = %s\n" % curl_quote(url)
    for line in headers:
        config += "header = %s\n" % curl_quote(line)
    command = [curl, "-fLsS", "--retry", "3", "--retry-delay", "5",
               "--proto", "=https", "--proto-redir", "=https",
               "--speed-limit", "1024", "--speed-time", "120",
               "--max-filesize", str(max_bytes), "--output", str(dest), "--config", "-"]
    try:
        result = subprocess.run(command, input=config.encode("ascii"), stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    except OSError:
        raise DiscError("curl could not be started on this runner.")
    if result.returncode != 0:
        if dest.exists():
            dest.unlink()
        text = result.stderr.decode("utf-8", "replace")
        status = re.search(r"returned error: (\d{3})", text)
        # curl's own text can contain the host name, so only a mapped message is shown.
        reason = CURL_MESSAGES.get(result.returncode)
        if result.returncode == 22 and status:
            reason = "the server answered HTTP %s (check the URL, its expiry and the auth header)" % status.group(1)
        elif reason is None:
            reason = "curl exit code %d" % result.returncode
        raise DiscError("Downloading the disc zip failed: %s." % reason)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise DiscError("The download produced an empty file.")
    if dest.stat().st_size > max_bytes:
        raise DiscError("The disc zip is larger than the allowed %s." % gigabytes(max_bytes))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- safe unzip ------------------------------------------------------------------------------

def split_member(name: str) -> List[str]:
    """Path parts of a zip member name, or DiscError for anything that could escape."""
    if "\x00" in name:
        raise DiscError("The zip contains an invalid file name and was rejected.")
    normal = name.replace("\\", "/")
    if normal.startswith("/") or re.match(r"^[A-Za-z]:", normal):
        raise DiscError("The zip contains an absolute path and was rejected.")
    parts = [part for part in normal.split("/") if part not in ("", ".")]
    for part in parts:
        if part == "..":
            raise DiscError("The zip tries to write outside its own folder (zip-slip) and was rejected.")
        if any(ord(char) < 32 or ord(char) == 127 for char in part):
            raise DiscError("The zip contains a file name with control characters and was rejected.")
    return parts


def path_key(parts: List[str]) -> tuple:
    # macOS volumes ignore case and Unicode form, so two such names would collide.
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)


def safe_extract(zip_path: Path, dest: Path, max_bytes: int) -> None:
    """Unzip with no symlinks, no path escapes, no overwrites and a total size cap."""
    dest.mkdir(parents=True, exist_ok=True)
    if not zipfile.is_zipfile(str(zip_path)):
        raise DiscError("The download is not a zip file. Some hosts answer with an HTML sign-in "
                        "page when the link has expired or the auth header is wrong.")
    try:
        archive = zipfile.ZipFile(str(zip_path))
    except (zipfile.BadZipFile, OSError):
        raise DiscError("The disc zip is damaged and cannot be opened.")
    with archive:
        infos = archive.infolist()
        if not infos:
            raise DiscError("The disc zip is empty.")
        if len(infos) > MAX_ENTRIES:
            raise DiscError("The disc zip has too many entries (limit %d)." % MAX_ENTRIES)
        plan = []
        files_seen = set()
        dirs_seen = set()
        total = 0

        def add_dirs(key: tuple) -> None:
            for depth in range(1, len(key) + 1):
                prefix = key[:depth]
                if prefix in files_seen:
                    raise DiscError("The zip has a file and a folder with the same path and was rejected.")
                dirs_seen.add(prefix)

        for info in infos:
            parts = split_member(info.filename)
            if not parts:
                continue
            if info.flag_bits & 0x1:
                raise DiscError("The disc zip is password-protected; use an unprotected zip "
                                "(keep the download link private instead).")
            mode = (info.external_attr >> 16) if info.create_system == 3 else 0
            kind = stat.S_IFMT(mode)
            if kind == stat.S_IFLNK:
                raise DiscError("The zip contains a symbolic link and was rejected.")
            if kind in (stat.S_IFBLK, stat.S_IFCHR, stat.S_IFIFO, stat.S_IFSOCK):
                raise DiscError("The zip contains a special file (device, pipe or socket) and was rejected.")
            is_dir = info.filename.replace("\\", "/").endswith("/") or kind == stat.S_IFDIR
            key = path_key(parts)
            if is_dir:
                add_dirs(key)
                plan.append((info, parts, True))
            else:
                if key in files_seen or key in dirs_seen:
                    raise DiscError("The zip lists the same path twice and was rejected.")
                add_dirs(key[:-1])
                files_seen.add(key)
                total += info.file_size
                plan.append((info, parts, False))
        if total > max_bytes:
            raise DiscError("The disc zip would unpack to more than the allowed %s." % gigabytes(max_bytes))
        root_real = os.path.realpath(str(dest))
        written = 0
        for info, parts, is_dir in plan:
            target = dest.joinpath(*parts)
            parent_real = os.path.realpath(str(target.parent))
            if os.path.commonpath([root_real, parent_real]) != root_real:
                raise DiscError("The zip tries to write outside its own folder and was rejected.")
            try:
                if is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                with archive.open(info) as source, open(str(target), "xb") as sink:
                    while True:
                        chunk = source.read(CHUNK)
                        if not chunk:
                            break
                        copied += len(chunk)
                        written += len(chunk)
                        if copied > info.file_size or written > max_bytes:
                            raise DiscError("A file in the zip is larger than the zip says; rejected.")
                        sink.write(chunk)
                if copied != info.file_size:
                    raise DiscError("A file in the zip is shorter than the zip says; it is damaged.")
                os.chmod(str(target), 0o644)
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
                raise DiscError("The disc zip is damaged or uses an unsupported feature.")
            except OSError as error:
                if error.errno == errno.ENOSPC:
                    raise DiscError("The runner ran out of disk space while unpacking the zip.")
                raise DiscError("A file from the zip could not be written (%s)." % type(error).__name__)


# --- what is in the zip ----------------------------------------------------------------------------

def visible_names(folder: Path) -> List[str]:
    names = []
    for name in sorted(os.listdir(str(folder))):
        if name.lower() in JUNK_NAMES or name.startswith("._"):
            continue
        names.append(name)
    return names


def content_root(extract_dir: Path) -> Tuple[Path, Optional[str]]:
    """The folder to look in: the zip's top level, or the single folder that wraps everything."""
    names = visible_names(extract_dir)
    if len(names) == 1 and (extract_dir / names[0]).is_dir():
        return extract_dir / names[0], names[0]
    return extract_dir, None


def child(folder: Path, name: str) -> Optional[Path]:
    """A child by name, ignoring letter case."""
    try:
        for entry in os.listdir(str(folder)):
            if entry.lower() == name.lower():
                return folder / entry
    except OSError:
        return None
    return None


def has_disc_layout(folder: Path) -> bool:
    sys_dir = child(folder, "sys")
    files_dir = child(folder, "files")
    if sys_dir is None or files_dir is None or not sys_dir.is_dir() or not files_dir.is_dir():
        return False
    dol = child(sys_dir, "main.dol")
    return dol is not None and dol.is_file()


def describe_names(folder: Path, wrapper: Optional[str]) -> str:
    shown = []
    for name in visible_names(folder)[:25]:
        label = "".join(char if char.isprintable() else "?" for char in name)[:60]
        shown.append(label + ("/" if (folder / name).is_dir() else ""))
    where = "inside the single folder '%s'" % wrapper if wrapper else "at the top level"
    listing = ", ".join(shown) if shown else "nothing"
    return "Found %s: %s." % (where, listing)


def classify(root: Path, wrapper: Optional[str]) -> Tuple[str, object]:
    """('image', path) or ('extracted', [folders for wit to try, best first])."""
    images = []
    for name in visible_names(root):
        path = root / name
        if path.is_file() and "." in name and name.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS:
            images.append(path)
    if len(images) > 1:
        raise DiscError("The disc zip holds %d disc images. Put exactly one image in the zip." % len(images))
    if images:
        return "image", images[0]
    if has_disc_layout(root):
        return "extracted", [root]
    data = child(root, "DATA")
    if data is not None and data.is_dir() and has_disc_layout(data):
        return "extracted", [root, data]
    raise DiscError("The disc zip holds neither one disc image (iso, wbfs, rvz, wia, gcz, ciso) nor an "
                    "extracted disc (sys/main.dol and files/, at the top or under DATA/). "
                    + describe_names(root, wrapper))


# --- extracted disc -> ISO ----------------------------------------------------------------------

def find_nodtool(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    home = os.environ.get("PADMINT_HOME")
    if home:
        pattern = os.path.join(glob.escape(home), "tools", "nodtool-*", "nodtool")
        matches = sorted(glob.glob(pattern))
        if matches:
            return Path(matches[-1])
    found = shutil.which("nodtool")
    return Path(found) if found else None


def disc_identity(nodtool: Path, image: Path) -> Optional[Tuple[str, Optional[int]]]:
    """(game id, revision) from `nodtool info`, read the way PadMint reads it."""
    try:
        result = subprocess.run([str(nodtool), "info", str(image)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    game_id = re.search(r"^Game ID: ([0-9A-Z]{6})", result.stdout, re.M)
    revision = re.search(r"^Disc \d+, Revision (\d+)", result.stdout, re.M)
    if not game_id:
        return None
    return game_id.group(1), int(revision.group(1)) if revision else None


def run_logged(command: List[str], log_path: Path, timeout: int) -> int:
    """Run a tool, keep its output in a file, and show only the end of it when it fails."""
    with open(str(log_path), "wb") as log:
        try:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return 127
    if result.returncode != 0:
        try:
            tail = log_path.read_text(errors="replace").splitlines()[-15:]
        except OSError:
            tail = []
        for line in tail:
            say("  | " + line)
    return result.returncode


def build_iso(folders: List[Path], work: Path, iso_path: Path, args: argparse.Namespace) -> Path:
    nodtool = find_nodtool(args.nodtool)
    if nodtool is None:
        raise DiscError("nodtool was not found. Run 'padmint tools kartpad --target ios' before this step.")
    say("The zip holds an extracted disc. Rebuilding an ISO with Wiimms ISO Tools (wit)...")
    wit_dir = Path(args.wit_dir)
    installer = Path(args.install_wit)
    code = subprocess.call(["bash", str(installer), str(wit_dir)])
    wit = wit_dir / "bin" / "wit"
    if code != 0 or not wit.is_file() or not os.access(str(wit), os.X_OK):
        raise DiscError("Wiimms ISO Tools (wit) is not available on this runner, so the extracted disc in the "
                        "zip cannot be turned back into a disc image. " + NEED_IMAGE_ZIP)
    help_log = work / "wit-help.log"
    version_log = work / "wit-version.log"
    run_logged([str(wit), "--version"], version_log, 60)
    try:
        say("wit: " + version_log.read_text(errors="replace").strip().splitlines()[0])
    except (OSError, IndexError):
        say("wit: version not reported")
    try:
        help_text = subprocess.run([str(wit), "copy", "--help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        help_text = ""
    help_log.write_text(help_text)
    if not re.search(r"director|\bfst\b", help_text, re.I):
        raise DiscError("This build of wit does not document a directory as the input of 'wit copy', so the "
                        "extracted disc cannot be rebuilt. " + NEED_IMAGE_ZIP)
    if iso_path.exists():
        iso_path.unlink()
    for folder in folders:
        say("Running wit copy on the extracted folder...")
        code = run_logged([str(wit), "copy", str(folder), "--dest", str(iso_path), "--iso"],
                          work / "wit-copy.log", 3600)
        if code == 0 and iso_path.is_file():
            identity = disc_identity(nodtool, iso_path)
            if identity == (WANTED_GAME_ID, WANTED_REVISION):
                say("nodtool confirms the rebuilt image is %s revision %d." % identity)
                return iso_path
            say("nodtool does not recognise the rebuilt image as %s revision %d." % (WANTED_GAME_ID, WANTED_REVISION))
        else:
            say("wit could not build an image from that folder (exit code %d)." % code)
        if iso_path.exists():
            iso_path.unlink()
    raise DiscError("Could not rebuild a valid Mario Kart Wii (RMCP01, revision 0) disc image from the "
                    "extracted folder. " + NEED_IMAGE_ZIP)


# --- main -------------------------------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    temp = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", default=os.path.join(temp, "disc-work"))
    parser.add_argument("--iso-path", default=os.path.join(temp, "mkw.iso"))
    parser.add_argument("--wit-dir", default=os.path.join(temp, "wit"))
    parser.add_argument("--install-wit", default=str(here / "install-wit.sh"))
    parser.add_argument("--nodtool", help="nodtool executable (default: the one PadMint installed under PADMINT_HOME)")
    parser.add_argument("--curl", default="curl")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--github-env", default=os.environ.get("GITHUB_ENV"))
    return parser.parse_args(argv)


def cleanup(args: argparse.Namespace) -> None:
    shutil.rmtree(args.work_dir, ignore_errors=True)
    try:
        Path(args.iso_path).unlink()
    except OSError:
        pass


def run(args: argparse.Namespace) -> int:
    url, expected_sha, headers = read_secrets()
    work = Path(args.work_dir)
    shutil.rmtree(str(work), ignore_errors=True)
    work.mkdir(parents=True, mode=0o700)
    zip_path = work / "disc.zip"
    say("Downloading the disc zip...")
    download(args.curl, url, headers, zip_path, args.max_bytes)
    say("Downloaded %.2f GB." % (zip_path.stat().st_size / GIB))
    if expected_sha:
        if sha256_of(zip_path) != expected_sha:
            raise DiscError("The downloaded zip does not match DISC_ZIP_SHA256.")
        say("SHA-256 of the zip matches DISC_ZIP_SHA256.")
    extract_dir = work / "extract"
    safe_extract(zip_path, extract_dir, args.max_bytes)
    zip_path.unlink()  # the unpacked copy is enough; free the disk
    root, wrapper = content_root(extract_dir)
    kind, found = classify(root, wrapper)
    if kind == "image":
        image = found
        extension = image.name.rsplit(".", 1)[-1].lower()
        final = work / ("mkw." + extension)
        os.replace(str(image), str(final))  # a fixed name, so no zip-supplied name reaches the logs
        shutil.rmtree(str(extract_dir), ignore_errors=True)
        say("The zip holds one disc image (.%s). Using it as it is." % extension)
        disc, source_kind = final, "image"
    else:
        disc = build_iso(found, work, Path(args.iso_path), args)
        shutil.rmtree(str(extract_dir), ignore_errors=True)
        source_kind = "rebuilt-from-extracted-folder"
    export_env(args.github_env, {"DISC_FILE": str(disc), "DISC_SOURCE_KIND": source_kind})
    say("Disc ready (%.2f GB)." % (disc.stat().st_size / GIB))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except DiscError as error:
        report_error(str(error))
        cleanup(args)
        return 1
    except Exception as error:  # never print the message: it could carry a URL
        frames = traceback.extract_tb(error.__traceback__)
        where = "%s:%d" % (Path(frames[-1].filename).name, frames[-1].lineno) if frames else "unknown"
        report_error("Unexpected %s at %s." % (type(error).__name__, where))
        cleanup(args)
        return 1


if __name__ == "__main__":
    sys.exit(main())
