#!/bin/bash
# verify_installer.sh — install a SwarmAI release the way a stranger would, then check it runs.
#
# Release gate G1/G2 (docs/design/2026-10-24-swarmai-usable-by-anyone.md §5.1). macOS only in
# S1; S2 adds Windows and Linux. Nothing here touches the real ~/.swarm-ai or the live daemon:
# everything runs under a throwaway HOME, on its own port, under a unique launchd label.
#
# Steps (each prints PASS / FAIL / SKIP; any FAIL -> exit 1):
#   C1  get the installer and mark it quarantined, like a browser download
#   C2  mount it and copy SwarmAI.app out, quarantining the copy like Finder does
#   C3  G2 manifest: Contents/Resources/install-manifest.json lists every required file + sha256
#   C4  backend bundle present in the app (Contents/Resources/python-backend/python-backend)
#   C5  backend binary architecture matches this host
#   C6  backend binary carries at least an ad-hoc code signature
#   C7  deploy the backend to <HOME>/.swarm-ai/runtime/<version>/ (+ resources/, current link)
#   C8  launchd starts the deployed backend; /health answers "healthy"
#   C9  the bundled Claude CLI runs (`--version`), launched by launchd
#   C10 first-run seed: data.db and SwarmWS/.context/SOUL.md exist
#   C11 nothing tried to run a system python / pip (shims on PATH log any call)
#   C12 Gatekeeper assessment of the app (INFO only: releases are unsigned for now)
#   C13 the app's own signature is intact (a broken one makes macOS call the app "damaged")
# Checks still to add: onboarding reachable, MCP tools, `py` subcommand (S3), update + rollback.
#
# Usage:
#   scripts/verify_installer.sh --release v2.3.0        # download from GitHub releases (gh)
#   scripts/verify_installer.sh --dmg path/to.dmg
#   scripts/verify_installer.sh --app path/to/SwarmAI.app
# Options:
#   --backend-dir DIR  use DIR as the backend bundle instead of the app's (harness self-test;
#                      the result is then NOT a release verdict and the exit code is 2)
#   --launcher MODE    launchd (default) or direct. direct starts the backend as a child process,
#                      for hosts where launchd jobs cannot be created (a sandboxed agent shell);
#                      Gatekeeper is then not exercised, so the result is NOT a release verdict (exit 2)
#   --no-quarantine    skip the quarantine marks (self-test only, exit 2). Use it on a managed Mac
#                      whose endpoint security kills and removes quarantined unsigned binaries:
#                      that answers a different question than Gatekeeper on a stranger's Mac
#   --port N           port for the backend under test (default 18399; must be free)
#   --keep             keep the work dir for inspection
# Exit: 0 all PASS, 1 any FAIL, 2 self-test mode (--backend-dir, --launcher direct, --no-quarantine)
#       with no FAIL. Run the release verdict on a clean VM or CI runner, not a managed workstation.

set -uo pipefail

PORT=18399
KEEP=0
LAUNCHER=launchd
NO_QUARANTINE=0
Q=", quarantined"

DIRECT_PIDS=()
SRC_KIND=""
SRC=""
BACKEND_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --release) SRC_KIND=release; SRC="$2"; shift 2 ;;
        --dmg) SRC_KIND=dmg; SRC="$2"; shift 2 ;;
        --app) SRC_KIND=app; SRC="$2"; shift 2 ;;
        --backend-dir) BACKEND_OVERRIDE="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --launcher) LAUNCHER="$2"; shift 2 ;;
        --no-quarantine) NO_QUARANTINE=1; shift ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,42p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
case "$LAUNCHER" in launchd|direct) ;; *) echo "--launcher must be launchd or direct" >&2; exit 64 ;; esac
[ "$NO_QUARANTINE" = 1 ] && Q=" (not quarantined: --no-quarantine)"
[ -n "$SRC_KIND" ] || { echo "need --release, --dmg or --app" >&2; exit 64; }
[ "$(uname -s)" = "Darwin" ] || { echo "S1 supports macOS only" >&2; exit 64; }

