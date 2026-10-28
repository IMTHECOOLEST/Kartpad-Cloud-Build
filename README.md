# KartPad for iPhone and iPad, built on a GitHub Mac

This repository builds **KartPad (Mario Kart Wii) for iPhone and iPad** on a GitHub-hosted
macOS runner, from **your own game disc**. It builds PadMint's Mac build, runs it from the
command line (`python3 -m padmint make kartpad ios`, no window ever opens), and puts the
resulting IPA in a **draft release** and, if the repository is private, in a workflow artifact.

> **The IPA is unsigned and contains translated game code. It is a personal build. Never
> publish it, never share it, never turn the draft release into a public one.**

## What is in here

| Path | Purpose |
|---|---|
| `.github/workflows/build-kartpad-ios.yml` | The workflow (manual start only). |
| `scripts/prepare-disc.py` | Downloads your disc zip, unpacks it safely, produces one disc image. |
| `scripts/install-wit.sh` | Installs Wiimms ISO Tools, only if your zip holds an extracted disc. |
| `scripts/run-padmint.sh` | Runs PadMint's `make` and prints safe diagnostics on failure. |
| `scripts/upload-draft-release.sh` | Creates the draft release. Always `--draft`. |
| `vendor/padmint/` | PadMint, unmodified copy of `padmint-main`. |
| `vendor/wit/` | The official Wiimms ISO Tools macOS build (GPL-2.0), pinned by SHA-256. |
| `tests/test_prepare_disc.py` | Tests for the disc-zip handling, using dummy bytes only. |

## One-time setup

1. Push this folder to a **private** GitHub repository (private is strongly advised: see
   "Public repositories" below).
2. In the repository open **Settings → Environments → New environment**, name it
   `disc-build`. Optionally add yourself as a required reviewer.
3. Add these secrets **to that environment** (not as repository secrets):

   | Secret | Required | What |
   |---|---|---|
   | `DISC_ZIP_URL` | yes | A plain `https://` link to your disc zip (see below). |
   | `DISC_ZIP_SHA256` | no | 64 hex characters. If set, the download must match it. |
   | `DISC_ZIP_AUTH_HEADER` | no | Full header line(s) for a private host, one `Name: value` per line, for example `Authorization: Bearer <token>`. Prefer `Authorization`: curl does not forward it to a different host after a redirect. |

   The secrets are only handed to the one step that downloads the zip, and are masked in logs.
   They are never workflow inputs.

### The disc zip

You host the zip yourself, privately (a link that expires, or a private host that accepts
a header). It must contain your own **PAL (European) Mario Kart Wii, RMCP01, revision 0**
disc, as either:

* **one disc image**: `.iso`, `.wbfs`, `.rvz`, `.wia`, `.gcz` or `.ciso` (any letter case),
  optionally inside one wrapper folder. This is the reliable choice. Use it if you can.
* **an extracted disc**: a folder with `sys/main.dol` and `files/`, at the top or under
  `DATA/` (Dolphin: right-click the game → Properties → Filesystem → Extract Entire Disc).
  PadMint's `nodtool` cannot build an image from a folder, so the workflow uses Wiimms
  ISO Tools (`wit copy <folder> --dest mkw.iso --iso`) and then checks the result with
  `nodtool info`. **This route has not been run** (see Caveats). If it fails the run stops
  with a plain message asking for a zip that holds an ISO, WBFS or RVZ.

Anything else fails with a list of the top-level names found. The unzip refuses path
traversal, symlinks, devices, password-protected zips and anything over 12 GB.

## Run it

**Actions → Build KartPad for iPhone and iPad (cloud Mac) → Run workflow.**

| Input | Default | Meaning |
|---|---|---|
| `upload_draft_release` | on | Attach the IPA and `SHA256SUMS` to a **draft** release. |
| `upload_artifact` | on | Also keep them as a workflow artifact. **Private repositories only**; on a public one it is skipped with a warning. |
| `artifact_retention_days` | 3 | 1 to 90. |
| `jobs` | blank | Parallel compile jobs, 1 to 16. Blank lets PadMint choose. |

