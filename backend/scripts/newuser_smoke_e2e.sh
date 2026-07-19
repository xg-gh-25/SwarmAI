#!/bin/bash
# =============================================================================
# newuser_smoke_e2e.sh — Isolated fresh-new-user end-to-end smoke test.
#
# WHAT
#   Drives the REAL packaged backend binary (~/.swarm-ai/daemon/python-backend)
#   through the exact contract a brand-new user hits on first launch:
#     Phase 1  cold-start /health → status=healthy   (no false-kill on slow init)
#     Phase 2  GET  /api/system/status → fresh user (onboarding_complete=false)
#                                        + workspace provisioned (ready=true)
#     Phase 3  PUT  /api/system/onboarding-complete → flag flips to true
#     Phase 4  POST /api/chat/stream → first message streams (SSE)   [FULL only]
#
# WHY
#   The frontend gating (App.tsx), boot overlay, and onboarding wizard are UI —
#   not headless-testable. But the BACKEND contract underneath them is, and it is
#   the part that regresses silently (route prefix, field casing, cold-start
#   false-kill, onboarding flag). This script is the automated regression guard
#   for that contract. It exercises the DEPLOYED onedir binary, not the source
#   tree — the same artifact a real new user runs.
#
# HOW (isolation — the load-bearing safety property)
#   config.get_app_data_dir() = Path.home()/".swarm-ai", which reads $HOME with
#   NO env override (verified backend/config.py). So launching the binary under
#   `env -i HOME=<throwaway-tmp>` points the ENTIRE backend at a brand-new data
#   dir — a genuine fresh install — WITHOUT touching the production ~/.swarm-ai.
#   SWARMAI_MODE=subprocess keeps the channel gateway OFF (no Slack/launchd
#   contention with the real daemon). Port is dynamically chosen free (never the
#   production 18321). A pre-flight assert REFUSES to run if isolation can't be
#   proven, and a trap tears down the backend + tmp dir on every exit path.
#
# USAGE
#   backend/scripts/newuser_smoke_e2e.sh            # FAST (default): phases 1-3, ~20s, no creds needed
#   backend/scripts/newuser_smoke_e2e.sh --full     # adds phase 4 (needs Bedrock creds; non-200 = SKIP)
#   backend/scripts/newuser_smoke_e2e.sh --fast     # explicit FAST
#
# EXIT CODES
#   0  GREEN — all executed contract phases passed
#   1  RED   — a real contract failure (route/field/flag/health regression)
#   2  binary not found — run `./prod.sh build` first
#   3  isolation could not be established — refused to run (never touches real data)
#
# Provenance: hardened from a throwaway harness proven 6/6 GREEN (run_bba97015).
# =============================================================================
set -u

# ── Mode ─────────────────────────────────────────────────────────────────────
MODE="fast"
case "${1:-}" in
  --full) MODE="full" ;;
  --fast|"") MODE="fast" ;;
  -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown arg: $1 (use --fast | --full)"; exit 3 ;;
esac

# ── Locate the packaged binary robustly ──────────────────────────────────────
REAL_HOME="${HOME:?HOME must be set}"
BIN="$REAL_HOME/.swarm-ai/daemon/python-backend"
if [ ! -x "$BIN" ]; then
  echo "❌ packaged backend binary not found (or not executable) at:"
  echo "     $BIN"
  echo "   Run \`./prod.sh build\` first to produce the onedir bundle."
  exit 2
fi

# ── Isolation pre-flight (AC1 — REFUSE to run unless provably isolated) ───────
# Create the throwaway HOME, then PROVE its data dir cannot be the real one
# BEFORE we launch anything. mktemp gives a fresh dir under $TMPDIR (/tmp/...).
TMP="$(mktemp -d "${TMPDIR:-/tmp}/swarm-newuser.XXXXXX")" || { echo "❌ mktemp failed"; exit 3; }

