#!/bin/bash
# verify_installer.sh — install a SwarmAI release the way a stranger would, then check it runs.
#
# Release gate G1/G2 (docs/design/2026-10-24-swarmai-usable-by-anyone.md §5.1) for every
# release target: macOS (.dmg), Linux (.AppImage), Windows (NSIS -setup.exe, run from Git Bash).
# Nothing here touches the real ~/.swarm-ai or a live backend: everything runs under a throwaway
# HOME (Windows: USERPROFILE / LOCALAPPDATA too), on its own port, under a unique launchd label.
#
# Steps (each prints PASS / FAIL / SKIP / INFO; any FAIL -> exit 1):
#   C1  get the installer and mark it as downloaded (macOS quarantine, Windows Mark-of-the-Web;
#       Linux has no download mark)
#   C2  install it like a user: macOS mount the dmg and copy the app out (quarantining the copy
#       like Finder), Linux extract the AppImage, Windows run the installer silently
#   C3  G2 manifest: install-manifest.json lists every required file + sha256, all match
#   C4  backend bundle present (python-backend/python-backend[.exe])
#   C5  backend binary architecture matches this host
#   C6  macOS: backend binary carries at least an ad-hoc code signature
#   C7  deploy the backend the way the app does (src-tauri/src/runtime_deploy.rs): runtime/<version>/
#       + resources/, pointer file `current`, macOS quarantine cleared on the runtime
#   C8  the backend starts the way the app starts it (macOS launchd, Linux/Windows a subprocess);
#       /health answers "healthy"
#   C9  the bundled Claude CLI runs (`--version`), started the same way
#   C10 first-run seed: data.db and SwarmWS/.context/SOUL.md exist
#   C11 nothing tried to run a system python / pip (shims on PATH log any call)
#   C12 macOS: Gatekeeper assessment of the app (INFO only: releases are unsigned for now)
#   C13 macOS: the app's own signature is intact (a broken one makes macOS call the app "damaged")
#   C14 the bundled Python runs skills: `py -c` imports the standard library and every skill's
#       python-deps, and `py <skill script>` runs a real skill script (S3c)
# Checks still to add: onboarding reachable, MCP tools, update + rollback;
# C7 should call the app's own deploy code instead of mirroring it.
#
# Usage:
#   scripts/verify_installer.sh --release v2.3.0        # this platform's installer, via gh
#   scripts/verify_installer.sh --dmg path/to.dmg        # macOS
#   scripts/verify_installer.sh --app path/to/SwarmAI.app
#   scripts/verify_installer.sh --appimage path/to.AppImage   # Linux
#   scripts/verify_installer.sh --setup path/to-setup.exe     # Windows (Git Bash)
# Options:
#   --backend-dir DIR  use DIR as the backend bundle instead of the installer's (harness self-test;
#                      the result is then NOT a release verdict and the exit code is 2)
#   --launcher MODE    macOS only: launchd (default) or direct. direct starts the backend as a child
#                      process, for hosts where launchd jobs cannot be created (a sandboxed agent
#                      shell); Gatekeeper is then not exercised, so it is NOT a release verdict (exit 2).
#                      Linux and Windows always start it directly: that is how the app starts it there.
#   --no-quarantine    skip the download marks (self-test only, exit 2). Use it on a managed Mac
#                      whose endpoint security kills and removes quarantined unsigned binaries:
#                      that answers a different question than Gatekeeper on a stranger's Mac
#   --port N           port for the backend under test (default 18399; must be free)
#   --keep             keep the work dir for inspection
# Exit: 0 all PASS, 1 any FAIL, 2 self-test mode (--backend-dir, --launcher direct, --no-quarantine)
#       with no FAIL, 64 usage. Run the release verdict on a clean VM or CI runner, not a managed
#       workstation.

PORT=18399
KEEP=0
LAUNCHER=""
NO_QUARANTINE=0
Q=", marked as downloaded"

case "$(uname -s)" in
    Darwin) OS=macos ;;
    Linux) OS=linux ;;
    MINGW*|MSYS*|CYGWIN*) OS=windows ;;
    *) echo "unsupported platform: $(uname -s)" >&2; exit 64 ;;
esac

