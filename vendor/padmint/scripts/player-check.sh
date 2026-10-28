#!/bin/bash
# Build a game the way a Mac player's PadMint does, before you announce a release.
#
#   scripts/player-check.sh GAME PLATFORM GAME_FILE              the game's latest release
#   scripts/player-check.sh GAME PLATFORM GAME_FILE CHECKOUT [APP]  a clean checkout; APP is its compatible base app
#
# GAME_FILE is - for games whose players choose their game in the app, not during the build.
# Players start PadMint.command, which runs Apple's /usr/bin/python3 (3.9), with nothing
# downloaded yet. A maintainer's `python3 -m padmint` usually runs a newer Python with warm
# caches, and passes builds that fail for players (padmint#142). This uses Apple's Python and
# an empty PadMint folder, and builds a fresh copy of the source. Run it with CHECKOUT before
# tagging and without it after publishing. The folder it prints holds your personal build
# and translated game code: keep it private and delete it when you are done.
set -euo pipefail
if [ $# -lt 3 ] || [ $# -gt 5 ]; then
  sed -n 4,5p "$0" | sed 's/^# *//'
  exit 2
fi
game=$1 platform=$2 game_file=$3 checkout=${4:-} app=${5:-}
if [ -n "$app" ]; then
  app="$(cd "$(dirname "$app")" && pwd)/$(basename "$app")"
fi
player_python=/usr/bin/python3
padmint_root=$(cd "$(dirname "$0")/.." && pwd)
check_folder=$(mktemp -d "${TMPDIR:-/tmp}/padmint-player-check.XXXXXX")
echo "Python: $("$player_python" --version 2>&1) at $player_python, as PadMint.command runs it"
echo "Folder: $check_folder"
cd "$padmint_root"
if [ -n "$checkout" ]; then
  revision=$(git -C "$checkout" rev-parse HEAD)
  git clone --quiet --no-hardlinks "$checkout" "$check_folder/source"
  git -C "$check_folder/source" checkout --quiet --detach "$revision"
  # `build` uses installed tools; `make` installs them automatically. Do the same
  # preparation for a checkout, still inside this check's empty PadMint folder.
  PADMINT_HOME="$check_folder/padmint" "$player_python" -m padmint tools "$game" \
    --repo "$check_folder/source" --target "$platform"
  build_args=(--repo "$check_folder/source" --revision "$revision" --target "$platform")
  if [ "$game_file" != - ]; then
    build_args+=(--disc "$game_file")
  fi
  if [ -n "$app" ]; then
    build_args+=(--app "$app")
  fi
  PADMINT_HOME="$check_folder/padmint" "$player_python" -m padmint build "$game" \
    "${build_args[@]}"
else
  make_args=(--out "$check_folder/out")
  if [ "$game_file" != - ]; then
    make_args+=(--disc "$game_file")
  fi
  PADMINT_HOME="$check_folder/padmint" "$player_python" -m padmint make "$game" "$platform" \
    "${make_args[@]}"
fi
echo "Player check passed. Delete $check_folder when you are done with it."
