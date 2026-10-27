#!/bin/bash
# SwarmAI Hive — install-and-boot smoke test for a Hive package
#
# Installs the package the way a Hive's first boot does (backend/hive/user_data.py:
# extract, python3.12 venv, `pip install -e .` in backend/) into a temp dir, starts it
# with hive/swarmai-hive.sh under a temp HOME, and checks that:
#   - /health answers and reports exactly the package's VERSION (what the update step
#     checks before it keeps a new version);
#   - the first boot seeded the workspace: the 5 public context files, config.json and
#     the default project's DDD docs;
#   - a non-public route is refused with 401 when no Hive credential is configured (the
#     app-layer auth fails closed).
# It never touches this machine's own SwarmAI data: the backend starts with an empty
# environment except PATH, HOME and SWARM_DATA_DIR (both in the temp dir) and the port, with
# AWS credential files and the instance metadata service switched off.
#
# Usage: hive/smoke_package.sh <archive> [port]     (PYTHON=python3.12 by default;
#        SMOKE_KEEP=1 keeps the temp dir for inspection)
# Exit codes: 0 all checks passed, 1 a check failed (the backend log tail is printed).

set -euo pipefail

ARCHIVE="${1:?usage: $0 <archive> [port]}"
PORT="${2:-18999}"
PY="${PYTHON:-python3.12}"

WORK="$(mktemp -d)"
APP="${WORK}/app"
HOME_DIR="${WORK}/home"
LOG="${WORK}/hive.log"
mkdir -p "${APP}" "${HOME_DIR}"
PID=""

cleanup() {
    if [ -n "${PID}" ]; then
        kill "${PID}" 2>/dev/null || true
        wait "${PID}" 2>/dev/null || true
    fi
    if [ -n "${SMOKE_KEEP:-}" ]; then
        echo "[smoke] kept ${WORK}" >&2
    else
        rm -rf "${WORK}"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

fail() {
    echo "[smoke] FAIL: $*" >&2
    echo "[smoke] --- backend log (last 60 lines) ---" >&2
    tail -60 "${LOG}" >&2 2>/dev/null || true
    exit 1
}

if curl -s --max-time 2 -o /dev/null "http://127.0.0.1:${PORT}/health"; then
    echo "[smoke] FAIL: something already answers on 127.0.0.1:${PORT}; pick another port" >&2
    exit 1
fi

tar xzf "${ARCHIVE}" --strip-components=1 -C "${APP}"
EXPECTED="$(tr -d '[:space:]' < "${APP}/VERSION")"

echo "[smoke] installing v${EXPECTED} with ${PY}..."
"${PY}" -m venv "${APP}/backend/.venv"
"${APP}/backend/.venv/bin/pip" install -q --upgrade pip
(cd "${APP}/backend" && .venv/bin/pip install -q -e .)

echo "[smoke] starting the Hive on 127.0.0.1:${PORT}..."
env -i PATH="${PATH}" TMPDIR="${TMPDIR:-/tmp}" LANG=C.UTF-8 \
    HOME="${HOME_DIR}" SWARM_DATA_DIR="${HOME_DIR}/.swarm-ai" SWARMAI_PORT="${PORT}" \
    AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true \
    bash "${APP}/hive/swarmai-hive.sh" > "${LOG}" 2>&1 &
PID=$!

HEALTH=""
for _ in $(seq 1 180); do
    if HEALTH="$(curl -sf --max-time 5 "http://127.0.0.1:${PORT}/health")"; then
        break
    fi
    kill -0 "${PID}" 2>/dev/null || fail "the backend exited before /health answered"
    HEALTH=""
    sleep 1
done
[ -n "${HEALTH}" ] || fail "/health did not answer within 180s"

ACTUAL="$(printf '%s' "${HEALTH}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('version', ''))")"
[ "${ACTUAL}" = "${EXPECTED}" ] || fail "/health reports v${ACTUAL}, the package is v${EXPECTED}"
echo "[smoke] ok: /health reports v${ACTUAL}"

WS="${HOME_DIR}/.swarm-ai/SwarmWS"
SEEDED=""
for _ in $(seq 1 60); do
    if [ -f "${WS}/.context/SOUL.md" ] && [ -f "${WS}/config.json" ] \
       && find "${WS}/Projects/SwarmAI" -name 'TECH.md' 2>/dev/null | grep -q .; then
        SEEDED=1
        break
    fi
    sleep 1
done
[ -n "${SEEDED}" ] || fail "the first boot did not seed the workspace (.context/SOUL.md, config.json, Projects/SwarmAI/TECH.md)"
for f in SWARMAI IDENTITY SOUL AGENT SELF; do
    [ -f "${WS}/.context/${f}.md" ] || fail "public context ${f}.md was not seeded"
done
echo "[smoke] ok: workspace seeded (public context, config, default project)"

CODE="$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/hive/instances")"
[ "${CODE}" = "401" ] || fail "a non-public route answered HTTP ${CODE} without credentials (expected 401)"
echo "[smoke] ok: non-public routes refuse without credentials (401)"

echo "[smoke] all checks passed"