DIRECT_PIDS=()
SRC_KIND=""
SRC=""
BACKEND_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --release) SRC_KIND=release; SRC="$2"; shift 2 ;;
        --dmg) SRC_KIND=dmg; SRC="$2"; shift 2 ;;
        --app) SRC_KIND=app; SRC="$2"; shift 2 ;;
        --appimage) SRC_KIND=appimage; SRC="$2"; shift 2 ;;
        --setup) SRC_KIND=setup; SRC="$2"; shift 2 ;;
        --backend-dir) BACKEND_OVERRIDE="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --launcher) LAUNCHER="$2"; shift 2 ;;
        --no-quarantine) NO_QUARANTINE=1; shift ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,52p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[ -n "$SRC_KIND" ] || { echo "need --release, --dmg, --app, --appimage or --setup" >&2; exit 64; }
case "$OS:$SRC_KIND" in
    *:release|macos:dmg|macos:app|linux:appimage|windows:setup) ;;
    *) echo "--$SRC_KIND is not an installer for $OS" >&2; exit 64 ;;
esac
if [ "$OS" = macos ]; then
    LAUNCHER="${LAUNCHER:-launchd}"
    case "$LAUNCHER" in launchd|direct) ;; *) echo "--launcher must be launchd or direct" >&2; exit 64 ;; esac
else
    case "${LAUNCHER:-direct}" in direct) LAUNCHER=direct ;; *) echo "--launcher is macOS only" >&2; exit 64 ;; esac
fi
[ "$NO_QUARANTINE" = 1 ] && Q=" (no download mark: --no-quarantine)"
[ "$OS" = linux ] && Q=""
SELF_TEST=0
{ [ -n "$BACKEND_OVERRIDE" ] || [ "$NO_QUARANTINE" = 1 ] || { [ "$OS" = macos ] && [ "$LAUNCHER" = direct ]; }; } && SELF_TEST=1
EXE_SUFFIX=""; [ "$OS" = windows ] && EXE_SUFFIX=".exe"

# ---------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------
WORK="$(mktemp -d "${KIROCREW_SCRATCH:-${TMPDIR:-/tmp}}/verify-installer.XXXXXX")"
TEST_HOME="$WORK/home"
TEST_LOCALAPPDATA="$TEST_HOME/AppData/Local"
MOUNT="$WORK/mnt"
SHIMS="$WORK/shims"
SHIM_LOG="$WORK/system-python-calls.log"
LABEL="com.swarmai.verify.$$.$RANDOM"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLI_LABEL="$LABEL.cli"
UID_GUI="gui/$(id -u)"
INSTALL_DIR=""
mkdir -p "$TEST_HOME" "$TEST_LOCALAPPDATA" "$MOUNT" "$SHIMS"
: > "$SHIM_LOG"
FAILS=0

result() {  # result <PASS|FAIL|SKIP|INFO> <id> <message>
    printf '%-4s %-4s %s\n' "$1" "$2" "$3"
    [ "$1" = FAIL ] && FAILS=$((FAILS + 1))
    return 0
}

cleanup() {
    local pid
    for pid in "${DIRECT_PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null; done
    if [ "$OS" = macos ]; then
        launchctl bootout "$UID_GUI/$LABEL" >/dev/null 2>&1 || true
        launchctl bootout "$UID_GUI/$CLI_LABEL" >/dev/null 2>&1 || true
        if mount | grep -q " on $MOUNT "; then hdiutil detach -quiet -force "$MOUNT" >/dev/null 2>&1 || true; fi
    fi
    if [ "$OS" = windows ] && [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/uninstall.exe" ]; then
        "$INSTALL_DIR/uninstall.exe" /S >/dev/null 2>&1 || true
    fi
    # the backend may still be writing into the test HOME for a moment after its stop
    for pid in "${DIRECT_PIDS[@]:-}"; do [ -n "$pid" ] && wait "$pid" 2>/dev/null; done
    if [ "$KEEP" = 1 ]; then echo "work dir kept: $WORK"
    else rm -rf "$WORK" 2>/dev/null || { sleep 2; rm -rf "$WORK"; }; fi
}
trap cleanup EXIT

mark_downloaded() {  # mark_downloaded <path> [-r]: what a browser / Finder copy leaves behind
    local value
    [ "$NO_QUARANTINE" = 1 ] && return 0
    case "$OS" in
        macos)
            value="0083;$(printf '%x' "$(date +%s)");Safari;$(uuidgen)"
            if [ "${2:-}" = "-r" ]; then
                find "$1" -print0 | xargs -0 xattr -w com.apple.quarantine "$value" 2>/dev/null
            else
                xattr -w com.apple.quarantine "$value" "$1"
            fi ;;
        windows)  # Mark-of-the-Web: ZoneId 3 = Internet
            powershell.exe -NoProfile -Command "Set-Content -LiteralPath '$(cygpath -w "$1")' -Stream Zone.Identifier -Value \"[ZoneTransfer]\`r\`nZoneId=3\"" ;;
        linux) return 0 ;;
    esac
}