The job has a 240-minute limit. Only `*.ipa` and `SHA256SUMS` leave the runner. The game-data
folder PadMint also makes stays on the runner and is deleted at the end, as are the disc
zip, the unpacked disc and PadMint's home folder.

### Where the IPA lands

* **Releases → Drafts**: `kartpad-ios-personal-<run number>` (a re-run of the same run adds
  `-attempt<N>`). Drafts are visible only to people with write access.
* **The run page → Artifacts**: `kartpad-ios-personal-<run number>`, private repositories only.
* The run summary shows the KartPad version, IPA name, size and SHA-256.

## Install on your device

The IPA is **not signed**. Sign and install it with a sideloading tool and your own Apple ID,
for example [Sideloadly](https://sideloadly.io), [AltStore](https://altstore.io) Classic or
[SideStore](https://sidestore.io). A free Apple ID's signature lasts 7 days; refresh it in your tool.
To update later, install the new IPA over the old one with the same tool and the same Apple ID:
saves and imported game data stay.

The game data is **not** in the IPA and the workflow does not upload it. The first time, in
KartPad tap **Import Game** on the Mario Kart Wii card, then **Import from Extracted Folder…**
and pick the `DATA` folder you extract with Dolphin on your own computer. (KartPad's guide
[INSTALL_IPA.md](https://github.com/chrissotraidis/kartpad/blob/main/docs/INSTALL_IPA.md) covers
this and the alternative of importing a disc image with your own Wii common key.)

## Public repositories

Do not use a public repository for this. Workflow logs of public repositories are public,
and artifacts of public repositories can be downloaded by any signed-in GitHub user, which is
why `upload_artifact` is skipped there. This build prints no disc data, but the IPA contains
translated game code and must stay private.

## Caveats and open risks

* **It builds the upstream KartPad release, not a local copy of the source.** PadMint's
  `make` finds the latest published release on `github.com/chrissotraidis/kartpad`, clones
  that source and downloads that release's empty `KartPad-v…-ios-for-padmint.ipa`. If the
  release has no such file the run fails; there is no automatic fallback.
* **The first run on a macOS runner is untested.** Everything here was checked on Linux with
  stand-in tools (see Tests). Nothing was run on macOS, with a real disc, or against the real
  GitHub release upload. Expect to read the first run's log.
* **Extracted folder → ISO is unverified.** It needs `wit` to accept the folder layout and
  produce an image `nodtool` recognises as RMCP01 revision 0. Wiimms ISO Tools build:
  `wit-v3.05a-r8638-mac.tar.gz`, SHA-256 `670fd1920eb0390ecdc2553942df71f0ced43c63c8e83588a23705b569d25213`,
  a universal x86_64 + arm64 binary. If it does not run on the runner, `install-wit.sh`
  tries a source build from a pinned git commit; **that commit pin was copied from a
  distribution packaging page and has never been fetched or built**.
* **wit is GPL-2.0.** Its licence text is inside the bundled archive; its source is at
  <https://wit.wiimm.de/>.
* PadMint's own tools (dotnet, cmake, ninja, nodtool) are downloaded at run time, pinned by
  digest in `vendor/padmint/padmint/tools.lock.json`. `brew install ripgrep` also runs.
* If PadMint's `scripts/package-release.py` fails for a reason that has nothing to do with the
  macOS payload (it also builds the Windows and Linux zips), the workflow warns and runs PadMint
  from `vendor/padmint`, which is the same code. If the audit rejects the macOS payload, it stops.
* A runner with little free disk space has unused simulators and spare Xcode versions removed;
  PadMint needs about 16 GB free.
* If PadMint's route fails, the run stops with the failing stage. The fallback (KartPad's full
  development build, `scripts/build-user-ipa.sh bootstrap` then `build <iso>`) is deliberately not
  automated.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

They cover one image, an extracted folder, a wrapper folder, zip-slip, symlinks, several
images, wrong content, secret handling and the wit/nodtool steps (with stand-in programs).
