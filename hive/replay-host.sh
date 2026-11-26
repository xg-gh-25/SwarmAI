#!/bin/bash
# SwarmAI Hive — the host side of Feature Map replay (design docs/design/2026-11-18-feature-map.md
# §6.3). Run as root by a Hive's first boot (backend/hive/user_data.py) and by every update (the
# provisioner's SSM update script), each under a deadline, from a root-owned copy
# (/usr/local/libexec/swarmai/replay-host.sh; an update takes it from the checked package, never
# from the install tree the Hive user owns); a failure only warns, and replay then
# reports its walks `not_run: runtime unavailable` (the engine checks all of this itself before
# it runs anything: backend/core/feature_map/replay_user.py).
#
#   1. The system packages the pinned Chromium headless shell needs on Amazon Linux 2023 (found
#      by `ldd` on the shell and a launch that renders text): its libraries, fontconfig and fonts
#      (without them it starts but every glyph is blank, so screenshots show nothing), and
#      nftables for step 4. dnf runs only when one of them is missing.
#   2. The replay user (swarm-replay): a system user with its own home (its package caches), the
#      user every process of a replay runs as. The code it runs is the repo under test, so it has
#      no way into the Hive user's home (the person's conversations, memory, stored secrets): the
#      home is 0700 with no ACL entry for it (an older version gave it traverse there, which
#      exposed everything world-readable below; that entry is removed). What a replay runs, the
#      pinned runtime and the task tools, lives in the shared location instead:
#      /var/lib/swarmai/replay, made here by root inside root's /var/lib/swarmai (so the Hive
#      user cannot swap it for a link), owned by the Hive user with the replay user's group, 0750:
#      the Hive user writes it, the replay user reads it, nobody else enters
#      (backend/core/feature_map/replay_user.py SHARED_DIR, hive/bin/task-tools).
#      Once (a marker in /var/lib/swarmai), the Hive user's existing ~/.swarm-ai and ~/.claude are
#      made private: group and other bits removed, the owner's kept, run as the Hive user so a
#      link in the tree can never make root change another file; the service's UMask=0077
#      (hive/swarmai-hive.service) keeps what it writes later private.
#   3. sudoers: the Hive user may run commands as swarm-replay (never as root), without a
#      password, without a pty or a PAM session (on sudo-rs, which has no pam_session setting,
#      sudo's PAM session opens no logind session on Ubuntu); the file is checked with visudo
#      before it is put in place.
#   4. The network boundary: an nftables table that rejects swarm-replay's traffic to the EC2
#      instance metadata service (169.254.169.254, fd00:ec2::254), loaded now and at every boot by
#      its own systemd unit, so a replayed app cannot read the instance role's credentials.
#
# An image (the Elastic Workers image, hive/agentcore/Dockerfile) runs steps 1-3 at build time,
# as root (`replay-host.sh image`): its container then runs as the Hive user with no systemd, so
# nothing could load step 4's rule at run time, and replay there finds the metadata service
# open to the replay user and reports its walks not_run with that reason. A failed step fails
# the image build instead of only warning.
#
# Usage: replay-host.sh                       the steps above (idempotent)
#        replay-host.sh image                 steps 1-3, for an image build (no firewall)
#        replay-host.sh render-nft <uid>        print the nftables ruleset for that uid
#        replay-host.sh render-sudoers <user> [classic|rs]   print the sudoers drop-in for the
#                                       Hive user (default: the dialect of the installed sudo)
#        replay-host.sh render-unit <nft>       print the systemd unit that loads the ruleset
#        replay-host.sh packages                print the package set
# Environment: SWARM_USER (the Hive user, default swarm), REPLAY_HOST_DNF_DEADLINE_S (default 200),
# REPLAY_HOST_STATE_DIR (root's state folder, default /var/lib/swarmai; the tests use a temp one).

set -uo pipefail

REPLAY_USER="swarm-replay"
REPLAY_HOME="/var/lib/swarm-replay"
OWNER="${SWARM_USER:-swarm}"
DNF_DEADLINE_S="${REPLAY_HOST_DNF_DEADLINE_S:-200}"
NFT_FILE="/etc/swarmai/replay-imds.nft"
NFT_UNIT="/etc/systemd/system/swarmai-replay-imds.service"
SUDOERS_FILE="/etc/sudoers.d/swarmai-replay"
STATE_DIR="${REPLAY_HOST_STATE_DIR:-/var/lib/swarmai}"
SHARED_DIR="$STATE_DIR/replay"
PRIVATE_MARK="$STATE_DIR/home-private.v1"

# The shell's libraries, by the soname ldd reported missing: libatk-1.0 (atk), libatspi
# (at-spi2-core), libasound (alsa-lib), libX11, libxcb, libXcomposite, libXdamage, libXext,
# libXfixes, libXrandr, libxkbcommon, libgbm (mesa-libgbm). dnf resolves their own dependencies.
BROWSER_LIBRARIES="atk at-spi2-core alsa-lib libX11 libxcb libXcomposite libXdamage libXext libXfixes libXrandr libxkbcommon mesa-libgbm"
# Text in screenshots: fontconfig (the shell reads /etc/fonts) and fonts for Latin, CJK and emoji.
BROWSER_FONTS="fontconfig liberation-sans-fonts liberation-serif-fonts liberation-mono-fonts google-noto-sans-cjk-ttc-fonts google-noto-emoji-color-fonts"
BOUNDARY_PACKAGES="nftables"