sha256_check() {  # stdin "sha256  path" lines, run in the resources dir; prints mismatches
    if command -v sha256sum >/dev/null 2>&1; then sha256sum -c --quiet 2>&1
    else shasum -a 256 -c --quiet 2>&1; fi
}

# Shims: a clean user has no python / pip. Any call is logged and fails. Windows looks up
# python.exe / python.cmd, never a bare script, so it gets .cmd shims too.
for name in python python3 pip pip3; do
    printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 127\n' "$name" "$SHIM_LOG" > "$SHIMS/$name"
    chmod +x "$SHIMS/$name"
    if [ "$OS" = windows ]; then
        printf '@echo %s %%* >> "%s"\r\n@exit /b 127\r\n' "$name" "$(cygpath -w "$SHIM_LOG")" > "$SHIMS/$name.cmd"
    fi
done
case "$OS" in
    windows)
        CLEAN_PATH="$SHIMS:/c/Windows/System32:/c/Windows:/c/Windows/System32/WindowsPowerShell/v1.0"
        [ -d "/c/Program Files/Git/cmd" ] && CLEAN_PATH="$CLEAN_PATH:/c/Program Files/Git/cmd" ;;
    *) CLEAN_PATH="$SHIMS:/usr/bin:/bin:/usr/sbin:/sbin" ;;
esac

# a free port refuses the connection (curl exit 7) on every platform; nc is not on Windows
curl -s -m 2 "http://127.0.0.1:$PORT/" >/dev/null 2>&1
if [ $? -ne 7 ]; then
    echo "port $PORT is already in use; pick another with --port" >&2
    exit 64
fi

xml_escape() { local s="$1"; s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"; printf '%s' "$s"; }

# start_job <label> <stdout-file> <program> [args...]: run it the way the app runs the backend,
# with the test HOME and a PATH that has no developer tools. macOS: a launchd job, like the real
# daemon (Gatekeeper treats it like any other launch). Linux / Windows: a child process, like the
# app's subprocess mode.
start_job() {
    local label="$1" out="$2"; shift 2
    if [ "$LAUNCHER" = direct ]; then
        local mode=daemon; [ "$OS" = macos ] || mode=subprocess
        local -a envs=(HOME="$TEST_HOME" PATH="$CLEAN_PATH" SWARMAI_MODE="$mode" SWARMAI_PORT="$PORT"
                       SWARM_DESKTOP_PREWARM=0)
        if [ "$OS" = windows ]; then
            envs+=(USERPROFILE="$(cygpath -w "$TEST_HOME")" LOCALAPPDATA="$(cygpath -w "$TEST_LOCALAPPDATA")"
                   APPDATA="$(cygpath -w "$TEST_HOME/AppData/Roaming")" SYSTEMROOT="${SYSTEMROOT:-C:\\Windows}"
                   WINDIR="${WINDIR:-C:\\Windows}" COMSPEC="${COMSPEC:-C:\\Windows\\System32\\cmd.exe}"
                   PATHEXT="${PATHEXT:-.COM;.EXE;.BAT;.CMD}" TEMP="$(cygpath -w "$WORK")" TMP="$(cygpath -w "$WORK")")
        fi
        env -i "${envs[@]}" /bin/sh -c 'rc="$0"; "$@" & c=$!; trap "kill \$c 2>/dev/null; wait \$c; exit 0" TERM; wait $c; echo $? > "$rc"' "$WORK/$label.rc" "$@" > "$out" 2>&1 &
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

# job_exit_code <label>: last exit code once the job is no longer running, else empty
job_exit_code() {
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
# C1: get the installer and mark it as downloaded
# ---------------------------------------------------------------------------
case "$OS:$(uname -m)" in
    macos:arm64) REL_PATTERN='*_aarch64.dmg'; INSTALLER_KIND=dmg ;;
    macos:*) REL_PATTERN='*_x64.dmg'; INSTALLER_KIND=dmg ;;
    linux:*) REL_PATTERN='*_amd64.AppImage'; INSTALLER_KIND=appimage ;;
    windows:*) REL_PATTERN='*_x64-setup.exe'; INSTALLER_KIND=setup ;;
