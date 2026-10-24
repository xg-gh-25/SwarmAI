#!/usr/bin/env bash
# Generate the Tauri updater manifest (latest.json) for a release.
#
# WHY: release.yml builds signed updater bundles (createUpdaterArtifacts:true +
# TAURI_SIGNING_PRIVATE_KEY) but never assembled the latest.json manifest the
# in-app updater fetches from the release. Without it, auto-update is a silent
# no-op (endpoint 404). This script composes that manifest from the downloaded
# CI artifacts and writes it into the release dir so it uploads as a release asset.
#
# Extracted from the workflow (vs inline `run:`) so it is LOCALLY TESTABLE against
# a fixture artifacts/ tree — see .github/scripts/test_gen_latest_json.sh.
#
# Tauri v2 plugin-updater manifest schema (verified against
# tauri-plugin-updater/src/updater.rs:76-123,1324-1352):
#   { "version": "X.Y.Z", "notes": "...", "pub_date": "<RFC3339>",
#     "platforms": { "<os>-<arch>": { "signature": "<.sig contents>", "url": "<bundle url>" } } }
#   os key:  macOS -> "darwin"  (NOT "macos"),  windows -> "windows"
#   arch key: aarch64 -> "aarch64",  x86_64 -> "x86_64"
#   -> our keys: darwin-aarch64, darwin-x86_64, windows-x86_64, linux-x86_64
# The updater downloads the BUNDLE: macOS the .app.tar.gz, Windows the NSIS -setup.exe, Linux the
# .AppImage (Tauri v2 updater artifacts, createUpdaterArtifacts: true). The `signature` value is
# the literal CONTENTS of the bundle's .sig file.
#
# Usage:
#   gen-latest-json.sh <VERSION> <ARTIFACTS_DIR> <OUT_FILE>
#     VERSION        e.g. 1.27.0  (no leading v)
#     ARTIFACTS_DIR  the download-artifact root: installer-<target>/ per release target
#                    (written by collect-installer.sh)
#     OUT_FILE       where to write latest.json  (e.g. release/latest.json)
#
# Contract (one release, design 2026-10-24-swarmai-usable-by-anyone §7 r14): every one of the
# four targets must have its bundle and a non-empty .sig, else FAIL (exit 1). Bundle discovery
# is scoped to each target's own artifact dir.

set -euo pipefail

VERSION="${1:?VERSION required}"
ARTIFACTS_DIR="${2:?ARTIFACTS_DIR required}"
OUT_FILE="${3:?OUT_FILE required}"

# Fail loud on a malformed VERSION: the tauri updater compares it as semver, and a
# bad value (e.g. leftover 'v', pre-release/build metadata, or a typo) could break
# comparison or cause a downgrade. Require plain MAJOR.MINOR.PATCH.
if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: VERSION '$VERSION' is not a plain semver (MAJOR.MINOR.PATCH) — refusing to build a manifest" >&2
  exit 1
fi

REPO_BASE="https://github.com/xg-gh-25/SwarmAI/releases/download/v${VERSION}"
PUB_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

# --- helper: find a single file by glob under ONE artifact subdir (scoped) ---
# echoes the path, or empty if none. Errors if >1 (ambiguous — fail loud).
_find_one() {
  local dir="$1" pattern="$2" matches
  [ -d "$dir" ] || { echo ""; return 0; }
  # shellcheck disable=SC2207
  matches=($(find "$dir" -name "$pattern" -type f 2>/dev/null))
  if [ "${#matches[@]}" -eq 0 ]; then echo ""; return 0; fi
  if [ "${#matches[@]}" -gt 1 ]; then
    echo "ERROR: ambiguous match for '$pattern' in '$dir': ${matches[*]}" >&2
    return 1
  fi
  echo "${matches[0]}"
}

# Build the platforms object incrementally with a tiny python helper (robust JSON).
PLATFORMS_JSON="{}"

_add_platform() {
  # $1=key  $2=bundle_filename  $3=signature_contents
  PLATFORMS_JSON="$(OUT_KEY="$1" OUT_FN="$2" OUT_SIG="$3" OUT_BASE="$REPO_BASE" \
    PLATFORMS_IN="$PLATFORMS_JSON" python3 -c '
import json, os
p = json.loads(os.environ["PLATFORMS_IN"])
p[os.environ["OUT_KEY"]] = {
    "signature": os.environ["OUT_SIG"],
    "url": os.environ["OUT_BASE"] + "/" + os.environ["OUT_FN"],
}
print(json.dumps(p))
')"
}

# <platform key> <artifact dir> <bundle glob>
TARGETS=(
  "darwin-aarch64 installer-macos-arm64 *_aarch64.app.tar.gz"
  "darwin-x86_64  installer-macos-x64   *_x64.app.tar.gz"
  "windows-x86_64 installer-windows-x64 *-setup.exe"
  "linux-x86_64   installer-linux-x64   *.AppImage"
)
for t in "${TARGETS[@]}"; do
  read -r key dir glob <<<"$t"
  BUNDLE="$(_find_one "${ARTIFACTS_DIR}/$dir" "$glob")"
  SIG="$(_find_one "${ARTIFACTS_DIR}/$dir" "$glob.sig")"
  if [ -z "$BUNDLE" ] || [ -z "$SIG" ]; then
    echo "ERROR: $key: updater bundle or .sig missing in ${ARTIFACTS_DIR}/$dir (bundle='$BUNDLE' sig='$SIG')" >&2
    exit 1
  fi
  # fail closed: an empty .sig would embed signature:"" and the updater must never get one
  SIG_CONTENT="$(cat "$SIG")"
  if [ -z "$SIG_CONTENT" ]; then
    echo "ERROR: $key: $SIG is EMPTY — refusing to ship a manifest with a blank signature" >&2
    exit 1
  fi
  _add_platform "$key" "$(basename "$BUNDLE")" "$SIG_CONTENT"
  echo "  + $key: $(basename "$BUNDLE")" >&2
done

# ---------------- assemble + validate ----------------
if [ "$PLATFORMS_JSON" = "{}" ]; then
  echo "ERROR: no updater platforms resolved — refusing to write an empty manifest" >&2
  exit 1
fi

VERSION="$VERSION" PUB_DATE="$PUB_DATE" PLATFORMS_IN="$PLATFORMS_JSON" OUT_FILE="$OUT_FILE" python3 -c '
import json, os
manifest = {
    "version": os.environ["VERSION"],
    "notes": "See the release notes at https://github.com/xg-gh-25/SwarmAI/releases/tag/v" + os.environ["VERSION"],
    "pub_date": os.environ["PUB_DATE"],
    "platforms": json.loads(os.environ["PLATFORMS_IN"]),
}
with open(os.environ["OUT_FILE"], "w") as f:
    json.dump(manifest, f, indent=2)
print("wrote " + os.environ["OUT_FILE"])
'
# Fail loud if the output is not valid JSON (belt-and-suspenders).
python3 -m json.tool "$OUT_FILE" >/dev/null
echo "latest.json generated for v${VERSION}"