# ---------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------
WORK="$(mktemp -d "${KIROCREW_SCRATCH:-${TMPDIR:-/tmp}}/verify-installer.XXXXXX")"
TEST_HOME="$WORK/home"
MOUNT="$WORK/mnt"
SHIMS="$WORK/shims"
SHIM_LOG="$WORK/system-python-calls.log"
LABEL="com.swarmai.verify.$$.$RANDOM"
CLI_LABEL="$LABEL.cli"
UID_GUI="gui/$(id -u)"
mkdir -p "$TEST_HOME" "$MOUNT" "$SHIMS"
: > "$SHIM_LOG"
FAILS=0
RESULTS=()

result() {  # result <PASS|FAIL|SKIP|INFO> <id> <message>
    RESULTS+=("$1 $2 $3")
    printf '%-4s %-4s %s\n' "$1" "$2" "$3"
    [ "$1" = FAIL ] && FAILS=$((FAILS + 1))
    return 0
}

cleanup() {
    local pid
    for pid in "${DIRECT_PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null; done
    launchctl bootout "$UID_GUI/$LABEL" >/dev/null 2>&1 || true
    launchctl bootout "$UID_GUI/$CLI_LABEL" >/dev/null 2>&1 || true
    if mount | grep -q " on $MOUNT "; then hdiutil detach -quiet -force "$MOUNT" >/dev/null 2>&1 || true; fi
    if [ "$KEEP" = 1 ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

quarantine() {  # quarantine <path> [-r]: the attribute a browser / Finder copy leaves behind
    local value
    [ "$NO_QUARANTINE" = 1 ] && return 0
    value="0083;$(printf '%x' "$(date +%s)");Safari;$(uuidgen)"
    if [ "${2:-}" = "-r" ]; then
        find "$1" -print0 | xargs -0 xattr -w com.apple.quarantine "$value" 2>/dev/null
    else
        xattr -w com.apple.quarantine "$value" "$1"
    fi
}

# Shims: a clean user has no python / pip. Any call is logged and fails.
for name in python python3 pip pip3; do
    printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 127\n' "$name" "$SHIM_LOG" > "$SHIMS/$name"
    chmod +x "$SHIMS/$name"
done
CLEAN_PATH="$SHIMS:/usr/bin:/bin:/usr/sbin:/sbin"

if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
    echo "port $PORT is already in use; pick another with --port" >&2
    exit 64
fi

xml_escape() { local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

# launchd_start <label> <stdout-file> <program> [args...]: run under launchd, like the real daemon,
# with the test HOME and a PATH that has no developer tools. Gatekeeper treats a launchd job like
# any other launch, so a quarantined unsigned binary is judged here as it would be for a stranger.
launchd_start() {
    local label="$1" out="$2"; shift 2
    if [ "$LAUNCHER" = direct ]; then
        env -i HOME="$TEST_HOME" PATH="$CLEAN_PATH" SWARMAI_MODE=daemon SWARMAI_PORT="$PORT" \
            SWARM_DESKTOP_PREWARM=0 /bin/sh -c 'rc="$0"; "$@" & c=$!; trap "kill \$c 2>/dev/null; wait \$c; exit 0" TERM; wait $c; echo $? > "$rc"' "$WORK/$label.rc" "$@" > "$out" 2>&1 &
        DIRECT_PIDS+=("$!")
        return 0
    fi
    local plist="$WORK/$label.plist" arg
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0"><dict>'
        echo "<key>Label</key><string>$(xml_escape "$label")</string>"
        echo '<key>ProgramArguments</key><array>'
        for arg in "$@"; do echo "<string>$(xml_escape "$arg")</string>"; done
        echo '</array>'
        echo '<key>EnvironmentVariables</key><dict>'
        echo "<key>HOME</key><string>$(xml_escape "$TEST_HOME")</string>"
        echo "<key>PATH</key><string>$(xml_escape "$CLEAN_PATH")</string>"
        echo '<key>SWARMAI_MODE</key><string>daemon</string>'
        echo "<key>SWARMAI_PORT</key><string>$PORT</string>"
        echo '<key>SWARM_DESKTOP_PREWARM</key><string>0</string>'
        echo '</dict>'
        echo "<key>WorkingDirectory</key><string>$(xml_escape "$TEST_HOME")</string>"
        echo "<key>StandardOutPath</key><string>$(xml_escape "$out")</string>"
        echo "<key>StandardErrorPath</key><string>$(xml_escape "$out")</string>"
        echo '<key>RunAtLoad</key><true/><key>KeepAlive</key><false/>'
        echo '</dict></plist>'
    } > "$plist"
    launchctl bootstrap "$UID_GUI" "$plist"
}

# launchd_exit_code <label>: last exit code once the job is no longer running, else empty
launchd_exit_code() {
    local info
    if [ "$LAUNCHER" = direct ]; then
        cat "$WORK/$1.rc" 2>/dev/null
        return 0
    fi
    info="$(launchctl print "$UID_GUI/$1" 2>/dev/null)" || return 0
    if printf '%s' "$info" | grep -q 'state = running'; then return 0; fi
    printf '%s' "$info" | sed -n 's/.*last exit code = \(.*\)$/\1/p' | head -1
}

# ---------------------------------------------------------------------------
# C1 / C2: get the installer, quarantine it, copy the app out
# ---------------------------------------------------------------------------
APP=""
case "$SRC_KIND" in
    release)
        if gh release download "$SRC" --pattern '*aarch64.dmg' --dir "$WORK" >/dev/null 2>&1; then
            DMG="$(ls "$WORK"/*.dmg | head -1)"
            quarantine "$DMG"
            result PASS C1 "downloaded $(basename "$DMG") from release $SRC$Q"
        else
            result FAIL C1 "could not download an aarch64 .dmg from release $SRC"
        fi ;;
    dmg)
        DMG="$WORK/$(basename "$SRC")"
        cp "$SRC" "$DMG" && quarantine "$DMG"
        result PASS C1 "copied $(basename "$SRC")$Q" ;;
    app)
        result SKIP C1 "--app given: no installer to download" ;;