esac
INSTALLER=""
case "$SRC_KIND" in
    release)
        if gh release download "$SRC" --pattern "$REL_PATTERN" --dir "$WORK" >/dev/null 2>&1; then
            INSTALLER="$(find "$WORK" -maxdepth 1 -type f -name "$REL_PATTERN" | head -1)"
            mark_downloaded "$INSTALLER"
            result PASS C1 "downloaded $(basename "$INSTALLER") from release $SRC$Q"
        else
            result FAIL C1 "could not download $REL_PATTERN from release $SRC"
        fi ;;
    app)
        result SKIP C1 "--app given: no installer to download" ;;
    *)
        INSTALLER_KIND="$SRC_KIND"
        INSTALLER="$WORK/$(basename "$SRC")"
        if cp "$SRC" "$INSTALLER" && mark_downloaded "$INSTALLER"; then
            result PASS C1 "copied $(basename "$SRC")$Q"
        else
            result FAIL C1 "could not copy $SRC"; INSTALLER=""
        fi ;;
esac

# ---------------------------------------------------------------------------
# C2: install it the way a user does; RES = the directory holding the installed resources
# ---------------------------------------------------------------------------
APP=""
RES=""
if [ "$SRC_KIND" = app ]; then
    APP="$WORK/Applications/$(basename "$SRC")"
    if [ -d "$SRC" ] && mkdir -p "$WORK/Applications" && ditto "$SRC" "$APP" && mark_downloaded "$APP" -r; then
        result PASS C2 "copied $(basename "$SRC") into a test Applications dir$Q"
    else
        result FAIL C2 "could not copy the app $SRC"; APP=""
    fi
elif [ -z "$INSTALLER" ]; then
    result SKIP C2 "no installer"
elif [ "$INSTALLER_KIND" = dmg ]; then
    if hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MOUNT" "$INSTALLER"; then
        src_app="$(find "$MOUNT" -maxdepth 1 -name '*.app' | head -1)"
        if [ -n "$src_app" ]; then
            APP="$WORK/Applications/$(basename "$src_app")"
            mkdir -p "$WORK/Applications" && ditto "$src_app" "$APP" && mark_downloaded "$APP" -r
            result PASS C2 "mounted the dmg, copied $(basename "$src_app") out$Q"
        else
            result FAIL C2 "no .app at the top of the dmg"
        fi
        hdiutil detach -quiet "$MOUNT" >/dev/null 2>&1 || true
    else
        result FAIL C2 "could not mount the dmg"
    fi
elif [ "$INSTALLER_KIND" = appimage ]; then
    # --appimage-extract needs no FUSE (a stranger's distro may lack libfuse2); the files it
    # yields are the ones the mounted AppImage runs
    chmod +x "$INSTALLER"
    if (cd "$WORK" && "$INSTALLER" --appimage-extract >/dev/null 2>&1) && [ -d "$WORK/squashfs-root" ]; then
        m="$(find "$WORK/squashfs-root" -maxdepth 5 -name install-manifest.json -type f | head -1)"
        RES="${m:+$(dirname "$m")}"
        RES="${RES:-$WORK/squashfs-root}"
        result PASS C2 "extracted the AppImage (resources at ${RES#"$WORK"/})"
    else
        result FAIL C2 "the AppImage would not extract (--appimage-extract)"
    fi
