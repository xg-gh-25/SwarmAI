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
#   -> our keys: "darwin-aarch64" (macOS) and "windows-x86_64" (Windows)
# The updater downloads the BUNDLE (.app.tar.gz / -setup.nsis.zip), NOT the DMG/exe.
# The `signature` value is the literal CONTENTS of the bundle's .sig file.
#
# Usage:
#   gen-latest-json.sh <VERSION> <ARTIFACTS_DIR> <OUT_FILE> [MACOS_BUILT] [WINDOWS_BUILT]
#     VERSION        e.g. 1.27.0  (no leading v)
#     ARTIFACTS_DIR  the download-artifact root (each artifact in a subdir)
#     OUT_FILE       where to write latest.json  (e.g. release/latest.json)
#     MACOS_BUILT    "true" if build-macos succeeded (default "true")
#     WINDOWS_BUILT  "true" if build-windows succeeded (default "true")
#
# Contract (Gate-1 adopted):
#   - macOS is load-bearing: if MACOS_BUILT=true and its bundle/.sig are missing
#     -> FAIL LOUDLY (exit 1). Never ship an empty/mac-less manifest silently.
#   - If MACOS_BUILT=false (e.g. hive-only release where build-macos failed):
#     skip the darwin platform gracefully; do NOT fail the publish.
#   - Windows is best-effort: omit the windows-x86_64 platform entirely if its
#     bundle/.sig are absent — never emit an entry with an empty signature.
#   - Bundle discovery is SCOPED to each artifact's own subdir (never a bare
#     `find <artifacts> -name '*.tar.gz'` which collides with the hive tar.gz).

set -euo pipefail

VERSION="${1:?VERSION required}"
ARTIFACTS_DIR="${2:?ARTIFACTS_DIR required}"
OUT_FILE="${3:?OUT_FILE required}"
MACOS_BUILT="${4:-true}"
WINDOWS_BUILT="${5:-true}"

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

# ---------------- macOS (darwin-aarch64) ----------------
if [ "$MACOS_BUILT" = "true" ]; then
  MAC_DIR="${ARTIFACTS_DIR}/macos-aarch64-updater"
  MAC_BUNDLE="$(_find_one "$MAC_DIR" '*.app.tar.gz')"
  MAC_SIG="$(_find_one "$MAC_DIR" '*.app.tar.gz.sig')"
  if [ -z "$MAC_BUNDLE" ] || [ -z "$MAC_SIG" ]; then
    echo "ERROR: build-macos succeeded but macOS updater bundle/.sig not found in $MAC_DIR" >&2
    echo "  bundle='$MAC_BUNDLE' sig='$MAC_SIG' (macOS is load-bearing — refusing to ship a mac-less manifest)" >&2
    exit 1
  fi
  # Fail-closed: an exists-but-EMPTY .sig would embed signature:"" — the updater
  # must never be handed a blank signature (supply-chain integrity). macOS is
  # load-bearing → a blank mac sig fails the whole publish.
  MAC_SIG_CONTENT="$(cat "$MAC_SIG")"
  if [ -z "$MAC_SIG_CONTENT" ]; then
    echo "ERROR: macOS .sig ($MAC_SIG) is EMPTY — refusing to ship a manifest with a blank signature" >&2
    exit 1
  fi
  _add_platform "darwin-aarch64" "$(basename "$MAC_BUNDLE")" "$MAC_SIG_CONTENT"
  echo "  + darwin-aarch64: $(basename "$MAC_BUNDLE")" >&2
else
  echo "  ~ skipping darwin-aarch64 (build-macos did not succeed)" >&2
fi

# ---------------- Windows (windows-x86_64) — best-effort ----------------
if [ "$WINDOWS_BUILT" = "true" ]; then
  WIN_DIR="${ARTIFACTS_DIR}/windows-updater"
  WIN_BUNDLE="$(_find_one "$WIN_DIR" '*.nsis.zip')"
  WIN_SIG="$(_find_one "$WIN_DIR" '*.nsis.zip.sig')"
  WIN_SIG_CONTENT=""
  [ -n "$WIN_SIG" ] && WIN_SIG_CONTENT="$(cat "$WIN_SIG")"
  # Best-effort: omit windows (never emit a blank signature) if bundle/.sig absent OR the .sig is empty.
  if [ -n "$WIN_BUNDLE" ] && [ -n "$WIN_SIG_CONTENT" ]; then
    _add_platform "windows-x86_64" "$(basename "$WIN_BUNDLE")" "$WIN_SIG_CONTENT"
    echo "  + windows-x86_64: $(basename "$WIN_BUNDLE")" >&2
  else
    echo "  ~ omitting windows-x86_64 (updater bundle/.sig absent or empty — best-effort)" >&2
  fi
fi

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
