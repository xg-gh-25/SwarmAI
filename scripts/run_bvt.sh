#!/usr/bin/env bash
# The backend Build-Verification-Test run: every file in backend/tests/bvt_manifest.txt
# (the CI pytest allowlist; why an allowlist: the manifest's own header). One command for
# CI (.github/workflows/ci.yml), validations.json and an agent's checks.
#   bash scripts/run_bvt.sh          run them
#   bash scripts/run_bvt.sh --list   print the test paths only
# A listed file that does not exist fails the run before anything runs.
set -euo pipefail
cd "$(dirname "$0")/../backend"
files=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -z "$line" ] && continue
  if [ ! -f "tests/$line" ]; then
    echo "bvt_manifest.txt lists a missing file: $line" >&2
    exit 1
  fi
  files+=("tests/$line")
done < tests/bvt_manifest.txt
if [ "${1:-}" = "--list" ]; then
  printf '%s\n' "${files[@]}"
  exit 0
fi
echo "BVT: running ${#files[@]} curated test files"
SWARMAI_SUITE=1 exec python -m pytest -n 0 --timeout=60 --timeout-method=thread \
  -q --tb=short -p no:cacheprovider -m "not slow and not pbt" "${files[@]}"