elif [ "$INSTALLER_KIND" = setup ]; then
    # NSIS silent install into a test dir. SmartScreen only judges launches from Explorer, so
    # the Mark-of-the-Web here is carried for what the installed files inherit, not for a prompt.
    INSTALL_DIR="$WORK/install"
    if "$INSTALLER" /S "/D=$(cygpath -w "$INSTALL_DIR")" && [ -d "$INSTALL_DIR" ]; then
        RES="$INSTALL_DIR"
        result PASS C2 "installer ran silently into a test dir$Q"
    else
        result FAIL C2 "the installer failed or installed nothing"
    fi
fi
[ -n "$APP" ] && RES="$APP/Contents/Resources"

MANIFEST="${RES:+$RES/install-manifest.json}"
VERSION=""
if [ -n "$APP" ]; then
    VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null)"
elif [ -n "$MANIFEST" ] && [ -f "$MANIFEST" ]; then
    VERSION="$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$MANIFEST" | head -1)"
fi
# only a plain version string names a runtime dir (PlistBuddy prints its errors on stdout)
printf '%s' "$VERSION" | grep -Eq '^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$' || VERSION=unknown

# ---------------------------------------------------------------------------
# C3: G2 manifest, written by desktop/scripts/write_install_manifest.py:
#   {"version": "...", "files": [{"path": "<relative to the resources dir>", "sha256": "<hex>"}, ...]}
# Read with awk (no python, jq or osascript needed): one object's path + sha256 -> one line.
# ---------------------------------------------------------------------------
manifest_lines() {
    awk '
        /"path":/   { p = $0; sub(/^[^:]*: *"/, "", p); sub(/".*$/, "", p) }
        /"sha256":/ { h = $0; sub(/^[^:]*: *"/, "", h); sub(/".*$/, "", h) }
        /}/         { if (p != "" && h != "") print h "  " p; p = ""; h = "" }
    ' "$1"
}
if [ -z "$RES" ]; then
    result SKIP C3 "nothing installed to inspect"
elif [ ! -f "$MANIFEST" ]; then
    result FAIL C3 "install-manifest.json is missing next to the installed resources (the build must write it)"
else
    lines="$(manifest_lines "$MANIFEST")"
    if [ -z "$lines" ]; then
        result FAIL C3 "install-manifest.json lists no files or cannot be parsed"
    else
        bad="$(cd "$RES" && printf '%s\n' "$lines" | sha256_check | head -5)"
        n="$(printf '%s\n' "$lines" | wc -l | tr -d ' ')"
        if [ -z "$bad" ]; then result PASS C3 "all $n manifest files present with matching sha256"
        else result FAIL C3 "manifest mismatch: $(printf '%s' "$bad" | tr '\n' ';')"; fi
    fi
fi

# ---------------------------------------------------------------------------
# C4-C6: the backend bundle inside the installation
# ---------------------------------------------------------------------------
BACKEND_SRC=""
if [ -n "$BACKEND_OVERRIDE" ]; then
    BACKEND_SRC="$WORK/backend-override"
    cp -R "$BACKEND_OVERRIDE" "$BACKEND_SRC" && mark_downloaded "$BACKEND_SRC" -r
    result SKIP C4 "self-test: backend taken from --backend-dir, not from the installer"
elif [ -n "$RES" ] && [ -f "$RES/python-backend/python-backend$EXE_SUFFIX" ]; then
    BACKEND_SRC="$RES/python-backend"
    result PASS C4 "backend bundle present ($(du -sh "$BACKEND_SRC" | cut -f1))"
elif [ -n "$RES" ]; then
    result FAIL C4 "python-backend/python-backend$EXE_SUFFIX is missing: the installer has no backend"
else
    result SKIP C4 "nothing installed to inspect"
fi

BIN="${BACKEND_SRC:+$BACKEND_SRC/python-backend$EXE_SUFFIX}"
host_arch="$(uname -m)"
if [ -z "$BIN" ]; then
    result SKIP C5 "no backend binary"
elif [ "$OS" = macos ]; then
    if lipo -archs "$BIN" 2>/dev/null | tr ' ' '\n' | grep -qx "$host_arch"; then
        result PASS C5 "backend binary contains $host_arch"
    else
        result FAIL C5 "backend binary archs '$(lipo -archs "$BIN" 2>/dev/null)' do not include $host_arch"
    fi
