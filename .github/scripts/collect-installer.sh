#!/usr/bin/env bash
# Collect one release target's installer and updater files into OUT_DIR, under names that are
# unique across targets (both macOS builds produce a bundle named SwarmAI.app.tar.gz).
#
# Usage: collect-installer.sh <NAME> <VERSION> <BUNDLE_DIR> <OUT_DIR>
#   NAME        macos-arm64 | macos-x64 | windows-x64 | linux-x64 (release.yml matrix name)
#   BUNDLE_DIR  desktop/src-tauri/target/<target>/release/bundle
# The installer is required (exit 1 without it). The updater .sig exists only when the build
# had TAURI_SIGNING_PRIVATE_KEY; without it this warns and the publish job's latest.json step
# fails, so an unsigned updater never ships.
set -euo pipefail
NAME="${1:?NAME}"; VERSION="${2:?VERSION}"; BUNDLE="${3:?BUNDLE_DIR}"; OUT="${4:?OUT_DIR}"
mkdir -p "$OUT"

one() {  # one <dir> <glob>: the single match, empty if none, error if several
  local m; m=()
  while IFS= read -r f; do m+=("$f"); done < <(find "$1" -maxdepth 1 -type f -name "$2" 2>/dev/null)
  [ "${#m[@]}" -le 1 ] || { echo "ambiguous '$2' in $1: ${m[*]}" >&2; exit 1; }
  echo "${m[0]:-}"
}
need() { [ -n "$1" ] || { echo "ERROR: $2 not found" >&2; exit 1; }; cp "$1" "$OUT/${3:-$(basename "$1")}"; }
want() { if [ -n "$1" ]; then cp "$1" "$OUT/$2"; else echo "WARNING: $3 not found (unsigned build?)" >&2; fi; }

case "$NAME" in
  macos-arm64|macos-x64)
    arch=aarch64; [ "$NAME" = macos-x64 ] && arch=x64
    need "$(one "$BUNDLE/dmg" '*.dmg')" "the .dmg"
    bundle="$(one "$BUNDLE/macos" '*.app.tar.gz')"
    want "$bundle" "SwarmAI_${VERSION}_${arch}.app.tar.gz" "the .app.tar.gz updater bundle"
    want "$(one "$BUNDLE/macos" '*.app.tar.gz.sig')" "SwarmAI_${VERSION}_${arch}.app.tar.gz.sig" "its .sig" ;;
  windows-x64)
    exe="$(one "$BUNDLE/nsis" '*-setup.exe')"
    need "$exe" "the NSIS -setup.exe"
    want "$(one "$BUNDLE/nsis" '*-setup.exe.sig')" "$(basename "${exe:-x}").sig" "its .sig" ;;
  linux-x64)
    img="$(one "$BUNDLE/appimage" '*.AppImage')"
    need "$img" "the .AppImage"
    want "$(one "$BUNDLE/appimage" '*.AppImage.sig')" "$(basename "${img:-x}").sig" "its .sig" ;;
  *) echo "unknown target name: $NAME" >&2; exit 64 ;;
esac
ls -la "$OUT"
