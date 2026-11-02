#!/usr/bin/env bash
# Test for gen-latest-json.sh — drives the REAL script against fixture artifact trees laid
# out like actions/download-artifact@v7 (installer-<target>/ per release target, as written by
# collect-installer.sh). Asserts the emitted latest.json's contents; every target is required.
#
# Run: bash .github/scripts/test_gen_latest_json.sh

set -u
FAILS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN="$SCRIPT_DIR/gen-latest-json.sh"
_pass() { echo "  PASS: $1"; }
_fail() { echo "  FAIL: $1"; FAILS=$((FAILS+1)); }
_mk() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

# all four targets, the names collect-installer.sh gives them
_fixture() {  # _fixture <root>
  local a="$1/artifacts"
  _mk "$a/installer-macos-arm64/SwarmAI_1.27.0_aarch64.dmg" "dmg"
  _mk "$a/installer-macos-arm64/SwarmAI_1.27.0_aarch64.app.tar.gz" "m1"
  _mk "$a/installer-macos-arm64/SwarmAI_1.27.0_aarch64.app.tar.gz.sig" "SIG_MAC_ARM"
  _mk "$a/installer-macos-x64/SwarmAI_1.27.0_x64.dmg" "dmg"
  _mk "$a/installer-macos-x64/SwarmAI_1.27.0_x64.app.tar.gz" "m2"
  _mk "$a/installer-macos-x64/SwarmAI_1.27.0_x64.app.tar.gz.sig" "SIG_MAC_X64"
  _mk "$a/installer-windows-x64/SwarmAI_1.27.0_x64-setup.exe" "w"
  _mk "$a/installer-windows-x64/SwarmAI_1.27.0_x64-setup.exe.sig" "SIG_WIN"
  _mk "$a/installer-linux-x64/SwarmAI_1.27.0_amd64.AppImage" "l"
  _mk "$a/installer-linux-x64/SwarmAI_1.27.0_amd64.AppImage.sig" "SIG_LINUX"
}
_py() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); $2" "$1"; }

echo "TEST_ALL_FOUR_TARGETS"
W="$(mktemp -d)"; _fixture "$W"
if bash "$GEN" 1.27.0 "$W/artifacts" "$W/latest.json" >/dev/null 2>&1; then
  J="$W/latest.json"
  _py "$J" "assert set(d['platforms'])=={'darwin-aarch64','darwin-x86_64','windows-x86_64','linux-x86_64'}" \
    && _pass "four platform keys" || _fail "platform keys wrong"
  _py "$J" "p=d['platforms']; assert [p[k]['signature'] for k in ('darwin-aarch64','darwin-x86_64','windows-x86_64','linux-x86_64')]==['SIG_MAC_ARM','SIG_MAC_X64','SIG_WIN','SIG_LINUX']" \
    && _pass "signature = each target's own .sig contents" || _fail "signatures wrong"
  _py "$J" "p=d['platforms']; assert p['darwin-x86_64']['url'].endswith('/v1.27.0/SwarmAI_1.27.0_x64.app.tar.gz'); assert p['windows-x86_64']['url'].endswith('_x64-setup.exe'); assert p['linux-x86_64']['url'].endswith('.AppImage')" \
    && _pass "urls point at the updater bundles (not the dmg)" || _fail "urls wrong"
  _py "$J" "import re; assert re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$', d['pub_date'])" \
    && _pass "pub_date is RFC3339 UTC" || _fail "pub_date not RFC3339"
else
  _fail "generation errored on the four-target fixture"
fi
rm -rf "$W"

echo "TEST_A_MISSING_TARGET_FAILS"
for gone in installer-linux-x64/SwarmAI_1.27.0_amd64.AppImage.sig installer-macos-x64/SwarmAI_1.27.0_x64.app.tar.gz \
            installer-windows-x64; do
  W="$(mktemp -d)"; _fixture "$W"; rm -rf "$W/artifacts/$gone"
  if bash "$GEN" 1.27.0 "$W/artifacts" "$W/latest.json" >/dev/null 2>&1; then
    _fail "succeeded without $gone"
  else
    _pass "fails without $gone"
  fi
  rm -rf "$W"
done

echo "TEST_EMPTY_SIG_FAILS"
W="$(mktemp -d)"; _fixture "$W"; : > "$W/artifacts/installer-windows-x64/SwarmAI_1.27.0_x64-setup.exe.sig"
bash "$GEN" 1.27.0 "$W/artifacts" "$W/latest.json" >/dev/null 2>&1 && _fail "shipped a blank signature" || _pass "an empty .sig fails"
rm -rf "$W"

echo "TEST_BAD_VERSION_FAILS"
W="$(mktemp -d)"; _fixture "$W"
bash "$GEN" v1.27.0 "$W/artifacts" "$W/latest.json" >/dev/null 2>&1 && _fail "accepted 'v1.27.0'" || _pass "a non-semver version fails"
rm -rf "$W"

echo
if [ "$FAILS" -gt 0 ]; then echo "FAILED: $FAILS"; exit 1; fi
echo "ALL PASS"