esac

if [ "$SRC_KIND" = app ]; then
    APP="$WORK/Applications/$(basename "$SRC")"
    mkdir -p "$WORK/Applications" && ditto "$SRC" "$APP" && quarantine "$APP" -r
    result PASS C2 "copied $(basename "$SRC") into a test Applications dir$Q"
elif [ -n "${DMG:-}" ] && hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MOUNT" "$DMG"; then
    src_app="$(find "$MOUNT" -maxdepth 1 -name '*.app' | head -1)"
    if [ -n "$src_app" ]; then
        APP="$WORK/Applications/$(basename "$src_app")"
        mkdir -p "$WORK/Applications" && ditto "$src_app" "$APP" && quarantine "$APP" -r
        result PASS C2 "mounted the dmg, copied $(basename "$src_app") out$Q"
    else
        result FAIL C2 "no .app at the top of the dmg"
    fi
    hdiutil detach -quiet "$MOUNT" >/dev/null 2>&1 || true
else
    result FAIL C2 "could not mount the dmg"
fi
RES="${APP:+$APP/Contents/Resources}"
VERSION="$( [ -n "$APP" ] && /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null )"
VERSION="${VERSION:-unknown}"

# ---------------------------------------------------------------------------
# C3: G2 manifest. Format (written by the build, S2):
#   {"version": "...", "files": [{"path": "<relative to Resources>", "sha256": "<hex>"}, ...]}
# ---------------------------------------------------------------------------
MANIFEST="${RES:+$RES/install-manifest.json}"
if [ -z "$RES" ]; then
    result SKIP C3 "no app to inspect"
elif [ ! -f "$MANIFEST" ]; then
    result FAIL C3 "Contents/Resources/install-manifest.json is missing (the build must write it)"
