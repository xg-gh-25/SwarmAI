#!/bin/bash
# Shared Tauri build wrapper — signing-key-aware.
#
# WHY THIS EXISTS
# ---------------
# tauri.conf.json sets `bundle.createUpdaterArtifacts: true`, which makes Tauri
# SIGN the updater artifacts on every `tauri build`. Signing requires the env var
# TAURI_SIGNING_PRIVATE_KEY. All CI paths (release/build-macos/build-windows/
# dev-build) inject that key from GitHub secrets, so they sign normally. A LOCAL
# developer build has no key, so `tauri build` used to fail at the signing tail
# step with: "A public key has been found, but no private key ...".
#
# This wrapper keys off TAURI_SIGNING_PRIVATE_KEY presence — the exact discriminator
# between "should sign" (CI, key present) and "can't sign" (local dev, key absent):
#   • key SET   → plain `tauri build` → base config (createUpdaterArtifacts:true) signs.
#   • key UNSET → overlay `bundle.createUpdaterArtifacts:false` via --config so the
#                 local build skips the updater-artifact signing step and succeeds.
#
# The base tauri.conf.json is UNCHANGED (createUpdaterArtifacts stays true) — a safe
# default: any build context that should sign does, unless it explicitly lacks a key.
# tauri --config deep-merges (RFC7386, per-key), so only createUpdaterArtifacts is
# overridden; icons/resources/targets are preserved.
#
# The updater FEATURE is untouched — this only affects whether *new local build
# artifacts* carry a signature; it does not disable the updater plugin or its
# frontend callers, and it never runs in CI (where the key is always set).
#
# Any args passed to this script are forwarded to `tauri build` (e.g. --target).
# Run from the `desktop/` directory (same cwd as the call sites it replaces).
#
# NOTE: do NOT export TAURI_SIGNING_PRIVATE_KEY in your local shell profile — that
# makes local builds attempt to sign with whatever key is set (which may not match
# the pubkey embedded in tauri.conf.json), failing loudly. Leave it to CI secrets.

set -e

if [ -z "$TAURI_SIGNING_PRIVATE_KEY" ]; then
    echo "  [tauri-build] TAURI_SIGNING_PRIVATE_KEY not set — local build, skipping updater-artifact signing (createUpdaterArtifacts=false overlay)."
    npm run tauri build -- --config '{"bundle":{"createUpdaterArtifacts":false}}' "$@"
else
    npm run tauri build -- "$@"
fi