render_nft() {
    # One transaction: declare (so the delete never fails), delete, define. Loading it again
    # replaces the table instead of adding the rules twice.
    local uid="$1"
    case "$uid" in
        ''|*[!0-9]*) echo "render-nft: not a uid: $uid" >&2; return 2 ;;
    esac
    cat <<EOF
table inet swarmai_replay
delete table inet swarmai_replay
table inet swarmai_replay {
    chain output {
        type filter hook output priority filter; policy accept;
        meta skuid $uid ip daddr 169.254.169.254 counter reject
        meta skuid $uid ip6 daddr fd00:ec2::254 counter reject
    }
}
EOF
}

# Which sudo this machine has: sudo-rs (the default sudo from Ubuntu 25.10 on) or the classic
# sudo. Their sudoers files differ in which Defaults they know.
sudo_dialect() {
    case "$(sudo --version 2>/dev/null | head -n 1)" in
        sudo-rs*) echo rs ;;
        *) echo classic ;;
    esac
}

render_sudoers() {
    local owner="$1" dialect="${2:-$(sudo_dialect)}" defaults
    case "$owner" in
        ''|*[!a-z0-9_-]*) echo "render-sudoers: not a user name: $owner" >&2; return 2 ;;
    esac
    # sudo-rs knows neither requiretty nor pam_session: it never asks for a tty, and its PAM
    # session on Ubuntu is common-session-noninteractive, which starts no logind session.
    case "$dialect" in
        classic) defaults='!use_pty, !requiretty, !pam_session' ;;
        rs) defaults='!use_pty' ;;
        *) echo "render-sudoers: not a sudo dialect: $dialect" >&2; return 2 ;;
    esac
    cat <<EOF
# SwarmAI Feature Map replay (hive/replay-host.sh): $owner starts a replay's processes as
# $REPLAY_USER, whose traffic to the instance metadata service the firewall rejects.
Defaults>$REPLAY_USER $defaults
$owner ALL=($REPLAY_USER) NOPASSWD: ALL
EOF
}

render_unit() {
    local nft="$1"
    cat <<EOF
[Unit]
Description=SwarmAI replay user: no instance metadata service (hive/replay-host.sh)
DefaultDependencies=no
Before=network-pre.target swarmai-hive.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$nft -f $NFT_FILE

[Install]
WantedBy=multi-user.target
EOF
}

warn() { echo "WARN: replay host: $*; Feature Map replay reports its walks not_run until this is fixed"; }

install_packages() {
    local pkgs="$BROWSER_LIBRARIES $BROWSER_FONTS $BOUNDARY_PACKAGES"
    # shellcheck disable=SC2086
    if rpm -q $pkgs >/dev/null 2>&1; then
        echo "replay host: browser libraries, fonts and nftables already installed"
        return 0
    fi
    # shellcheck disable=SC2086
    if timeout "$DNF_DEADLINE_S" dnf install -y -q $pkgs 2>&1 | tail -5; then
        # shellcheck disable=SC2086
        rpm -q $pkgs >/dev/null 2>&1 && { echo "replay host: browser libraries, fonts and nftables installed"; return 0; }
    fi
    warn "dnf could not install the browser packages within ${DNF_DEADLINE_S}s"
    return 1
}

ensure_user() {
    if ! id -u "$REPLAY_USER" >/dev/null 2>&1; then
        useradd --system --user-group --home-dir "$REPLAY_HOME" --create-home \
            --shell /sbin/nologin --comment "SwarmAI Feature Map replay" "$REPLAY_USER" \
            || { warn "useradd $REPLAY_USER failed"; return 1; }
    fi
    install -d -o "$REPLAY_USER" -g "$REPLAY_USER" -m 0700 "$REPLAY_HOME" \
        || { warn "$REPLAY_HOME could not be made"; return 1; }
    echo "replay host: user $REPLAY_USER ($(id -u "$REPLAY_USER"))"
}

owner_home() {
    # OWNER_HOME: the Hive user's home, a real folder (root never changes anything through a link)
    OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
    if [ -z "$OWNER_HOME" ] || [ ! -d "$OWNER_HOME" ]; then
        warn "the Hive user $OWNER has no home"
        return 1
    fi
    if [ -L "$OWNER_HOME" ]; then
        warn "the Hive user's home $OWNER_HOME is a symbolic link; nothing is changed through it"
        return 1
    fi
}

shared_location() {
    if [ -L "$STATE_DIR" ] || [ -L "$SHARED_DIR" ]; then
        warn "$STATE_DIR or $SHARED_DIR is a symbolic link; not used"
        return 1
    fi
    install -d -o root -g root -m 0755 "$STATE_DIR" \
        && install -d -o "$OWNER" -g "$REPLAY_USER" -m 0750 "$SHARED_DIR" \
        || { warn "$SHARED_DIR could not be made (the runtime and the task tools stay out of the replay user's reach)"; return 1; }
    echo "replay host: $SHARED_DIR holds the pinned runtime and the task tools ($OWNER writes, $REPLAY_USER reads)"
}