else
    # osascript JavaScript is on every Mac; it turns the manifest into "sha256  path" lines.
    lines="$(osascript -l JavaScript -e 'function run(a){var o=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(a[0],4,null).js);return o.files.map(function(f){return f.sha256+"  "+f.path}).join("\n")}' "$MANIFEST" 2>/dev/null)"
    if [ -z "$lines" ]; then
        result FAIL C3 "install-manifest.json lists no files or cannot be parsed"
    else
        bad="$(cd "$RES" && printf '%s\n' "$lines" | shasum -a 256 -c --quiet 2>&1 | head -5)"
        n="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
        if [ -z "$bad" ]; then result PASS C3 "all $n manifest files present with matching sha256"
        else result FAIL C3 "manifest mismatch: $(printf '%s' "$bad" | tr '\n' ';')"; fi
    fi
fi

# ---------------------------------------------------------------------------
# C4-C6: the backend bundle inside the app
# ---------------------------------------------------------------------------
BACKEND_SRC=""
if [ -n "$BACKEND_OVERRIDE" ]; then
    BACKEND_SRC="$WORK/backend-override"
    ditto "$BACKEND_OVERRIDE" "$BACKEND_SRC" && quarantine "$BACKEND_SRC" -r
    result SKIP C4 "self-test: backend taken from --backend-dir, not from the app"
elif [ -n "$RES" ] && [ -x "$RES/python-backend/python-backend" ]; then
    BACKEND_SRC="$RES/python-backend"
    result PASS C4 "backend bundle present ($(du -sh "$BACKEND_SRC" | cut -f1))"
elif [ -n "$RES" ]; then
    result FAIL C4 "Contents/Resources/python-backend/python-backend is missing: the installer has no backend"
else
    result SKIP C4 "no app to inspect"
fi

BIN="${BACKEND_SRC:+$BACKEND_SRC/python-backend}"
if [ -n "$BIN" ]; then
    host_arch="$(uname -m)"
    if lipo -archs "$BIN" 2>/dev/null | tr ' ' '\n' | grep -qx "$host_arch"; then
        result PASS C5 "backend binary contains $host_arch"
    else
        result FAIL C5 "backend binary archs '$(lipo -archs "$BIN" 2>/dev/null)' do not include $host_arch"
    fi
    if codesign -v "$BIN" >/dev/null 2>&1; then
        result PASS C6 "backend binary has a valid (at least ad-hoc) signature"
    else
        result FAIL C6 "backend binary has no valid code signature (arm64 needs at least ad-hoc)"
    fi
else
    result SKIP C5 "no backend binary"
    result SKIP C6 "no backend binary"
fi

