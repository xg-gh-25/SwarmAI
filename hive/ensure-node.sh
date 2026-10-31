#!/bin/bash
# Make the newest installed Node the Hive's default node / npm / npx, and fail when it is
# older than the frontend needs (design docs/design/2026-10-31-swarm-out-single-loop-fixes.md
# §2.7: a Hive on Node 18 could not start vitest, so a run's frontend tests never ran).
#
# Amazon Linux 2023 installs a versioned Node as node-<major> (nodejs20: node-20, npm-20,
# npx-20) next to the default one; the plain `npm` package brings its own older Node as
# `node`. The newest one found is linked into /usr/local/bin, which comes before /usr/bin
# on the PATH of the service, of `sudo -u swarm` and of a login shell.
#
# Usage: ensure-node.sh [MIN_VERSION]   (default: MIN_NODE below; it matches
# desktop/package.json "engines.node", a test keeps the two the same)
# Exit 0: the default Node is at least MIN; 1: none found, or the newest is older.
# ENSURE_NODE_SEARCH / ENSURE_NODE_BIN_DIR override where it looks and links (tests).
set -u

MIN_NODE="20.19.0"
want="${1:-$MIN_NODE}"
search="${ENSURE_NODE_SEARCH:-/usr/bin}"
bin_dir="${ENSURE_NODE_BIN_DIR:-/usr/local/bin}"

# true when version $1 >= version $2 (dotted numbers)
ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

best=""
best_v=""
for cand in "$search"/node-[0-9]* "$search"/node; do
    [ -x "$cand" ] || continue
    v=$("$cand" --version 2>/dev/null | sed 's/^v//')
    case "$v" in [0-9]*) ;; *) continue ;; esac
    if [ -z "$best_v" ] || ! ge "$best_v" "$v"; then
        best="$cand"
        best_v="$v"
    fi
done

if [ -z "$best" ]; then
    echo "ensure-node: no Node found in $search" >&2
    exit 1
fi

suffix="${best##*/node}"            # "-20" for node-20, "" for node
mkdir -p "$bin_dir"
ln -sf "$best" "$bin_dir/node"
for tool in npm npx; do
    if [ -x "$search/$tool$suffix" ]; then
        ln -sf "$search/$tool$suffix" "$bin_dir/$tool"
    fi
done

if ! ge "$best_v" "$want"; then
    echo "ensure-node: the newest Node is $best_v ($best); the frontend needs >= $want" >&2
    exit 1
fi
echo "ensure-node: node $best_v ($best) is the default"