elif command -v file >/dev/null 2>&1; then
    kind="$(file -b "$BIN")"
    case "$host_arch" in x86_64) want='x86-64' ;; aarch64|arm64) want='aarch64|ARM64|Aarch64' ;; *) want="$host_arch" ;; esac
    if printf '%s' "$kind" | grep -Eq "$want"; then result PASS C5 "backend binary is $want: ${kind%%,*}"
    else result FAIL C5 "backend binary is not $want: $(printf '%s' "$kind" | cut -c1-120)"; fi
else
    result SKIP C5 "no 'file' tool to read the binary's architecture"
fi
if [ -z "$BIN" ]; then
    result SKIP C6 "no backend binary"
elif [ "$OS" = macos ]; then
    if codesign -v "$BIN" >/dev/null 2>&1; then
        result PASS C6 "backend binary has a valid (at least ad-hoc) signature"
    else
        result FAIL C6 "backend binary has no valid code signature (arm64 needs at least ad-hoc)"
    fi
else
    result SKIP C6 "no code signature requirement on $OS (first release is unsigned)"
fi

# ---------------------------------------------------------------------------
# C7: deploy the way the app does (src-tauri/src/runtime_deploy.rs): runtime/<version>/ with the
# backend and resources/, the pointer file `current` (a file, not a link), and on macOS the
# quarantine attribute cleared on the runtime (the app runs `xattr -dr com.apple.quarantine`)
# ---------------------------------------------------------------------------
if [ "$OS" = windows ]; then RT_ROOT="$TEST_LOCALAPPDATA/SwarmAI/runtime"
else RT_ROOT="$TEST_HOME/.swarm-ai/runtime"; fi
RT="$RT_ROOT/$VERSION"
DEPLOYED=0
copy_tree() { if [ "$OS" = macos ]; then ditto "$1" "$2"; else mkdir -p "$2" && cp -R "$1/." "$2/"; fi; }
if [ -n "$BIN" ]; then
    mkdir -p "$RT"
    if copy_tree "$BACKEND_SRC" "$RT"; then
        mkdir -p "$RT/resources"
        if [ -n "$RES" ]; then
            for f in "$RES"/*; do  # the same files runtime_deploy.rs copies
                [ -f "$f" ] || continue
                case "$(basename "$f" | tr 'A-Z' 'a-z')" in
                    .*|*.icns|*.exe|*.dll|install-manifest.json) continue ;;
                esac
                cp "$f" "$RT/resources/"
            done
        fi
        printf '%s' "$VERSION" > "$RT/.version"
        printf '%s' "$VERSION" > "$RT_ROOT/current"
        [ "$OS" = macos ] && xattr -dr com.apple.quarantine "$RT" 2>/dev/null
        DEPLOYED=1
        result PASS C7 "deployed to runtime/$VERSION (+ resources/, current = $VERSION)"
    else
        result FAIL C7 "copying the backend into runtime/$VERSION failed"
    fi
else
    result SKIP C7 "no backend to deploy"
fi

# ---------------------------------------------------------------------------
# C8: start it the way the app does; /health answers
# ---------------------------------------------------------------------------
BACKEND_LOG="$WORK/backend.log"
HEALTHY=0
how="launchd"; [ "$LAUNCHER" = direct ] && how="a child process"
if [ "$DEPLOYED" = 1 ]; then
    if start_job "$LABEL" "$BACKEND_LOG" "$RT/python-backend$EXE_SUFFIX" --host 127.0.0.1 --port "$PORT"; then
        health=""
        for _ in $(seq 1 90); do
            health="$(curl -s -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null)"
            printf '%s' "$health" | grep -q '"status": *"healthy"' && { HEALTHY=1; break; }
            [ -n "$(job_exit_code "$LABEL")" ] && break
            sleep 2
        done
        if [ "$HEALTHY" = 1 ]; then
            result PASS C8 "$how started the backend; /health is healthy"
        else
            code="$(job_exit_code "$LABEL")"
            result FAIL C8 "backend not healthy (exit code: ${code:-still running}); last log: $(tail -3 "$BACKEND_LOG" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
        fi
    else
        result FAIL C8 "could not start the backend ($how)"
    fi
else
    result SKIP C8 "nothing deployed"
fi

# ---------------------------------------------------------------------------
# C9: the bundled Claude CLI runs, started the same way
# ---------------------------------------------------------------------------
CLI="$RT/_internal/claude_agent_sdk/_bundled/claude$EXE_SUFFIX"
if [ "$DEPLOYED" = 1 ] && [ -f "$CLI" ]; then
    CLI_OUT="$WORK/cli.out"
    start_job "$CLI_LABEL" "$CLI_OUT" "$CLI" --version >/dev/null 2>&1
    code=""
    for _ in $(seq 1 30); do code="$(job_exit_code "$CLI_LABEL")"; [ -n "$code" ] && break; sleep 1; done
    if [ "$code" = 0 ] && [ -s "$CLI_OUT" ]; then
        result PASS C9 "bundled Claude CLI runs: $(head -1 "$CLI_OUT" | cut -c1-80)"
    else
        result FAIL C9 "bundled Claude CLI did not run (exit ${code:-timeout}): $(head -2 "$CLI_OUT" 2>/dev/null | tr '\n' ' ' | cut -c1-200)"
    fi
elif [ "$DEPLOYED" = 1 ]; then
    result FAIL C9 "no bundled Claude CLI at _internal/claude_agent_sdk/_bundled/claude$EXE_SUFFIX"
else
    result SKIP C9 "nothing deployed"
fi

# ---------------------------------------------------------------------------
# C14: the bundled Python runs skills ("$SWARM_BACKEND_BIN" py, S3c): the standard library and
# every skill's declared python-deps import, and a real skill script runs from the deployed skills
# ---------------------------------------------------------------------------
PY_LABEL="$LABEL.py"; PYS_LABEL="$LABEL.pys"
SKILL_SCRIPT="$RT/_internal/skills/s_frontend-design/scripts/search.py"
if [ "$DEPLOYED" = 1 ]; then
    PY_OUT="$WORK/py.out"; PYS_OUT="$WORK/pys.out"
    # the declared list is read from the DEPLOYED skills with the same helper the build uses
    start_job "$PY_LABEL" "$PY_OUT" "$RT/python-backend$EXE_SUFFIX" py -c \
        "import sys, importlib; sys.path.insert(0, sys.argv[1]); from skill_python_deps import bundled_imports; mods = ['json.tool', 'http.server', 'csv', 'sqlite3', 'xml.dom.minidom', 'email.mime.text'] + bundled_imports(sys.argv[2]); [importlib.import_module(m) for m in mods]; print('py-imports-ok', len(mods))" \
        "$REPO_ROOT/backend/scripts" "$RT/_internal/skills" >/dev/null 2>&1
    start_job "$PYS_LABEL" "$PYS_OUT" "$RT/python-backend$EXE_SUFFIX" py "$SKILL_SCRIPT" "fintech banking" --domain color >/dev/null 2>&1
    c1=""; c2=""
    for _ in $(seq 1 60); do
        c1="$(job_exit_code "$PY_LABEL")"; c2="$(job_exit_code "$PYS_LABEL")"
        [ -n "$c1" ] && [ -n "$c2" ] && break; sleep 1
    done
    if [ "$c1" = 0 ] && grep -q py-imports-ok "$PY_OUT" && [ "$c2" = 0 ] && [ -s "$PYS_OUT" ]; then
        result PASS C14 "bundled py imports the stdlib + skill python-deps and runs a skill script"
    else
        result FAIL C14 "bundled py: imports exit ${c1:-timeout} ($(tail -1 "$PY_OUT" 2>/dev/null | cut -c1-120)); skill script exit ${c2:-timeout} ($(tail -1 "$PYS_OUT" 2>/dev/null | cut -c1-120))"
    fi
else
    result SKIP C14 "nothing deployed"
fi

# ---------------------------------------------------------------------------
# C10: first-run seed; C11: no system python; C12 / C13: macOS signature and Gatekeeper
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
echo "platform: $OS   installer: ${SRC}   version: $VERSION   work: $WORK"
if [ "$FAILS" -gt 0 ]; then
    echo "RESULT: FAIL ($FAILS check(s) failed)"
    exit 1
fi
if [ "$SELF_TEST" = 1 ]; then
    echo "RESULT: self-test only (--backend-dir, --no-quarantine or macOS --launcher direct) — not a release verdict"
    exit 2
fi
echo "RESULT: PASS"
exit 0