# ---------------------------------------------------------------------------
# C7: deploy to the versioned runtime (spec §3.1 layout), keeping the quarantine
# attributes a real copy keeps (ditto, like Rust's fs::copy, copies extended attributes)
# ---------------------------------------------------------------------------
RT="$TEST_HOME/.swarm-ai/runtime/$VERSION"
DEPLOYED=0
if [ -n "$BIN" ]; then
    mkdir -p "$RT"
    if ditto "$BACKEND_SRC" "$RT"; then
        mkdir -p "$RT/resources"
        if [ -n "$RES" ]; then
            for f in "$RES"/*; do
                case "$(basename "$f")" in python-backend|daemon|*.icns) continue ;; esac
                ditto "$f" "$RT/resources/$(basename "$f")"
            done
        fi
        ln -sfn "$RT" "$TEST_HOME/.swarm-ai/runtime/current"
        DEPLOYED=1
        result PASS C7 "deployed to runtime/$VERSION (+ resources/, current -> $VERSION)"
    else
        result FAIL C7 "copying the backend into runtime/$VERSION failed"
    fi
else
    result SKIP C7 "no backend to deploy"
fi

# ---------------------------------------------------------------------------
# C8: launchd starts it; /health answers
# ---------------------------------------------------------------------------
BACKEND_LOG="$WORK/backend.log"
HEALTHY=0
if [ "$DEPLOYED" = 1 ]; then
    if launchd_start "$LABEL" "$BACKEND_LOG" "$RT/python-backend" --port "$PORT"; then
        health=""
        for _ in $(seq 1 75); do
            health="$(curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)"
            printf '%s' "$health" | grep -q '"status": *"healthy"' && { HEALTHY=1; break; }
            [ -n "$(launchd_exit_code "$LABEL")" ] && break
            sleep 2
        done
        if [ "$HEALTHY" = 1 ]; then
            result PASS C8 "$LAUNCHER started the backend; /health is healthy"
        else
            code="$(launchd_exit_code "$LABEL")"
            result FAIL C8 "backend not healthy (exit code: ${code:-still running}); last log: $(tail -3 "$BACKEND_LOG" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
        fi
    else
        result FAIL C8 "launchctl bootstrap refused the job"
    fi
else
    result SKIP C8 "nothing deployed"
fi

# ---------------------------------------------------------------------------
# C9: the bundled Claude CLI runs, also launched by launchd
# ---------------------------------------------------------------------------
CLI="$RT/_internal/claude_agent_sdk/_bundled/claude"
if [ "$DEPLOYED" = 1 ] && [ -f "$CLI" ]; then
    CLI_OUT="$WORK/cli.out"
    launchd_start "$CLI_LABEL" "$CLI_OUT" "$CLI" --version >/dev/null 2>&1
    code=""
    for _ in $(seq 1 30); do code="$(launchd_exit_code "$CLI_LABEL")"; [ -n "$code" ] && break; sleep 1; done
    if [ "$code" = 0 ] && [ -s "$CLI_OUT" ]; then
        result PASS C9 "bundled Claude CLI runs: $(head -1 "$CLI_OUT" | cut -c1-80)"
    else
        result FAIL C9 "bundled Claude CLI did not run (exit ${code:-timeout}): $(head -2 "$CLI_OUT" 2>/dev/null | tr '\n' ' ' | cut -c1-200)"
    fi
elif [ "$DEPLOYED" = 1 ]; then
    result FAIL C9 "no bundled Claude CLI at _internal/claude_agent_sdk/_bundled/claude"
else
    result SKIP C9 "nothing deployed"
fi

# ---------------------------------------------------------------------------
# C10: first-run seed; C11: no system python; C12: Gatekeeper (INFO)
# ---------------------------------------------------------------------------
if [ "$HEALTHY" = 1 ]; then
    missing=""
    [ -f "$TEST_HOME/.swarm-ai/data.db" ] || missing="$missing data.db"
    [ -f "$TEST_HOME/.swarm-ai/SwarmWS/.context/SOUL.md" ] || missing="$missing SwarmWS/.context/SOUL.md"
    if [ -z "$missing" ]; then result PASS C10 "first-run seed created data.db and SwarmWS/.context"
    else result FAIL C10 "first-run seed missing:$missing"; fi
else
    result SKIP C10 "backend never became healthy"
fi

if [ "$DEPLOYED" = 1 ]; then
    if [ -s "$SHIM_LOG" ]; then
        result FAIL C11 "the product called a system python/pip: $(head -3 "$SHIM_LOG" | tr '\n' ';' | cut -c1-200)"
    else
        result PASS C11 "no call to a system python or pip"
    fi
else
    result SKIP C11 "nothing ran"
fi

if [ -n "$APP" ]; then
    verdict="$(spctl -a -t exec -vv "$APP" 2>&1 | head -2 | tr '\n' ' ')"
    result INFO C12 "Gatekeeper on the app: $verdict"
    # An unsigned app is fine for now (the user clicks "Open Anyway"); a BROKEN signature is not:
    # with quarantine macOS then reports the app as damaged and offers no way to open it.
    if codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
        result PASS C13 "app signature is intact (ad-hoc or real)"
    else
        result FAIL C13 "app signature is broken: $(codesign --verify --deep --strict "$APP" 2>&1 | head -1 | cut -c1-160)"
    fi
fi

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
echo
echo "installer: ${SRC}   version: $VERSION   work: $WORK"
if [ "$FAILS" -gt 0 ]; then
    echo "RESULT: FAIL ($FAILS check(s) failed)"
    exit 1
fi
if [ -n "$BACKEND_OVERRIDE" ] || [ "$LAUNCHER" = direct ] || [ "$NO_QUARANTINE" = 1 ]; then
    echo "RESULT: self-test only (--backend-dir, --launcher direct or --no-quarantine) — not a release verdict"
    exit 2
fi
echo "RESULT: PASS"
exit 0
