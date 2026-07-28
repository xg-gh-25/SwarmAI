#!/usr/bin/env bash
# Test for gen-latest-json.sh — drives the REAL script against fixture artifact
# trees mimicking actions/download-artifact@v4 layout (each artifact in a subdir).
# NON-VACUOUS: asserts the emitted latest.json's actual contents; mutation check —
# break a contract line in gen-latest-json.sh and the matching test goes RED.
#
# Run: bash .github/scripts/test_gen_latest_json.sh

set -u
FAILS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN="$SCRIPT_DIR/gen-latest-json.sh"
_pass() { echo "  PASS: $1"; }
_fail() { echo "  FAIL: $1"; FAILS=$((FAILS+1)); }

# fixture builder: makes artifacts/<name>/<file> with given content
_mk() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

# ── Test 1: macOS + Windows both present → both platforms, correct keys/urls/sigs ──
echo "TEST_BOTH_PLATFORMS"
W1="$(mktemp -d)"
_mk "$W1/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "macbytes"
_mk "$W1/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" "MAC_SIG_BLOB_abc"
_mk "$W1/artifacts/windows-updater/SwarmAI_1.27.0_x64-setup.nsis.zip" "winbytes"
_mk "$W1/artifacts/windows-updater/SwarmAI_1.27.0_x64-setup.nsis.zip.sig" "WIN_SIG_BLOB_xyz"
# hive tar.gz in a sibling artifact — MUST NOT be picked up as the macOS bundle (Gate-1 #2)
_mk "$W1/artifacts/hive-linux-arm64/swarmai-hive-v1.27.0-linux-arm64.tar.gz" "hivebytes"
if bash "$GEN" 1.27.0 "$W1/artifacts" "$W1/latest.json" true true >/dev/null 2>&1; then
  J="$W1/latest.json"
  python3 -c "import json,sys; d=json.load(open('$J')); sys.exit(0 if set(d['platforms'])=={'darwin-aarch64','windows-x86_64'} else 1)" \
    && _pass "both platform keys present (darwin-aarch64 + windows-x86_64)" \
    || _fail "wrong platform keys: $(python3 -c "import json;print(list(json.load(open('$J'))['platforms']))")"
  # signature == .sig CONTENTS (AC4)
  python3 -c "import json; d=json.load(open('$J')); assert d['platforms']['darwin-aarch64']['signature']=='MAC_SIG_BLOB_abc'" \
    && _pass "darwin signature = .sig contents" || _fail "darwin signature wrong"
  # url points at the BUNDLE .app.tar.gz, NOT hive/dmg (AC5 + Gate-1 #2 collision)
  python3 -c "import json; u=json.load(open('$J'))['platforms']['darwin-aarch64']['url']; assert u.endswith('/v1.27.0/SwarmAI.app.tar.gz'), u" \
    && _pass "darwin url = bundle (no hive collision)" || _fail "darwin url wrong (collision?): $(python3 -c "import json;print(json.load(open('$J'))['platforms']['darwin-aarch64']['url'])")"
  python3 -c "import json; u=json.load(open('$J'))['platforms']['windows-x86_64']['url']; assert u.endswith('-setup.nsis.zip'), u" \
    && _pass "windows url = nsis.zip bundle" || _fail "windows url wrong"
  # pub_date RFC3339 (AC / Gate-1 #5)
  python3 -c "import json,re; d=json.load(open('$J')); assert re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$', d['pub_date']), d['pub_date']" \
    && _pass "pub_date is RFC3339 UTC" || _fail "pub_date not RFC3339"
else
  _fail "generation errored on the both-platforms fixture"
fi
rm -rf "$W1"

# ── Test 2: Windows ABSENT → windows platform OMITTED, macOS still present (AC6) ──
echo "TEST_WINDOWS_OMITTED"
W2="$(mktemp -d)"
_mk "$W2/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "m"
_mk "$W2/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" "MSIG"
if bash "$GEN" 1.27.0 "$W2/artifacts" "$W2/latest.json" true true >/dev/null 2>&1; then
  python3 -c "import json; d=json.load(open('$W2/latest.json')); assert 'windows-x86_64' not in d['platforms'] and 'darwin-aarch64' in d['platforms']" \
    && _pass "windows omitted (not empty entry), darwin present" || _fail "windows handling wrong"
else
  _fail "generation errored when windows absent (should succeed mac-only)"
fi
rm -rf "$W2"

# ── Test 3: macOS BUILT but bundle MISSING → FAIL LOUD (AC8, load-bearing) ──
echo "TEST_MACOS_MISSING_FAILS_LOUD"
W3="$(mktemp -d)"; mkdir -p "$W3/artifacts/hive-linux-arm64"
_mk "$W3/artifacts/hive-linux-arm64/swarmai-hive-v1.27.0-linux-arm64.tar.gz" "h"
if bash "$GEN" 1.27.0 "$W3/artifacts" "$W3/latest.json" true true >/dev/null 2>&1; then
  _fail "generation SUCCEEDED with macOS built-but-missing (should fail loud)"
