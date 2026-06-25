#!/bin/bash
# SwarmAI daemon guardian — recovers the backend from accidental deregistration.
#
# Invoked by launchd (com.swarmai.guardian) every StartInterval seconds. Each
# invocation is a single probe. Consecutive-dead-probe state is persisted in a
# counter file so the guardian only acts after N consecutive failures — never
# racing a normal SIGKILL+KeepAlive restart (~10s).
#
# Decision logic lives in daemon_guard.guardian_decision (unit-tested). This
# script only: gathers the dead-probe count, calls the guard, and (if told)
# re-bootstraps the backend plist.
#
# Why a guardian at all: C034 — sending bootout to the daemon from one of its
# own child sessions left it deregistered with KeepAlive disabled and nobody to
# restart it (7-minute outage). The sentinel (~/.swarm-ai/.daemon-intentional-down)
# distinguishes intentional stop/upgrade from accidental death.

set -u

# launchd gui/<uid> agents inherit a minimal PATH and do NOT load the login
# shell profile. Set a PATH that includes Homebrew so python3/nc resolve.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:${PATH:-}"

SWARM_HOME="${HOME}/.swarm-ai"
BACKEND_LABEL="com.swarmai.backend"
BACKEND_PORT=18321
BACKEND_PLIST="${HOME}/Library/LaunchAgents/${BACKEND_LABEL}.plist"
COUNTER_FILE="${SWARM_HOME}/.guardian-dead-probes"
SENTINEL_FILE="${SWARM_HOME}/.daemon-intentional-down"
# Standalone copy of daemon_guard.py (pure stdlib), installed next to this
# script. This is the production-robust path: no repo checkout, no PYTHONPATH,
# no frozen-bundle dependency. install_guardian copies it here.
GUARD_PY="${SWARM_HOME}/guardian/daemon_guard.py"
LOCK_FILE="${SWARM_HOME}/.guardian.lock"
REQUIRED_DEAD_PROBES=3
UID_NUM="$(id -u)"
GUI_TARGET="gui/${UID_NUM}/${BACKEND_LABEL}"

# Overlap protection: launchd StartInterval can fire a new run before the
# previous one finishes (e.g. a slow python cold-start on the dead path). A
# non-blocking flock means only one guardian probe runs at a time — prevents
# read-modify-write races on the counter and duplicate bootstrap attempts.
exec 9>"${LOCK_FILE}" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || { echo "[guardian] another probe holds the lock — skipping"; exit 0; }
fi

# Resolve a python3 (used only to run the standalone guard script — stdlib only).
_resolve_python() {
    for cand in /opt/homebrew/bin/python3 /usr/bin/python3 "$(command -v python3)"; do
        [ -n "$cand" ] && [ -x "$cand" ] && { echo "$cand"; return 0; }
    done
    return 1
}

PYTHON="$(_resolve_python)" || { echo "[guardian] no python3 found"; exit 0; }
if [ ! -f "${GUARD_PY}" ]; then
    # Surface the failure (LL18: never silent-degrade). Without the guard script
    # the guardian cannot make decisions — log loudly so a broken install shows.
    echo "[guardian] FATAL: guard script missing at ${GUARD_PY} — recovery disabled"
    exit 0
fi

# ---------------------------------------------------------------------------
# Periodic log rotation — runs on EVERY probe (every StartInterval, ~30s),
# BEFORE the healthy-path early-exit below, so it caps the launchd-captured
# backend logs even when the daemon never restarts (the wrapper only rotates at
# launch). launchd opens StandardOut/ErrorPath in APPEND mode, so truncating by
# path is safe: the daemon's inherited fd re-seeks to EOF and resumes at 0 with
# no sparse-file hole. Keep this in sync with swarmai_backend.sh::_rotate_log.
# ---------------------------------------------------------------------------
_LOG_DIR="${SWARM_HOME}/logs"
_LOG_MAX_BYTES=$((20 * 1024 * 1024))   # rotate when a log exceeds 20MB
_LOG_KEEP_BYTES=$((4 * 1024 * 1024))   # keep last 4MB as the .1 backup

_rotate_log() {
    local f="$1"
    [ -f "$f" ] || return 0
    local size
    size="$(stat -f%z "$f" 2>/dev/null || echo 0)"
    if [ "$size" -gt "$_LOG_MAX_BYTES" ]; then
        [ -f "${f}.1" ] && mv -f "${f}.1" "${f}.2" 2>/dev/null || true
        tail -c "$_LOG_KEEP_BYTES" "$f" > "${f}.1" 2>/dev/null || true
        : > "$f" 2>/dev/null || true
        echo "[guardian] $(date '+%Y-%m-%d %H:%M:%S') rotated $(basename "$f") (was ${size} bytes)"
    fi
    return 0
}