private_home() {
    owner_home || return 1
    local home="$OWNER_HOME"
    if id -u "$REPLAY_USER" >/dev/null 2>&1 && command -v setfacl >/dev/null 2>&1; then
        setfacl -x "u:${REPLAY_USER}" "$home" \
            || { warn "the replay user's ACL entry on $home could not be removed"; return 1; }
    fi
    chmod 0700 "$home" || { warn "$home could not be made 0700"; return 1; }
    echo "replay host: $home is 0700, with no way in for $REPLAY_USER"
}

tighten_data() {
    # once: the Hive user's existing data, created before the service's UMask=0077. Every
    # folder is walked even when one chmod fails (a file removed meanwhile); what decides is
    # a second walk: the marker is written only when nothing of the Hive user's is left open.
    [ -f "$PRIVATE_MARK" ] && return 0
    owner_home || return 1
    local home="$OWNER_HOME" dir uid left=""
    uid="$(id -u "$OWNER")" || { warn "no uid for $OWNER"; return 1; }
    for dir in "$home/.swarm-ai" "$home/.claude"; do
        [ -d "$dir" ] && [ ! -L "$dir" ] || continue
        runuser -u "$OWNER" -- find -P "$dir" -xdev -ignore_readdir_race -uid "$uid" ! -type l \
            -perm /077 -exec chmod go= {} + 2>/dev/null
        left="$left$(runuser -u "$OWNER" -- find -P "$dir" -xdev -ignore_readdir_race -uid "$uid" \
            ! -type l -perm /077 -print -quit 2>/dev/null)"
    done
    if [ -n "$left" ]; then
        warn "$left (and maybe more) is still readable by others; tried again at the next update"
        return 1
    fi
    : > "$PRIVATE_MARK" || { warn "$PRIVATE_MARK could not be written"; return 1; }
    echo "replay host: $home/.swarm-ai and $home/.claude made private (once)"
}

install_sudoers() {
    local tmp
    tmp="$(mktemp)"
    render_sudoers "$OWNER" > "$tmp" || { rm -f "$tmp"; warn "no sudoers file for $OWNER"; return 1; }
    if visudo -cf "$tmp" >/dev/null 2>&1; then
        install -m 0440 -o root -g root "$tmp" "$SUDOERS_FILE"
        rm -f "$tmp"
        echo "replay host: $SUDOERS_FILE installed"
        return 0
    fi
    rm -f "$tmp"
    warn "visudo rejected the replay sudoers file; not installed"
    return 1
}

install_firewall() {
    local uid
    uid="$(id -u "$REPLAY_USER" 2>/dev/null)" || { warn "no user $REPLAY_USER for the firewall rule"; return 1; }
    command -v nft >/dev/null 2>&1 || { warn "nft is not installed"; return 1; }
    # /etc/swarmai holds the Hive's auth credential and is 0700 (user_data.py); made here only
    # when missing, never loosened. The ruleset in it is read by root only.
    [ -d /etc/swarmai ] || install -d -m 0700 /etc/swarmai
    render_nft "$uid" > "$NFT_FILE.new" && mv -f "$NFT_FILE.new" "$NFT_FILE"
    chmod 0644 "$NFT_FILE"
    render_unit "$(command -v nft)" > "$NFT_UNIT"
    systemctl daemon-reload
    systemctl enable swarmai-replay-imds.service >/dev/null 2>&1 || warn "the firewall unit could not be enabled"
    if systemctl restart swarmai-replay-imds.service; then
        echo "replay host: metadata service rejected for uid $uid (IPv4 and IPv6)"
        return 0
    fi
    warn "the firewall rule did not load (systemctl status swarmai-replay-imds)"
    return 1
}

case "${1:-setup}" in
    render-nft) render_nft "${2:-}"; exit $? ;;
    render-sudoers) render_sudoers "${2:-}" "${3:-}"; exit $? ;;
    render-unit) render_unit "${2:-/usr/sbin/nft}"; exit $? ;;
    packages) echo "$BROWSER_LIBRARIES $BROWSER_FONTS $BOUNDARY_PACKAGES"; exit 0 ;;
    setup|image) ;;
    *) echo "usage: $0 [setup|image|render-nft <uid>|render-sudoers <user> [classic|rs]|render-unit <nft>|packages]" >&2; exit 2 ;;
esac

if [ "$(id -u)" != 0 ]; then
    echo "replay-host.sh: run as root" >&2
    exit 1
fi

status=0
install_packages || status=1
ensure_user || status=1
shared_location || status=1
private_home || status=1
tighten_data || status=1
install_sudoers || status=1
if [ "${1:-setup}" = image ]; then
    echo "replay host: no firewall rule in an image (it is loaded as root at boot, and an image has no root at run time)"
else
    install_firewall || status=1
fi
exit "$status"