else
  [ ! -f "$W3/latest.json" ] && _pass "failed loud + wrote no manifest when macOS expected-but-absent" \
    || _fail "failed but still wrote a manifest"
fi
rm -rf "$W3"

# ── Test 4: macOS NOT built (hive-only release) → graceful skip, NO hard fail (Gate-1 #4) ──
echo "TEST_MACOS_NOT_BUILT_GRACEFUL"
W4="$(mktemp -d)"
_mk "$W4/artifacts/windows-updater/App-setup.nsis.zip" "w"
_mk "$W4/artifacts/windows-updater/App-setup.nsis.zip.sig" "WSIG"
# MACOS_BUILT=false → must NOT fail, must emit windows-only manifest
if bash "$GEN" 1.27.0 "$W4/artifacts" "$W4/latest.json" false true >/dev/null 2>&1; then
  python3 -c "import json; d=json.load(open('$W4/latest.json')); assert 'darwin-aarch64' not in d['platforms'] and 'windows-x86_64' in d['platforms']" \
    && _pass "macOS-not-built → graceful skip, windows-only manifest, publish NOT failed" || _fail "unexpected platforms when macOS not built"
else
  _fail "generation hard-failed when macOS not built (regression: would break hive-only release)"
fi
rm -rf "$W4"

# ── Test 5: AMBIGUOUS macOS bundle (two *.app.tar.gz) → FAIL LOUD (_find_one guard) ──
echo "TEST_AMBIGUOUS_BUNDLE_FAILS"
W5="$(mktemp -d)"
_mk "$W5/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "a"
_mk "$W5/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" "SIGA"
_mk "$W5/artifacts/macos-aarch64-updater/Other.app.tar.gz" "b"   # a 2nd bundle → ambiguous
if bash "$GEN" 1.27.0 "$W5/artifacts" "$W5/latest.json" true false >/dev/null 2>&1; then
  _fail "generation SUCCEEDED with two *.app.tar.gz (should fail on ambiguity)"
else
  _pass "ambiguous macOS bundle → fail loud (no silent wrong-bundle pick)"
fi
rm -rf "$W5"

# ── Test 6: EMPTY macOS .sig → FAIL LOUD (supply-chain: never blank signature) ──
echo "TEST_EMPTY_MACOS_SIG_FAILS"
W6="$(mktemp -d)"
_mk "$W6/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "m"
_mk "$W6/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" ""   # exists but EMPTY
if bash "$GEN" 1.27.0 "$W6/artifacts" "$W6/latest.json" true false >/dev/null 2>&1; then
  _fail "generation SUCCEEDED with empty macOS .sig (should fail — blank signature)"
else
  [ ! -f "$W6/latest.json" ] && _pass "empty macOS .sig → fail loud, no manifest" || _fail "failed but wrote manifest"
fi
rm -rf "$W6"

# ── Test 7: EMPTY windows .sig → windows OMITTED, macOS still ships (best-effort) ──
echo "TEST_EMPTY_WINDOWS_SIG_OMITTED"
W7="$(mktemp -d)"
_mk "$W7/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "m"
_mk "$W7/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" "MSIG"
_mk "$W7/artifacts/windows-updater/App-setup.nsis.zip" "w"
_mk "$W7/artifacts/windows-updater/App-setup.nsis.zip.sig" ""   # exists but EMPTY
if bash "$GEN" 1.27.0 "$W7/artifacts" "$W7/latest.json" true true >/dev/null 2>&1; then
  python3 -c "import json; d=json.load(open('$W7/latest.json')); assert 'windows-x86_64' not in d['platforms'] and 'darwin-aarch64' in d['platforms']" \
    && _pass "empty windows .sig → windows omitted (no blank sig), macOS ships" || _fail "empty-windows-sig handling wrong"
else
  _fail "generation hard-failed on empty windows .sig (should be best-effort omit)"
fi
rm -rf "$W7"

# ── Test 8: malformed VERSION → FAIL LOUD (semver guard) ──
echo "TEST_MALFORMED_VERSION_FAILS"
W8="$(mktemp -d)"
_mk "$W8/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz" "m"
_mk "$W8/artifacts/macos-aarch64-updater/SwarmAI.app.tar.gz.sig" "MSIG"
FAILED_ALL=0
for badv in "v1.27.0" "1.27" "1.27.0-rc1" "1.27.0.0" ""; do
  if bash "$GEN" "$badv" "$W8/artifacts" "$W8/out.json" true false >/dev/null 2>&1; then
    _fail "accepted malformed VERSION '$badv' (should reject)"; FAILED_ALL=1
  fi
done
[ "$FAILED_ALL" -eq 0 ] && _pass "all malformed VERSIONs rejected (v-prefix, 2-part, pre-release, 4-part, empty)"
# and a good one still passes
bash "$GEN" "1.27.0" "$W8/artifacts" "$W8/out.json" true false >/dev/null 2>&1 \
  && _pass "valid semver 1.27.0 accepted" || _fail "valid semver rejected (guard too strict)"
rm -rf "$W8"

echo
if [ "$FAILS" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
