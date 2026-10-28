#!/usr/bin/env bash
# Attach the IPA and SHA256SUMS to a DRAFT release. A release made here is always a draft:
# nothing in this repository publishes one or turns a draft off.
#
# Needs: ASSETS_DIR (holds exactly one *.ipa and SHA256SUMS), GH_TOKEN, GH_REPO (or a git
# remote), GITHUB_RUN_NUMBER, GITHUB_SHA. Optional: GITHUB_RUN_ATTEMPT, KARTPAD_VERSION.
set -euo pipefail

: "${ASSETS_DIR:?ASSETS_DIR is not set}"
: "${GITHUB_RUN_NUMBER:?GITHUB_RUN_NUMBER is not set}"
: "${GITHUB_SHA:?GITHUB_SHA is not set}"

shopt -s nullglob
ipas=("$ASSETS_DIR"/*.ipa)
if [ "${#ipas[@]}" -ne 1 ]; then
  echo "::error::expected exactly one .ipa in the release folder, found ${#ipas[@]}"
  exit 1
fi
ipa="${ipas[0]}"
sums="$ASSETS_DIR/SHA256SUMS"
if [ ! -f "$sums" ]; then
  echo "::error::SHA256SUMS is missing from the release folder"
  exit 1
fi

# Only these two files may ever be attached.
for file in "$ASSETS_DIR"/*; do
  case "$(basename "$file")" in
    SHA256SUMS | *.ipa) ;;
    *)
      echo "::error::unexpected file in the release folder"
      exit 1
      ;;
  esac
done

tag="kartpad-ios-personal-${GITHUB_RUN_NUMBER}"
if [ "${GITHUB_RUN_ATTEMPT:-1}" != "1" ]; then
  # A re-run of the same run would otherwise collide with the draft it made before.
  tag="${tag}-attempt${GITHUB_RUN_ATTEMPT}"
fi
title="KartPad for iPhone/iPad, personal build #${GITHUB_RUN_NUMBER}"
if [ -n "${KARTPAD_VERSION:-}" ] && [ "${KARTPAD_VERSION}" != "unknown" ]; then
  title="KartPad ${KARTPAD_VERSION} for iPhone/iPad, personal build #${GITHUB_RUN_NUMBER}"
fi

notes="$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/release-notes.XXXXXX")"
trap 'rm -f "$notes"' EXIT
cat >"$notes" <<'EOF'
**Personal build. Never publish this release.**

- It was made from the owner's own game disc and **contains translated game code**.
- The IPA is **unsigned**. Sign and install it yourself with your own Apple ID (for example with Sideloadly).
- Do not share it, do not attach it anywhere else, and do not turn this draft into a public release.
- `SHA256SUMS` lists the checksum of the IPA.
EOF

# --draft is not optional.
gh release create "$tag" "$ipa" "$sums" \
  --draft \
  --target "$GITHUB_SHA" \
  --title "$title" \
  --notes-file "$notes" >/dev/null
echo "Draft release '$tag' created with the IPA and SHA256SUMS."

# Confirm that GitHub kept it a draft. Listing can lag or be limited, so a missing row only warns.
state="$(gh release list --limit 50 --json tagName,isDraft \
  --jq ".[] | select(.tagName == \"${tag}\") | .isDraft" 2>/dev/null || true)"
case "$state" in
  true) echo "Confirmed: the release is a draft." ;;
  false)
    echo "::error::release '$tag' is not a draft. Delete it in the repository's Releases page now."
    exit 1
    ;;
  *) echo "::warning::could not confirm the draft state of '$tag'; check the repository's Releases page." ;;
esac