_rotate_log "${_LOG_DIR}/backend-stderr.log"
_rotate_log "${_LOG_DIR}/backend-stdout.log"

# Is the port alive? (cheap, no python needed)
if nc -z 127.0.0.1 "${BACKEND_PORT}" 2>/dev/null; then
    # Healthy — reset the dead-probe counter and exit (no log line on healthy
    # path → guardian-stdout.log does not grow during normal operation).
    echo 0 > "${COUNTER_FILE}" 2>/dev/null || true
    exit 0
fi

# Port dead — increment consecutive-dead counter.
dead=0
[ -f "${COUNTER_FILE}" ] && dead="$(cat "${COUNTER_FILE}" 2>/dev/null || echo 0)"
case "$dead" in (''|*[!0-9]*) dead=0;; esac
dead=$((dead + 1))
echo "$dead" > "${COUNTER_FILE}" 2>/dev/null || true

# Ask the guard whether to bootstrap (it reads registration + sentinel itself).
decision_json="$("${PYTHON}" "${GUARD_PY}" guardian-decision "${dead}" "${REQUIRED_DEAD_PROBES}" 2>/dev/null)"

action="$(echo "${decision_json}" | "${PYTHON}" -c 'import sys,json; print(json.load(sys.stdin).get("action","SKIP"))' 2>/dev/null || echo SKIP)"
reason="$(echo "${decision_json}" | "${PYTHON}" -c 'import sys,json; print(json.load(sys.stdin).get("reason",""))' 2>/dev/null || echo "")"
clear_sentinel="$(echo "${decision_json}" | "${PYTHON}" -c 'import sys,json; print(json.load(sys.stdin).get("clear_sentinel",False))' 2>/dev/null || echo False)"

if [ "${action}" != "SHOULD_BOOTSTRAP" ]; then
    echo "[guardian] SKIP (${reason})"
    exit 0
fi

echo "[guardian] $(date '+%Y-%m-%d %H:%M:%S') recovering daemon — ${reason}"

# Clear a stale sentinel if the guard told us to (crash during upgrade).
if [ "${clear_sentinel}" = "True" ]; then
    "${PYTHON}" "${GUARD_PY}" clear-sentinel 2>/dev/null || true
fi

# Bootstrap the backend (re-register + start). Retry once — launchd can return
# transient "already loaded" if a prior bootout hasn't fully settled.
if [ ! -f "${BACKEND_PLIST}" ]; then
    echo "[guardian] backend plist missing at ${BACKEND_PLIST} — cannot recover"
    exit 0
fi

for attempt in 1 2; do
    # TOCTOU re-check #1: a stop/upgrade may have written the sentinel AFTER our
    # decision but during this loop. Re-read it before every bootstrap — never
    # bootstrap into an intentional-down / mid-rsync window (the core
    # anti-corruption invariant, COE-2026-05-01). The once-computed decision is
    # not enough.
    if [ -f "${SENTINEL_FILE}" ]; then
        echo "[guardian] sentinel appeared mid-loop — aborting bootstrap (intentional down)"
        exit 0
    fi
    # TOCTOU re-check #2: KeepAlive (on a crash, the service stays registered)
    # may have restarted the daemon between our decision and now. If it's back,
    # we're done — never bootout a service we didn't just confirm dead.
    if nc -z 127.0.0.1 "${BACKEND_PORT}" 2>/dev/null; then
        echo "[guardian] daemon came back on its own (attempt ${attempt}) — no action"
        echo 0 > "${COUNTER_FILE}" 2>/dev/null || true
        exit 0
    fi

    launchctl bootstrap "gui/${UID_NUM}" "${BACKEND_PLIST}" 2>/dev/null
    rc=$?
    # rc 0 = bootstrapped. rc 5 (I/O error) / 37 (already in progress) = the
    # service is ALREADY loaded — treat as success, do NOT bootout (that would
    # kill a service that is up or coming up).
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 5 ] || [ "$rc" -eq 37 ]; then
        echo "[guardian] bootstrap ok (attempt ${attempt}, rc=${rc})"
        echo 0 > "${COUNTER_FILE}" 2>/dev/null || true
        exit 0
    fi
    # Genuine failure (not already-loaded). Wait for launchd to settle, then
    # bootout ONLY if still dead, before retrying.
    sleep 2
    if ! nc -z 127.0.0.1 "${BACKEND_PORT}" 2>/dev/null; then
        launchctl bootout "${GUI_TARGET}" 2>/dev/null || true
    fi
    sleep 1
done

echo "[guardian] bootstrap FAILED after 2 attempts — leaving for next probe"
exit 0
