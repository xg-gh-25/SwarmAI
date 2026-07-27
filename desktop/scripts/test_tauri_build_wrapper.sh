#!/bin/bash
# Test for tauri-build.sh — the signing-key-aware build wrapper.
#
# NON-VACUOUS: drives the REAL desktop/scripts/tauri-build.sh via a PATH-stubbed
# `npm` that records argv, then asserts the overlay is present IFF the signing key
# is absent. Mutation check: revert the `if [ -z ... ]` guard in tauri-build.sh and
# TEST_KEY_UNSET_ADDS_OVERLAY goes RED.
#
# Run: bash desktop/scripts/test_tauri_build_wrapper.sh   (from repo root or anywhere)

set -u
FAILS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/tauri-build.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# PATH-stub npm: records every arg (one per line) to $WORK/argv, exits 0.
mkdir -p "$WORK/stubbin"
cat > "$WORK/stubbin/npm" <<'STUB'
#!/bin/bash
: > "$ARGV_OUT"
for a in "$@"; do printf '%s\n' "$a" >> "$ARGV_OUT"; done
exit 0
STUB
chmod +x "$WORK/stubbin/npm"

_pass() { echo "  PASS: $1"; }
_fail() { echo "  FAIL: $1"; FAILS=$((FAILS+1)); }

# ── Test 1: key UNSET → overlay createUpdaterArtifacts:false present ──
run_unset() {
  ARGV_OUT="$WORK/argv1"
  ( unset TAURI_SIGNING_PRIVATE_KEY
    PATH="$WORK/stubbin:$PATH" ARGV_OUT="$ARGV_OUT" bash "$HELPER" >/dev/null 2>&1 )
  ARGV_OUT="$WORK/argv1"
}
echo "TEST_KEY_UNSET_ADDS_OVERLAY"
run_unset
if grep -qF '{"bundle":{"createUpdaterArtifacts":false}}' "$WORK/argv1"; then
  _pass "overlay JSON forwarded when key unset"
else
  _fail "overlay JSON MISSING when key unset (argv: $(tr '\n' ' ' < "$WORK/argv1"))"
fi
# and it must include build + config flag
grep -qxF 'build' "$WORK/argv1" && grep -qxF -- '--config' "$WORK/argv1" \
  && _pass "argv has build + --config" || _fail "argv missing build/--config"

# ── Test 2: key SET → NO overlay (base config governs, CI signs) ──
echo "TEST_KEY_SET_NO_OVERLAY"
ARGV_OUT="$WORK/argv2"
PATH="$WORK/stubbin:$PATH" ARGV_OUT="$ARGV_OUT" TAURI_SIGNING_PRIVATE_KEY="dummy-key" bash "$HELPER" >/dev/null 2>&1
if grep -qF 'createUpdaterArtifacts' "$WORK/argv2"; then
  _fail "overlay LEAKED when key set (would strip CI signing!) argv: $(tr '\n' ' ' < "$WORK/argv2")"
else
  _pass "no overlay when key set — base config signs unchanged"
fi

# ── Test 3: passthrough args forwarded in BOTH branches (CI uses --target X) ──
echo "TEST_PASSTHROUGH_ARGS"
ARGV_OUT="$WORK/argv3"
PATH="$WORK/stubbin:$PATH" ARGV_OUT="$ARGV_OUT" TAURI_SIGNING_PRIVATE_KEY="dummy" bash "$HELPER" --target aarch64-apple-darwin >/dev/null 2>&1
grep -qxF -- '--target' "$WORK/argv3" && grep -qxF 'aarch64-apple-darwin' "$WORK/argv3" \
  && _pass "passthrough --target forwarded (key set)" || _fail "passthrough --target dropped (key set)"
ARGV_OUT="$WORK/argv4"
( unset TAURI_SIGNING_PRIVATE_KEY
  PATH="$WORK/stubbin:$PATH" ARGV_OUT="$WORK/argv4" bash "$HELPER" --target x86_64-apple-darwin >/dev/null 2>&1 )
grep -qxF -- '--target' "$WORK/argv4" && grep -qxF 'x86_64-apple-darwin' "$WORK/argv4" \
  && _pass "passthrough --target forwarded (key unset, alongside overlay)" || _fail "passthrough --target dropped (key unset)"

echo
if [ "$FAILS" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
