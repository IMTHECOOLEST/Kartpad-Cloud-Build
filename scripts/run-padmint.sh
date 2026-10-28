#!/usr/bin/env bash
# Build KartPad for iPhone/iPad with PadMint, from the command line, on the runner.
#
# Needs (set by the workflow): DISC_FILE (one disc image), OUT (an output folder),
# PADMINT_APP_DIR (the folder that holds the `padmint` package), RUNNER_TEMP.
# Optional: JOBS (blank = let PadMint choose), PADMINT_HOME.
#
# `make` never opens a window: the 127.0.0.1 page only appears for a bare `padmint` run
# with a terminal on stdin, and PADMINT_TERMINAL=1 rules that out as well.
#
# Nothing here uses `set -x`, and nothing disc-derived is printed on failure: only the
# tail of PadMint's own console output and the names of the build stages.
set -euo pipefail

: "${DISC_FILE:?DISC_FILE is not set}"
: "${OUT:?OUT is not set}"
: "${PADMINT_APP_DIR:?PADMINT_APP_DIR is not set}"
: "${RUNNER_TEMP:?RUNNER_TEMP is not set}"

JOBS="${JOBS:-}"
if [ -n "$JOBS" ] && ! [[ "$JOBS" =~ ^[0-9]+$ ]]; then
  echo "::error::the jobs input must be a whole number from 1 to 16, or blank"
  exit 2
fi
if [ ! -f "$DISC_FILE" ]; then
  echo "::error::the disc image is missing"
  exit 2
fi

# Backends read these as the signs of their own CI, so PadMint's make must not see them
# (this is what PadMint's own scripts/github-build.py does before it calls make).
unset CI GITHUB_ACTIONS GITHUB_REF GITHUB_REF_NAME GITHUB_HEAD_REF GITHUB_BASE_REF GITHUB_EVENT_NAME GITHUB_SHA

export PADMINT_TERMINAL=1
export PADMINT_HOME="${PADMINT_HOME:-$RUNNER_TEMP/padmint-home}"
export PADMINT_LANG=en
export PYTHONUNBUFFERED=1

mkdir -p "$OUT"
log="$RUNNER_TEMP/padmint-make.log"
: >"$log"

extra=()
if [ -n "$JOBS" ]; then
  extra=(--jobs "$JOBS")
fi

cd "$PADMINT_APP_DIR"
echo "Running: python3 -m padmint make kartpad ios --disc <disc image> --out <output folder>${JOBS:+ --jobs $JOBS}"

set +e
/usr/bin/python3 -m padmint make kartpad ios --disc "$DISC_FILE" --out "$OUT" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"
set -e

if [ "$status" -eq 0 ]; then
  exit 0
fi

echo "::error::PadMint make failed with exit code $status. See the lines below and the log above."
echo "---- last 80 lines of PadMint's output ----"
tail -n 80 "$log" || true
echo "---- build stages recorded under PADMINT_HOME (names only) ----"
/usr/bin/python3 -I - "$PADMINT_HOME" <<'PY' || true
import glob
import json
import os
import sys

home = sys.argv[1]
pattern = os.path.join(glob.escape(home), "games", "*", "build", "**", "progress.jsonl")
seen = []
for path in sorted(glob.glob(pattern, recursive=True)):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            lines = stream.read().splitlines()
    except OSError:
        continue
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        backend = record.get("backend")
        sources = [record] + ([backend] if isinstance(backend, dict) else [])
        for source in sources:
            stage, event = source.get("stage"), source.get("event")
            if isinstance(stage, str) and isinstance(event, str):
                label = "%s: %s" % (stage[:40], event[:40])
                if not seen or seen[-1] != label:
                    seen.append(label)
if not seen:
    print("(no stage records found)")
for label in seen[-40:]:
    print("  " + label)
PY
exit "$status"
