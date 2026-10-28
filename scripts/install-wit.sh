#!/usr/bin/env bash
# Install Wiimms ISO Tools (wit) into DEST so that DEST/bin/wit exists.
#
#   scripts/install-wit.sh DEST
#
# Only prepare-disc.py calls this, and only when the disc zip holds an extracted disc
# (PadMint's nodtool can convert and extract images but cannot build one from a folder).
#
# Two routes, each checked before it runs:
#   1. the bundled official macOS build, pinned by SHA-256;
#   2. if that will not unpack or will not run on the runner (an Intel-only
#      binary needs Rosetta), a build from source, pinned by the full git commit hash.
# Exit codes: 0 = DEST/bin/wit is ready, 3 = wit is not available (the reason is printed).
#
# Route 1 uses vendor/wit/wit-v3.05a-r8638-mac.tar.gz, the official macOS build supplied
# by the repository owner (a universal x86_64 + arm64 binary), checked against WIT_MAC_SHA256
# before it is unpacked. Route 2's commit pin came from a distribution packaging page and is
# UNVERIFIED: it has never been fetched or built. If the commit cannot be fetched this script
# stops with exit code 3; it never uses an unpinned download.
set -euo pipefail

WIT_MAC_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/wit/wit-v3.05a-r8638-mac.tar.gz"
WIT_MAC_SHA256="670fd1920eb0390ecdc2553942df71f0ced43c63c8e83588a23705b569d25213"
WIT_SRC_GIT_URL="https://github.com/Wiimm/wiimms-iso-tools.git"
WIT_SRC_GIT_COMMIT="fc1c0b840cb3ac41ca6e4f1d5e16da12b47eab58"

say() { printf 'install-wit: %s\n' "$*" >&2; }

dest="${1:-}"
if [ -z "$dest" ]; then
  say "usage: install-wit.sh DEST"
  exit 2
fi

work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/wit-install.XXXXXX")"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

rm -rf "$dest"
mkdir -p "$dest/bin"

# Put a built or downloaded wit (and its share/ folder, if it has one) into DEST.
install_from() {
  local binary="$1" root
  root="$(dirname "$(dirname "$binary")")"
  cp "$binary" "$dest/bin/wit" || return 1
  chmod 755 "$dest/bin/wit" || return 1
  if [ "$(basename "$(dirname "$binary")")" = "bin" ] && [ -d "$root/share" ]; then
    cp -R "$root/share" "$dest/share" || return 1
  fi
}

# A wit that starts and answers --version is good enough to go on; wit itself is
# checked further by prepare-disc.py (wit copy --help) and by nodtool on the result.
runs() { "$1" --version >/dev/null 2>&1; }

try_mac_binary() {
  local archive="$WIT_MAC_FILE" actual
  if [ ! -f "$archive" ]; then
    say "the bundled macOS build is missing; skipping that route"
    return 1
  fi
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  else
    actual="$(sha256sum "$archive" | awk '{print $1}')"
  fi
  if [ "$actual" != "$WIT_MAC_SHA256" ]; then
    say "the macOS build does not match its pinned SHA-256; not using it"
    return 1
  fi
  # Refuse absolute paths and .. before unpacking anything. (The listing goes to a file:
  # piping tar into grep -q would trip pipefail when grep stops reading early.)
  if ! tar -tzf "$archive" >"$work/members.txt"; then
    say "the macOS archive cannot be read"
    return 1
  fi
  if grep -Eq '(^/|(^|/)\.\.(/|$))' "$work/members.txt"; then
    say "the macOS archive has unsafe paths; not using it"
    return 1
  fi
  mkdir -p "$work/mac" || return 1
  tar -xzf "$archive" -C "$work/mac" || return 1
  local binary
  binary="$(find "$work/mac" -type f -name wit -perm -u+x | head -n 1)"
  if [ -z "$binary" ]; then
    say "the macOS archive has no wit program"
    return 1
  fi
  if ! runs "$binary"; then
    say "the macOS build does not run on this runner ($(uname -m)); trying a source build"
    return 1
  fi
  install_from "$binary" || return 1
  say "using the pinned macOS build"
}

try_source_build() {
  if [ -z "$WIT_SRC_GIT_URL" ] || [ -z "$WIT_SRC_GIT_COMMIT" ]; then
    say "no pinned source (WIT_SRC_GIT_URL / WIT_SRC_GIT_COMMIT are empty)"
    return 1
  fi
  if ! command -v make >/dev/null 2>&1 || ! command -v cc >/dev/null 2>&1; then
    say "make or a C compiler is missing"
    return 1
  fi
  local src="$work/src" head makefile builddir jobs binary
  git init -q "$src" || return 1
  git -C "$src" remote add origin "$WIT_SRC_GIT_URL" || return 1
  if ! git -C "$src" fetch -q --depth 1 origin "$WIT_SRC_GIT_COMMIT"; then
    say "could not fetch the pinned wit source commit"
    return 1
  fi
  git -C "$src" checkout -q --detach FETCH_HEAD || return 1
  head="$(git -C "$src" rev-parse HEAD)" || return 1
  if [ "$head" != "$WIT_SRC_GIT_COMMIT" ]; then
    say "the fetched commit is not the pinned one; not building it"
    return 1
  fi
  makefile="$(find "$src" -maxdepth 2 -name Makefile -not -path '*/.git/*' | head -n 1)"
  if [ -z "$makefile" ]; then
    say "the pinned source has no Makefile"
    return 1
  fi
  builddir="$(dirname "$makefile")"
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 2)"
  say "building wit from source (commit $head)"
  # NO_FUSE: the FUSE file-system tool is not needed and does not build on macOS.
  if ! (cd "$builddir" && make -j"$jobs" NO_FUSE=1 >"$work/make.log" 2>&1); then
    say "the source build failed; last lines of its log:"
    tail -n 15 "$work/make.log" >&2 || true
    return 1
  fi
  binary="$builddir/bin/wit"
  if [ ! -x "$binary" ]; then
    binary="$(find "$builddir" -type f -name wit -perm -u+x -not -path '*/.git/*' | head -n 1)"
  fi
  if [ -z "$binary" ] || ! runs "$binary"; then
    say "the built wit does not run"
    return 1
  fi
  install_from "$binary" || return 1
  say "using wit built from source"
}

if try_mac_binary || try_source_build; then
  if runs "$dest/bin/wit"; then
    exit 0
  fi
  say "the installed wit does not run"
fi

say "Wiimms ISO Tools (wit) is not available here"
exit 3
