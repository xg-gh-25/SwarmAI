#!/bin/bash
# Build the elastic Hive image (linux/arm64) from a Hive release package, and optionally
# push it as <version> (design docs/design/2026-11-20-swarm-out-multi-carrier.md §6.3).
#
# Usage: hive/agentcore/build_image.sh <version> [--push <registry/repo>]
#   <version>  the package dist/swarmai-hive-v<version>-linux-arm64.tar.gz (hive/release.sh)
#   --push     tag <registry/repo>:<version> and <registry/repo>:<version>-b<build id> and push
#              both (already logged in). The build id is the package's own (its BUILD.json,
#              backend/scripts/build_identity.py): a Host runs Elastic Workers on the image of
#              its own build (backend/hive/agentcore_provisioner._host_version), so a build of
#              new code under the same version gets its own tag. No moving tag such as latest:
#              an elastic Hive pins the digest its tag serves (agentcore_provisioner.pinned_uri).
#
# Uses docker or finch, whichever is installed (docker first). The build context is a
# temporary directory holding only the package and the supervisor, never the checkout.
# DIST_DIR overrides where the package is read from.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${DIST_DIR:-${ROOT}/dist}"

usage() { echo "usage: $0 <version> [--push <registry/repo>]" >&2; exit 2; }

VERSION=""
PUSH=""
while [ $# -gt 0 ]; do
    case "$1" in
        --push) [ $# -ge 2 ] && [ -n "$2" ] || usage; PUSH="$2"; shift 2 ;;
        -*) usage ;;
        *) [ -z "${VERSION}" ] || usage; VERSION="${1#v}"; shift ;;
    esac
done
[ -n "${VERSION}" ] || usage

if command -v docker >/dev/null 2>&1; then
    TOOL=docker
elif command -v finch >/dev/null 2>&1; then
    TOOL=finch
else
    echo "[agentcore-image] ERROR: neither docker nor finch is installed" >&2
    exit 1
fi

NAME="swarmai-hive-v${VERSION}-linux-arm64.tar.gz"
PACKAGE="${DIST_DIR}/${NAME}"
[ -f "${PACKAGE}" ] || { echo "[agentcore-image] ERROR: ${PACKAGE} not found (build it with hive/release.sh ${VERSION})" >&2; exit 1; }
# The package's own checksum, when it is beside it (release.sh writes checksums.txt;
# the release names it <package>.sha256).
if command -v sha256sum >/dev/null 2>&1; then SHA=(sha256sum); else SHA=(shasum -a 256); fi
# A <package>.sha256 must name the package; checksums.txt may belong to another version.
LINE_RE="^[0-9a-f]{64} [ *]${NAME//./\\.}\$"
if [ -f "${PACKAGE}.sha256" ] && ! grep -Eq "${LINE_RE}" "${PACKAGE}.sha256"; then
    echo "[agentcore-image] ERROR: ${PACKAGE}.sha256 has no line for ${NAME}" >&2
    exit 1
fi
for sum in "${PACKAGE}.sha256" "${DIST_DIR}/checksums.txt"; do
    if [ -f "${sum}" ] && grep -Eq "${LINE_RE}" "${sum}"; then
        (cd "${DIST_DIR}" && grep -E "${LINE_RE}" "${sum}" | "${SHA[@]}" -c --status -) \
            || { echo "[agentcore-image] ERROR: ${NAME} does not match ${sum}" >&2; exit 1; }
        break
    fi
done

CTX="$(mktemp -d)"
trap 'rm -rf "${CTX}"' EXIT
cp "${PACKAGE}" "${CTX}/swarmai-hive.tar.gz"
cp "${SCRIPT_DIR}/supervisor.py" "${CTX}/supervisor.py"

# The package's build id (its BUILD.json; GNU tar needs --wildcards for the pattern, bsdtar
# takes it as one). Only 16 hex digits are taken: anything else is no id.
BUILD_ID=""
if command -v tar >/dev/null 2>&1 && command -v sed >/dev/null 2>&1; then
    BUILD_ID="$( { tar xzf "${PACKAGE}" --wildcards -O '*/BUILD.json' 2>/dev/null \
                   || tar xzf "${PACKAGE}" -O '*/BUILD.json' 2>/dev/null; } \
                 | sed -n 's/^ *"id": *"\([0-9a-f]\{16\}\)",\{0,1\} *$/\1/p' | head -n 1)" || BUILD_ID=""
fi
if [ -z "${BUILD_ID}" ]; then
    echo "[agentcore-image] WARN: ${NAME} carries no build identity (BUILD.json): tagged ${VERSION} only," \
         "so a Host of a known build will not take it" >&2
fi

LOCAL_TAG="swarmai-hive-agentcore:${VERSION}"
TAGS=(-t "${LOCAL_TAG}")
PUSHED=()
if [ -n "${PUSH}" ]; then
    TAGS+=(-t "${PUSH}:${VERSION}")
    PUSHED+=("${PUSH}:${VERSION}")
    if [ -n "${BUILD_ID}" ]; then
        TAGS+=(-t "${PUSH}:${VERSION}-b${BUILD_ID}")
        PUSHED+=("${PUSH}:${VERSION}-b${BUILD_ID}")
    fi
fi

echo "[agentcore-image] building ${LOCAL_TAG} (linux/arm64) with ${TOOL}"
"${TOOL}" build --platform linux/arm64 -f "${SCRIPT_DIR}/Dockerfile" "${TAGS[@]}" "${CTX}"

for ref in ${PUSHED[@]+"${PUSHED[@]}"}; do
    echo "[agentcore-image] pushing ${ref}"
    "${TOOL}" push "${ref}"
done
echo "[agentcore-image] done: ${LOCAL_TAG}${PUSH:+ -> ${PUSHED[*]}}"