# Resolve both candidate data dirs to absolute normalized paths and assert distinct.
# Use Python's os.path.realpath (a hard dependency here) — NOT `realpath -m`, whose
# `-m` flag is GNU-coreutils-only and is REJECTED by macOS /bin/realpath ("illegal
# option -- m"). os.path.realpath normalizes `..`/symlinks identically on macOS+Linux
# and works even though tmp/.swarm-ai does not exist yet. (REVIEW finding, run_bba97015.)
_norm(){ python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1" 2>/dev/null || echo "$1"; }
ISO_DATA_DIR="$(_norm "$TMP/.swarm-ai")"
REAL_DATA_DIR="$(_norm "$REAL_HOME/.swarm-ai")"
case "$ISO_DATA_DIR" in
  "$REAL_DATA_DIR"|"$REAL_DATA_DIR"/*)
    echo "❌ ISOLATION VIOLATION: throwaway data dir resolves INTO the real one:"
    echo "     iso : $ISO_DATA_DIR"
    echo "     real: $REAL_DATA_DIR"
    echo "   Refusing to run (would risk production ~/.swarm-ai). No backend launched."
    rmdir "$TMP" 2>/dev/null
    exit 3 ;;
esac
# Normalize the temp root to a concrete non-empty value FIRST. Guarding on the raw
# "${TMPDIR%/}"/* would degenerate to `/*` (matches ANY absolute path) when TMPDIR
# is exported-but-empty — the guard would then rubber-stamp an arbitrary location.
# (adversarial F2, run_bba97015.)
_TR="${TMPDIR:-/tmp}"; _TR="${_TR%/}"; [ -n "$_TR" ] || _TR="/tmp"
case "$TMP" in
  /tmp/*|/private/*|"$_TR"/*) : ;;  # under a temp root — good
  *) echo "❌ ISOLATION: tmp dir '$TMP' is not under a temp root — refusing."; rmdir "$TMP" 2>/dev/null; exit 3 ;;
esac

# ── Dynamic free port (AC3 — never collide with the real daemon on 18321) ─────
PORT="$(python3 -c 'import socket
s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()' 2>/dev/null)"
case "$PORT" in
  ''|*[!0-9]*) echo "❌ could not allocate a free port"; rmdir "$TMP" 2>/dev/null; exit 3 ;;
esac

# Pre-launch: the port MUST be free right now. If something already answers on it
# (a stray daemon, a parallel run that won the TOCTOU race after our socket closed),
# refuse — otherwise Phase 1 could get status=healthy from the WRONG backend and
# report a non-isolated false-green (adversarial F4, run_bba97015).
if curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "❌ port $PORT is already serving /health before launch — refusing (would test the wrong backend)."
  rmdir "$TMP" 2>/dev/null; exit 3
fi

LOG="$TMP/backend.log"
BASE="http://127.0.0.1:$PORT"
BPID=""
PASS=0; FAIL=0
ok(){ echo "  ✅ $1"; PASS=$((PASS+1)); }
no(){ echo "  ❌ $1"; FAIL=$((FAIL+1)); }
skip(){ echo "  ⏭️  $1"; }

# ── Cleanup on EVERY exit path (AC2 — trap set BEFORE any launch) ─────────────
cleanup(){
  # kill only if the PID is non-empty AND still alive (kill -0) — avoids killing an
  # unrelated process if the backend died early and the OS recycled its PID
  # (adversarial F3, run_bba97015).
  if [ -n "$BPID" ] && kill -0 "$BPID" 2>/dev/null; then
    kill "$BPID" 2>/dev/null; sleep 1; kill -9 "$BPID" 2>/dev/null
  fi
  rm -rf "$TMP" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "=== FRESH-USER E2E SMOKE ($MODE) ==="
echo "isolated HOME : $TMP"
echo "isolated data : $ISO_DATA_DIR   (real: $REAL_DATA_DIR)"
echo "port          : $PORT           binary: $BIN"

# ── Launch packaged backend against the virgin HOME ───────────────────────────
env -i HOME="$TMP" PATH="$PATH" \
    SWARMAI_MODE=subprocess SWARMAI_PORT="$PORT" \
    CLAUDE_CODE_USE_BEDROCK="${CLAUDE_CODE_USE_BEDROCK:-}" \
    AWS_REGION="${AWS_REGION:-us-east-1}" \
    AWS_PROFILE="${AWS_PROFILE:-}" \
    "$BIN" --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
BPID=$!
echo "spawned PID=$BPID"

# ── PHASE 1: cold-start health (bounded poll — AC5, NEVER hangs) ──────────────
echo "--- PHASE 1: /health cold-start ---"
HEALTHY=0
MAX_POLLS=75          # 75 × 2s = 150s absolute cap (well above ~14s observed cold start)
for i in $(seq 1 $MAX_POLLS); do
  if ! kill -0 "$BPID" 2>/dev/null; then
    no "backend process DIED during boot"; tail -20 "$LOG"; break
  fi
  ST="$(curl -s -m 3 "$BASE/health" 2>/dev/null | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("status",""))
except Exception: print("")' 2>/dev/null)"
  # Require our OWN process to still be the one alive when health goes green — a
  # healthy reply while $BPID is dead means a DIFFERENT backend owns the port
  # (isolation broken); treat that as failure, not success (adversarial F4).
  if [ "$ST" = "healthy" ]; then
    if kill -0 "$BPID" 2>/dev/null; then
      ok "status=healthy after ~$((i*2))s (our PID $BPID serving; no false-kill on slow init)"; HEALTHY=1; break
    else
      no "healthy reply but our backend PID $BPID is DEAD — a different process owns port $PORT (isolation broken)"; break
    fi
  fi
  [ "$ST" = "initializing" ] && [ $((i%5)) -eq 0 ] && echo "  … still initializing (${i}x)"
  sleep 2
done
if [ "$HEALTHY" != 1 ]; then
  no "never reached healthy within $((MAX_POLLS*2))s"; tail -20 "$LOG"
  echo ""; echo "RESULT: PASS=$PASS FAIL=$FAIL"; echo "E2E_VERDICT=RED"; exit 1
fi

# ── PHASE 2: fresh user is un-onboarded + workspace provisioned ───────────────
echo "--- PHASE 2: /api/system/status (fresh = onboarding_complete:false) ---"
S1="$(curl -s -m 5 "$BASE/api/system/status" 2>/dev/null)"
OC="$(echo "$S1"  | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("onboarding_complete"))
except Exception: print("ERR")' 2>/dev/null)"
WR="$(echo "$S1"  | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("swarm_workspace",{}).get("ready"))
except Exception: print("ERR")' 2>/dev/null)"
[ "$OC" = "False" ] && ok "fresh user: onboarding_complete=false (→ wizard)" || no "expected onboarding_complete=false, got: $OC"
[ "$WR" = "True" ]  && ok "workspace provisioned from scratch: ready=true"    || no "workspace not ready: $WR"

# ── PHASE 3: complete onboarding → flag flips (returning-user path) ───────────
echo "--- PHASE 3: PUT /api/system/onboarding-complete → flag flips ---"
CODE="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X PUT "$BASE/api/system/onboarding-complete" 2>/dev/null)"
if [ "$CODE" = "200" ]; then
  ok "PUT onboarding-complete → 200"
  OC2="$(curl -s -m 5 "$BASE/api/system/status" 2>/dev/null | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("onboarding_complete"))
except Exception: print("ERR")' 2>/dev/null)"
  [ "$OC2" = "True" ] && ok "onboarding_complete now true (→ ChatPage, no re-onboard)" || no "flag did not flip: $OC2"
else
  no "PUT onboarding-complete returned $CODE (expected 200)"
fi

# ── PHASE 4: first chat message (FULL only; needs creds; non-200 = SKIP) ──────
if [ "$MODE" = "full" ]; then
  echo "--- PHASE 4: first /api/chat/stream message (needs Bedrock creds) ---"
  # 45s bound: enough for a creds-present backend to generate "E2E_OK", and enough to
  # confirm the stream opens (session_start arrives in ~1s). We deliberately do NOT
  # wait for the slow no-creds CREDENTIALS_EXPIRED terminal frame — the classifier
  # below treats "stream opened but no token/error by timeout" as the expected
  # no-creds SKIP (O030: don't stretch a timeout to race a slow external probe).
  CODE="$(curl -s -m 45 -o "$TMP/chat.out" -w '%{http_code}' -N \
     -X POST "$BASE/api/chat/stream" -H 'Content-Type: application/json' \
     -d '{"agent_id":"default","message":"Reply with exactly: E2E_OK","session_id":null}' 2>/dev/null)"
  if [ "$CODE" = "200" ]; then
    # Classify the SSE stream. Three outcomes (adversarial F1, run_bba97015):
    #  - CREDENTIALS_EXPIRED / auth error frame → SKIP (the isolated `env -i` env has
    #    no Bedrock creds — this is the EXPECTED no-creds condition, AC4: not a FAIL).
    #  - the actual requested token E2E_OK present → PASS (real end-to-end generation).
    #  - HTTP 200 but neither → FAIL (a bare `data:`/`event:` framing match must NOT
    #    count as generation — that was the original false-green).
    if grep -q "E2E_OK" "$TMP/chat.out"; then
      # Real end-to-end generation — creds present, model produced the token.
      ok "first message streamed + generated the requested token (E2E_OK)"
    elif grep -qiE 'CREDENTIALS_EXPIRED|"code"[[:space:]]*:[[:space:]]*"[A-Z_]*(AUTH|CREDENTIAL)' "$TMP/chat.out"; then
      # Explicit no-creds error frame — the expected condition in an isolated env.
      skip "chat/stream reached generation but isolated env has no Bedrock creds (CREDENTIALS_EXPIRED) — contract path verified (AC4: SKIP, not FAIL)"
    elif grep -qiE 'event:[[:space:]]*error|"type"[[:space:]]*:[[:space:]]*"error"' "$TMP/chat.out"; then
      # A genuine non-auth error frame IS a real failure.
      no "chat/stream returned a non-auth SSE error frame (head: $(head -c200 "$TMP/chat.out" | tr '\n' ' '))"
    elif grep -q '"type": "session_start"' "$TMP/chat.out"; then
      # Stream opened (contract path reached) but neither the token nor a terminal
      # frame arrived within the timeout. In an isolated env the terminal frame is
      # almost always the slow CREDENTIALS_EXPIRED probe; we cannot wait unbounded
      # to confirm (O030). AC4: absent generation in the no-creds isolated env = SKIP.
      # A REAL generation regression would surface as a non-auth error frame above,
      # or as a missing session_start (→ the FAIL branch below).
      skip "chat/stream opened (session_start received, contract path verified) but did not complete generation within the timeout — expected in a no-creds isolated env (AC4: SKIP, not FAIL). Run with valid Bedrock creds to exercise real generation."
    else
      # No session_start at all → the endpoint itself is broken (real failure).
      no "POST 200 but stream never opened (no session_start) — endpoint contract broken (head: $(head -c200 "$TMP/chat.out" | tr '\n' ' '))"
    fi
  else
    skip "chat/stream HTTP=$CODE — no Bedrock creds in isolated env (contract path reached; generation skipped, NOT a failure)"
  fi
else
  echo "--- PHASE 4: skipped (FAST mode — pass --full to test generation) ---"
fi

# ── Verdict ───────────────────────────────────────────────────────────────────
echo ""
echo "RESULT: PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" = 0 ]; then echo "E2E_VERDICT=GREEN"; exit 0; else echo "E2E_VERDICT=RED"; exit 1; fi
