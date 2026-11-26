"""User-data script renderer for Hive EC2 instances.

Generates a parameterized bash script that EC2 runs on first boot.
Downloads the hive package from S3 and checks it against the checksums.txt beside it,
installs dependencies, installs a pinned Caddy release (checksum checked) and configures
it with basic auth, starts services, and tags the instance ready.
"""

import re

_USER_DATA_TEMPLATE = r"""#!/bin/bash
# SwarmAI Hive — EC2 user-data (runs once on first boot)
# Fully automated: pull from S3 -> install -> start -> tag ready
set -euo pipefail
# The log, and the console too: once a first boot that never ended is cleaned up, the
# console output the provisioner copies into the error is all that is left of it.
exec > >(tee -a /var/log/hive-setup.log) 2>&1

echo "=== SwarmAI Hive Setup — $(date) ==="

${tmp_on_disk}
# First boot: nothing of ours is in /tmp yet, so move it to disk now rather than at a
# reboot a Hive may never have. A lazy unmount leaves any service already holding the
# tmpfs (a PrivateTmp namespace) on it; everything opened from here on is on disk.
if mountpoint -q /tmp && [ "$(systemctl is-enabled tmp.mount 2>/dev/null || true)" = masked ]; then
    if umount -l /tmp; then echo "/tmp: now on disk"; else echo "WARN: /tmp stays on tmpfs until the next boot"; fi
fi

HIVE_S3_BUCKET="${s3_bucket}"
HIVE_VERSION="${version}"
HIVE_REGION="${region}"
INSTALL_DIR="/opt/swarmai"
SWARM_USER="swarm"

# ── 1. System packages ──
echo "[1/9] Installing system packages..."
dnf install -y python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make 2>&1 | tail -5

# ── 2. Create swarm user ──
echo "[2/9] Creating swarm user..."
useradd -m -s /bin/bash "$SWARM_USER" 2>/dev/null || true
mkdir -p "/home/$SWARM_USER/.swarm-ai/logs"
chown -R "$SWARM_USER:$SWARM_USER" "/home/$SWARM_USER/.swarm-ai"

# ── 3. Download from S3 ──
echo "[3/9] Downloading hive package from S3..."
mkdir -p "$INSTALL_DIR"
aws s3 cp "s3://$HIVE_S3_BUCKET/v$HIVE_VERSION/swarmai-hive-v$HIVE_VERSION-linux-arm64.tar.gz" \
    /tmp/hive.tar.gz --region "$HIVE_REGION"
# Unpacked only when it is the package checksums.txt (uploaded with it) names: a mismatch
# stops the setup here, before anything is installed.
${package_check}
verify_hive_package /tmp/hive.tar.gz "$HIVE_S3_BUCKET" "$HIVE_VERSION" "$HIVE_REGION"
tar xzf /tmp/hive.tar.gz --strip-components=1 -C "$INSTALL_DIR"
chown -R "$SWARM_USER:$SWARM_USER" "$INSTALL_DIR"
rm /tmp/hive.tar.gz
# M7: Persist the bucket name for the Swarm Out bridge (core/swarm_out_bridge.py).
echo "$HIVE_S3_BUCKET" > "$INSTALL_DIR/.hive-bucket"
# The newest installed Node as the default node / npm / npx; a Hive older than the
# frontend needs does not come up (design 2026-10-31-swarm-out-single-loop-fixes §2.7).
bash "$INSTALL_DIR/hive/ensure-node.sh"

# ── 4. Python venv + deps ──
echo "[4/9] Setting up Python environment..."
cd "$INSTALL_DIR/backend"
sudo -u "$SWARM_USER" python3.12 -m venv .venv
sudo -u "$SWARM_USER" .venv/bin/pip install -q --upgrade pip
sudo -u "$SWARM_USER" .venv/bin/pip install -q -e . 2>&1 | tail -3

${tree_sitter_grammars}

${replay_host}

# ── 5. Install Caddy ──
echo "[5/9] Installing Caddy..."
# A pinned release from GitHub, checked against that release's checksum (CADDY_VERSION).
# H2: a failure aborts the setup: continuing without Caddy = instance looks healthy but
# 401s everything.
${caddy_install}
install_caddy /usr/bin/caddy
mkdir -p /etc/caddy /var/log/caddy

# ── 6. Configure Caddy (HTTP, CloudFront terminates TLS) ──
echo "[6/9] Configuring Caddy with basic auth (the access list)..."
cat > /etc/caddy/Caddyfile << 'CADDY'
# ORIGIN-GUARD-GLOBAL-BEGIN
# Only this Hive's CloudFront distribution sends X-Swarm-Origin with this Hive's secret.
# Caddy runs basic_auth before respond by default; this order makes the 403 below come first.
# The default logger (Caddy's error log, in the journal) writes request headers too.
{
    order respond before basic_auth
    # No admin API: the agent on the Hive must not be able to load another config (Caddy
    # runs as root). Every change to Caddy restarts the service, none uses the API.
    admin off
    log default {
        format filter {
            wrap json
            fields {
                request>headers>X-Swarm-Origin delete
            }
        }
    }
}
# ORIGIN-GUARD-GLOBAL-END
:80 {
    # Health check BEFORE auth — enables external monitors (G11)
    handle /health {
        reverse_proxy 127.0.0.1:18321
    }

    # /health is exempt from the origin guard and basic auth only as the literal request
    # path, a query allowed: {http.request.uri} is the raw request target. The path matcher
    # and {http.request.uri.path} clean, decode and case-fold, but the backend gets the raw
    # path, so /x/../health, //health, /HEALTH and /%68ealth must not pass.
    # ORIGIN-GUARD-RULE-BEGIN
    @foreign_origin {
        not expression `{http.request.uri} == "/health" || {http.request.uri}.startsWith("/health?")`
        not header X-Swarm-Origin ${origin_secret}
    }
    respond @foreign_origin 403
    request_header -X-Swarm-Origin
    # ORIGIN-GUARD-RULE-END
    @protected not expression `{http.request.uri} == "/health" || {http.request.uri}.startsWith("/health?")`
    basic_auth @protected {
        import /etc/swarmai-auth/hive-users
    }

    # SSE streaming — MUST be before generic /api/* (first-match)
    handle /api/chat/stream {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
            transport http {
                read_timeout 300s
            }
        }
    }
    handle /api/chat/answer-question {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
            transport http {
                read_timeout 120s
            }
        }
    }
    handle /api/chat/cmd-permission-continue {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
            transport http {
                read_timeout 120s
            }
        }
    }
    # CUSTOM_ROUTES_ABOVE — do not remove this marker
    handle /api/* {
        reverse_proxy 127.0.0.1:18321
    }
    handle {
        root * /opt/swarmai/desktop/dist
        try_files {path} /index.html
        file_server
    }
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }
    log {
        # ORIGIN-GUARD-LOG-BEGIN
        format filter {
            wrap json
            fields {
                request>headers>X-Swarm-Origin delete
            }
        }
        # ORIGIN-GUARD-LOG-END
        output file /var/log/caddy/hive-access.log {
            roll_size 100mb
            roll_keep 5
        }
    }
}
CADDY

# ── 6b. The access list (both auth layers) and the backend's fallback credential ──
# One "<user> <bcrypt hash>" line per key: the Host's own key and one per person. Caddy
# imports it inside basic_auth @protected (above) and the backend's app-layer auth
# (middleware/hive_auth.py) reads it, so both layers check the same list. The Hive user can
# read it (the backend runs as that user and picks up a changed list without a restart)
# but not write it; only root does (first boot here, then the Host's apply over SSM:
# hive/access.py). The directory is created with its mode in one step, the file filled in
# place after it exists at its mode, so the list is never writable by anyone but root.
echo "[6b/9] Writing the access list..."
install -m 750 -o root -g "$SWARM_USER" -d /etc/swarmai-auth
install -m 640 -o root -g "$SWARM_USER" /dev/null /etc/swarmai-auth/hive-users
echo '${access_b64}' | base64 -d > /etc/swarmai-auth/hive-users
# The backend's HIVE_USER / HIVE_PASS_HASH: used only by a package that predates the access
# list (a downgrade), and always the Host's own key. Root-only, in the root-only
# /etc/swarmai (systemd reads EnvironmentFile= as root). The hash is base64-encoded here
# to avoid any shell/systemd `$` expansion of the $2b$.. hash.
install -m 700 -d /etc/swarmai
HIVE_HASH_DECODED=$(echo '${auth_hash_b64}' | base64 -d)
install -m 600 /dev/null /etc/swarmai/hive-auth.env
umask 077
cat > /etc/swarmai/hive-auth.env << HIVEAUTHENV
HIVE_USER=${auth_user}
HIVE_PASS_HASH=$HIVE_HASH_DECODED
HIVEAUTHENV
chown root:root /etc/swarmai/hive-auth.env
chmod 600 /etc/swarmai/hive-auth.env

# ── 7. Systemd services ──
echo "[7/9] Installing systemd services..."
chmod +x "$INSTALL_DIR/hive/swarmai-hive.sh"
cp "$INSTALL_DIR/hive/swarmai-hive.service" /etc/systemd/system/

cat > /etc/systemd/system/caddy.service << 'SVC'
[Unit]
Description=Caddy HTTP Server
After=network-online.target
[Service]
Type=simple
Environment=HOME=/var/lib/caddy
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable swarmai-hive caddy

# ── 8. Start services ──
echo "[8/9] Starting services..."
systemctl start swarmai-hive
echo "Waiting for backend..."
HEALTHY=false
for i in $(seq 1 ${backend_health_wait_s}); do
    if curl -sf http://127.0.0.1:18321/health > /dev/null 2>&1; then
        echo "Backend healthy after ${i}s"
        HEALTHY=true
        break
    fi
    sleep 1
done
systemctl start caddy

# ── 9. Tag instance ready ──
echo "[9/9] Tagging instance..."
TOKEN=$(curl -sf -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
INSTANCE_ID=$(curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

if [ "$HEALTHY" = true ]; then
    TAG_STATUS="ready"
else
    TAG_STATUS="error"
fi

aws ec2 create-tags --resources "$INSTANCE_ID" \
    --tags Key=HiveStatus,Value="$TAG_STATUS" Key=HiveVersion,Value="$HIVE_VERSION" \
    --region "$HIVE_REGION"

# Log rotation
tee /etc/logrotate.d/swarmai > /dev/null << 'LOGROTATE'
/home/swarm/.swarm-ai/logs/backend.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE

echo "=== Hive Setup Complete — status=$TAG_STATUS — $(date) ==="
"""


def caddy_hash_password(password: str) -> str:
    """Generate a bcrypt hash for Caddy basic_auth and the backend (an access list line).

    Caddy's ``basicauth`` directive ONLY accepts bcrypt hashes — SHA-256,
    MD5, and plaintext are all rejected.  bcrypt is therefore a hard
    requirement; if it's missing the deploy must fail loudly rather than
    produce an instance with broken authentication.

    The ``bcrypt`` package is listed in pyproject.toml [dependencies].
    """
    import bcrypt
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=14))
    return hashed.decode()


# ── Origin guard: only this Hive's CloudFront distribution reaches the origin ──
# Every CloudFront distribution is in the global prefix list the security group allows,
# so a distribution someone else creates could reach the origin. Each Hive has a random
# secret (secrets.token_urlsafe(32)) that only its own distribution sends as
# X-Swarm-Origin (CustomHeaders); the Caddyfile answers 403 to every other request
# except /health, before basic auth, strips the header before the backend, and no log
# (the access log and the default/error log) keeps it. The guard is
# the three marked blocks of the heredoc above; the SSM editor below inserts exactly
# those blocks into an existing Hive's Caddyfile, so a migrated Hive and a new one run
# the same file, and removing them restores the file the Hive had before.
ORIGIN_HEADER = "X-Swarm-Origin"
ORIGIN_SECRET_PATTERN = r"[A-Za-z0-9_-]{32,128}"
_ORIGIN_SECRET_RE = re.compile(ORIGIN_SECRET_PATTERN)
_GUARD_MARK_PATTERN = (r"^[ \t]*# ORIGIN-GUARD-(GLOBAL|RULE|LOG)-BEGIN\n.*?"
                       r"^[ \t]*# ORIGIN-GUARD-\1-END\n")
_GUARD_MARK_RE = re.compile(_GUARD_MARK_PATTERN, re.S | re.M)
_CADDY_HEREDOC_OPEN = "cat > /etc/caddy/Caddyfile << 'CADDY'\n"

# ── /health: exempt only as the literal request path ──
# The origin guard and basic auth both exempt /health with this expression on the raw
# request target. Caddy's path matcher and {http.request.uri.path} compare a cleaned,
# decoded, case-folded path while the backend gets the raw one, so a path matcher also
# exempted /x/../health, //health, /HEALTH and /%68ealth. A query stays allowed (the path
# is still /health). The heredoc above writes these lines; health_exempt_fix_sh() below
# gives an existing Hive the same file on its next update.
HEALTH_EXEMPT_EXPR = '{http.request.uri} == "/health" || {http.request.uri}.startsWith("/health?")'
HEALTH_PROTECTED_LINE = f"    @protected not expression `{HEALTH_EXEMPT_EXPR}`\n"
_HEALTH_EXEMPT_COMMENT = (
    "    # /health is exempt from the origin guard and basic auth only as the literal request\n"
    "    # path, a query allowed: {http.request.uri} is the raw request target. The path matcher\n"
    "    # and {http.request.uri.path} clean, decode and case-fold, but the backend gets the raw\n"
    "    # path, so /x/../health, //health, /HEALTH and /%68ealth must not pass.\n")
# What a Hive rendered before the fix has in place of the comment and HEALTH_PROTECTED_LINE
# (its guard block, when it has one, is replaced whole from the template).
_LEGACY_PROTECTED_LINE = "    @protected not path /health\n"

# (block name, the line it sits next to in the Caddyfile, before/after that line)
_GUARD_ANCHORS = (
    ("GLOBAL", ":80 {\n", "before"),
    ("RULE", HEALTH_PROTECTED_LINE, "before"),
    ("LOG", "    log {\n", "after"),
)


def _guard_blocks() -> list:
    """[(block text with ${origin_secret}, anchor, where)], read from the template so the
    editor can never insert anything but what a new Hive gets. Fails loud on drift."""
    caddy = _USER_DATA_TEMPLATE.split(_CADDY_HEREDOC_OPEN, 1)[1].split("\nCADDY\n", 1)[0]
    found = {m.group(1): m.group(0) for m in _GUARD_MARK_RE.finditer(caddy + "\n")}
    out = []
    for name, anchor, where in _GUARD_ANCHORS:
        block = found.get(name)
        if block is None or (block + anchor if where == "before" else anchor + block) not in caddy:
            raise RuntimeError(f"origin guard block {name} drifted from its anchor")
        out.append((block, anchor, where))
    return out


# Runs on the instance (python3 of Amazon Linux 2023: keep it 3.9-compatible and stdlib
# only). argv: apply|remove <Caddyfile>; apply reads the secret from stdin. It writes the
# file only when every anchor was found once (atomic replace, mode kept) and never prints
# the secret.
_GUARD_EDITOR_TEMPLATE = r'''
import os, re, shutil, sys
BLOCKS = __BLOCKS__
MARK = re.compile(__MARK__, re.S | re.M)
mode, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    text = f.read()
out = MARK.sub("", text)
ADMIN_ONLY = __ADMIN_ONLY__
if mode == "apply":
    out = out.replace(ADMIN_ONLY, "", 1)  # the guard's own global block has admin off
    secret = sys.stdin.read().strip()
    if not re.fullmatch(__SECRET_RE__, secret):
        sys.exit("ERROR: the origin secret has an unexpected shape; Caddyfile not changed")
    for block, anchor, where in BLOCKS:
        if out.count(anchor) != 1:
            sys.exit("ERROR: this Caddyfile is not in the layout the origin guard needs "
                     "(a line it goes next to is missing or repeated); Caddyfile not changed. "
                     "Update the Hive first (its update rewrites the /health lines the guard "
                     "goes next to), or redeploy it")
        block = block.replace("${origin_secret}", secret)
        out = out.replace(anchor, block + anchor if where == "before" else anchor + block, 1)
elif mode == "remove":
    if not re.search(r"^\s*admin off\s*$", out, re.M) and out.count(":80 {\n") == 1:
        out = out.replace(":80 {\n", ADMIN_ONLY + ":80 {\n", 1)
else:
    sys.exit("ERROR: unknown mode")
if out != text:
    tmp = path + ".origin-new"
    with open(tmp, "w") as f:
        f.write(out)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
print("Caddyfile origin guard: " + mode)
'''


def origin_guard_editor_source() -> str:
    """The Python the SSM command runs on an existing Hive to add (``apply``) or remove
    (``remove``) the origin guard in /etc/caddy/Caddyfile."""
    return (_GUARD_EDITOR_TEMPLATE
            .replace("__BLOCKS__", repr(_guard_blocks()))
            .replace("__MARK__", repr(_GUARD_MARK_PATTERN))
            .replace("__ADMIN_ONLY__", repr(ADMIN_OFF_GLOBAL))
            .replace("__SECRET_RE__", repr(ORIGIN_SECRET_PATTERN)))


_guard_blocks()  # a template edit that moves a guard block fails at import, not on a Hive


def _check_health_exempt_lines() -> None:
    """The template carries the exemption as the update step writes it: the comment and
    HEALTH_PROTECTED_LINE together once (outside the guard), and the guard's own line."""
    caddy = _USER_DATA_TEMPLATE.split(_CADDY_HEREDOC_OPEN, 1)[1].split("\nCADDY\n", 1)[0]
    base = _GUARD_MARK_RE.sub("", caddy + "\n")
    rule = [m.group(0) for m in _GUARD_MARK_RE.finditer(caddy + "\n") if m.group(1) == "RULE"]
    if (base.count(_HEALTH_EXEMPT_COMMENT + HEALTH_PROTECTED_LINE) != 1
            or _LEGACY_PROTECTED_LINE in caddy or len(rule) != 1
            or f"        not expression `{HEALTH_EXEMPT_EXPR}`\n" not in rule[0]):
        raise RuntimeError("the /health exemption lines drifted from the template")


_check_health_exempt_lines()


# Runs on the instance (python3 3.9, stdlib only), from the update script.
#   edit <Caddyfile>: prints changed / unchanged / unknown / stuck. A file with the legacy
#     line gets the template's comment and line in its place, and a guard it has is taken
#     out and put back from the template with its own secret, so the result is the file a
#     new Hive renders. Written atomically (mode kept) only when changed. A file it cannot
#     edit is left alone: unknown when its basic auth is one of two shapes known safe
#     (on every request, as the oldest `basicauth *` layout; or `@protected` defined once,
#     as exactly HEALTH_PROTECTED_LINE), stuck otherwise (it may still exempt by path).
#   origin-header <Caddyfile>: prints "X-Swarm-Origin: <secret>" when the file has the
#     guard (the caller sends it to a 0600 file, never to the output), else nothing.
_HEALTH_FIX_EDITOR_TEMPLATE = r"""
import os, re, shutil, sys
BLOCKS = __BLOCKS__
MARK = re.compile(__MARK__, re.S | re.M)
LEGACY, FIXED, PROTECTED = __LEGACY__, __FIXED__, __PROTECTED__
def basic_auth_is_safe(text):
    # An allowlist of the two shapes whose basic auth exempts at most the literal /health:
    # basic auth with no matcher (the oldest `basicauth *` layout), or `@protected` defined
    # once, as exactly the fixed line. Anything else may still exempt by path. Checked line
    # by line, as the files this update renders are written: a hand edit that nests basic
    # auth in a handle or adds a site block is not verified here.
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    auth = [l for l in lines if l.split()[0] in ("basic_auth", "basicauth")]
    defs = [l for l in lines if l.split()[0] == "@protected"]
    if len(auth) != 1:
        return False
    if auth[0] in ("basic_auth * {", "basicauth * {", "basic_auth {", "basicauth {"):
        return True
    return auth[0] == "basic_auth @protected {" and defs == [PROTECTED.strip()]
mode, path = sys.argv[1], sys.argv[2]
with open(path) as f:
    text = f.read()
marked = list(MARK.finditer(text))
rule = [m.group(0) for m in marked if m.group(1) == "RULE"]
secret = None
if rule:
    found = re.search(r"not header X-Swarm-Origin (\S+)", rule[0])
    if len(rule) == 1 and found and re.fullmatch(__SECRET_RE__, found.group(1)):
        secret = found.group(1)
if mode == "origin-header":
    if secret is not None:
        sys.stdout.write("X-Swarm-Origin: " + secret + "\n")
    sys.exit(0)
if mode != "edit":
    sys.exit("ERROR: unknown mode")
def unknown():
    print("unknown" if basic_auth_is_safe(text) else "stuck")
    sys.exit(0)
if marked and secret is None:
    unknown()  # a guard this editor cannot read: leave the file as it is
out = MARK.sub("", text)
if out.count(LEGACY) == 1 and FIXED not in out:
    out = out.replace(LEGACY, FIXED, 1)
elif out.count(FIXED) != 1 or LEGACY in out:
    unknown()
if secret is not None:
    for block, anchor, where in BLOCKS:
        if out.count(anchor) != 1:
            unknown()
        block = block.replace("${origin_secret}", secret)
        out = out.replace(anchor, block + anchor if where == "before" else anchor + block, 1)
if out == text:
    print("unchanged")
    sys.exit(0)
tmp = path + ".health-new"
with open(tmp, "w") as f:
    f.write(out)
shutil.copymode(path, tmp)
os.replace(tmp, path)
print("changed")
"""


def health_fix_editor_source() -> str:
    """The Python the update script runs on an existing Hive (modes: see above)."""
    return (_HEALTH_FIX_EDITOR_TEMPLATE
            .replace("__BLOCKS__", repr(_guard_blocks()))
            .replace("__MARK__", repr(_GUARD_MARK_PATTERN))
            .replace("__LEGACY__", repr(_LEGACY_PROTECTED_LINE))
            .replace("__FIXED__", repr(_HEALTH_EXEMPT_COMMENT + HEALTH_PROTECTED_LINE))
            .replace("__PROTECTED__", repr(HEALTH_PROTECTED_LINE))
            .replace("__SECRET_RE__", repr(ORIGIN_SECRET_PATTERN)))


# The update script's first step (provisioner.update), before the install is touched:
# an existing Hive's /etc/caddy/Caddyfile gets the /health lines a new Hive renders. When
# the file changed: caddy validate, restart, then check both ways (Brain convention: a
# deny-only check passes a rule that blocks everyone): /health is still answered, and
# /x/../health is refused without the origin header and, on a guarded Hive, gets 401
# (basic auth) with this Hive's secret. Any failure puts the previous file back (and
# restarts Caddy with it when it had been restarted) and stops the update, so the Hive
# keeps its version and the card shows the reason. So does a file the editor cannot edit
# whose basic auth is not one of the two shapes known safe (editor: basic_auth_is_safe):
# the update stops, file unchanged. A safe one it cannot edit (the oldest `basicauth *`
# layout, or a fixed file edited by hand) is left as it is with a WARN.
_HEALTH_FIX_SH_TEMPLATE = r"""# ── Caddy: /health exempt only as the literal request path (hive/user_data.py) ──
HEALTH_CADDYFILE="/etc/caddy/Caddyfile"
HEALTH_FIX_PY=$(echo '__EDITOR_B64__' | base64 -d)
health_fix_fail() {
  cp -p "$HEALTH_CADDYFILE.health-bak" "$HEALTH_CADDYFILE"
  rm -f "$HEALTH_CADDYFILE.health-bak"
  if [ "$1" = restarted ]; then
    systemctl restart caddy || true
  fi
  echo "ERROR: Caddy /health fix: $2; previous Caddyfile restored, Hive not updated"
  exit 1
}
health_code() {
  # $1 path (sent as is), $2 optional header file: the code Caddy answers once it is up
  local code="000" i
  for i in $(seq 1 15); do
    if [ -n "${2:-}" ]; then
      code=$(curl --path-as-is -s -o /dev/null -w '%{http_code}' -H "@$2" "http://127.0.0.1$1" || true)
    else
      code=$(curl --path-as-is -s -o /dev/null -w '%{http_code}' "http://127.0.0.1$1" || true)
    fi
    if [ "$code" != "000" ]; then
      break
    fi
    sleep 1
  done
  echo "$code"
}
if [ -f "$HEALTH_CADDYFILE" ]; then
  cp -p "$HEALTH_CADDYFILE" "$HEALTH_CADDYFILE.health-bak"
  HEALTH_FIX=$(python3 -c "$HEALTH_FIX_PY" edit "$HEALTH_CADDYFILE") || health_fix_fail no "the edit failed"
  case "$HEALTH_FIX" in
    unchanged)
      rm -f "$HEALTH_CADDYFILE.health-bak"
      echo "Caddy /health exemption: already the literal request path"
      ;;
    changed)
      caddy validate --config "$HEALTH_CADDYFILE" --adapter caddyfile >/dev/null 2>&1 \
        || health_fix_fail no "caddy validate rejected the edited file"
      systemctl restart caddy || health_fix_fail restarted "caddy did not restart"
      CODE=$(health_code /health)
      case "$CODE" in
        000|401|403) health_fix_fail restarted "/health answered $CODE" ;;
      esac
      CODE=$(health_code /x/../health)
      case "$CODE" in
        401|403) ;;
        *) health_fix_fail restarted "/x/../health answered $CODE without the origin header" ;;
      esac
      HEALTH_HDR=$(mktemp)
      python3 -c "$HEALTH_FIX_PY" origin-header "$HEALTH_CADDYFILE" > "$HEALTH_HDR" || true
      if [ -s "$HEALTH_HDR" ]; then
        CODE=$(health_code /x/../health "$HEALTH_HDR")
        if [ "$CODE" != "401" ]; then
          rm -f "$HEALTH_HDR"
          health_fix_fail restarted "/x/../health answered $CODE with the origin secret, not 401"
        fi
      fi
      rm -f "$HEALTH_HDR" "$HEALTH_CADDYFILE.health-bak"
      echo "Caddy /health exemption: now the literal request path only"
      ;;
    stuck)
      rm -f "$HEALTH_CADDYFILE.health-bak"
      echo "ERROR: Caddy /health fix: the Caddyfile's basic-auth exemption is not one this update can verify or edit; fix it by hand or redeploy. Hive not updated"
      exit 1
      ;;
    unknown)
      rm -f "$HEALTH_CADDYFILE.health-bak"
      echo "WARN: Caddy /health exemption: $HEALTH_CADDYFILE cannot be edited by this update; its one basic-auth line has no matcher or the literal /health one (nesting not checked); left as it is"
      ;;
    *)
      health_fix_fail no "unexpected editor result '$HEALTH_FIX'"
      ;;
  esac
else
  echo "WARN: no $HEALTH_CADDYFILE; the Caddy /health fix was skipped"
fi"""


def health_exempt_fix_sh() -> str:
    """The update-script step that gives an existing Hive the literal /health exemption."""
    import base64
    editor_b64 = base64.b64encode(health_fix_editor_source().encode()).decode()
    return _HEALTH_FIX_SH_TEMPLATE.replace("__EDITOR_B64__", editor_b64)


def valid_origin_secret(value) -> bool:
    return isinstance(value, str) and bool(_ORIGIN_SECRET_RE.fullmatch(value))


# ── The access list (design 2026-11-26-hive-access-credentials) ──────────────
# One "<user> <bcrypt hash>" line per key, the Host's own key first. Caddy imports the file
# inside basic_auth @protected and the backend's app-layer auth (middleware/hive_auth.py)
# reads it, so both layers check one list. Readable by the Hive user, written only by root:
# first boot (the template above), the update step access_list_fix_sh() that moves an
# existing Hive to it, and access_apply_sh(), which the Host runs over SSM for every
# change (hive/access.py). Every writer replaces the file whole.
ACCESS_LIST_DIR = "/etc/swarmai-auth"
ACCESS_LIST_PATH = ACCESS_LIST_DIR + "/hive-users"
ACCESS_USER_PATTERN = r"[a-z0-9-]{1,32}"
ACCESS_HASH_PATTERN = r"\$2[abxy]\$[0-9]{2}\$[./A-Za-z0-9]{53}"
ACCESS_LINE_PATTERN = rf"^({ACCESS_USER_PATTERN}) ({ACCESS_HASH_PATTERN})$"
_ACCESS_LINE_RE = re.compile(ACCESS_LINE_PATTERN)
ACCESS_IMPORT_LINE = f"        import {ACCESS_LIST_PATH}\n"
_ACCESS_BLOCK = "    basic_auth @protected {\n" + ACCESS_IMPORT_LINE + "    }\n"
# Caddy's admin API off (Caddy runs as root; the agent on the Hive must not be able to load
# another config). A guarded Caddyfile has it in the origin guard's global block (the
# template); a Caddyfile without the guard gets this global block of its own instead, so
# the API is off whether or not the guard is there: render_user_data without a secret, the
# guard editor's remove (it puts this block back) and apply (it takes it out before adding
# the guard's own), and the access list update step for a file that has neither.
ADMIN_OFF_GLOBAL = "{\n    admin off\n}\n"


def access_list_text(entries: list[tuple[str, str]]) -> str:
    """The access list file for *entries* ``[(user, bcrypt hash)]``. Refuses an empty
    list, a line that is not ``<user> <bcrypt hash>`` and a user listed twice (Caddy
    refuses a repeated user, and the backend denies everything on such a file)."""
    if not entries:
        raise ValueError("the access list needs at least one key")
    seen: set[str] = set()
    lines = []
    for user, hashed in entries:
        line = f"{user} {hashed}"
        if not _ACCESS_LINE_RE.match(line) or user in seen:
            raise ValueError(f"invalid or repeated access list entry for user {user!r}")
        seen.add(user)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _check_access_block() -> None:
    caddy = _USER_DATA_TEMPLATE.split(_CADDY_HEREDOC_OPEN, 1)[1].split("\nCADDY\n", 1)[0]
    if caddy.count(_ACCESS_BLOCK) != 1:
        raise RuntimeError("the basic_auth access list block drifted from the template")
    if "    admin off\n" not in [m.group(0) for m in _GUARD_MARK_RE.finditer(caddy + "\n")
                                if m.group(1) == "GLOBAL"][0]:
        raise RuntimeError("admin off is missing from the template's global block")


_check_access_block()


# Runs on the instance (python3 3.9, stdlib only, as root), from the update script. argv:
# <Caddyfile> <access list>. A Caddyfile whose basic_auth @protected block holds one inline
# "<user> <hash>" line (what every Hive rendered before the access list) gets the template's
# import block in its place, and that line becomes the access list (root:<Hive user>
# 0640, its directory 0750). Either way the file gets Caddy's admin API off when it does
# not have it yet (in its global block, or ADMIN_OFF_GLOBAL). Prints "on" when the file
# already imports the list and has the API off (the list and its directory get their owner
# and mode again), "changed" after an edit, "unknown" for any other shape (nothing
# written). Never prints a hash.
_ACCESS_FIX_EDITOR_TEMPLATE = r'''
import grp, os, re, shutil, sys
INLINE = re.compile(r"^(    basic_auth @protected \{\n)        (__USER__) (__HASH__)\n(    \}\n)", re.M)
BLOCK, LIST_DIR, ADMIN_ONLY = __BLOCK__, __LIST_DIR__, __ADMIN_ONLY__
caddyfile, listfile = sys.argv[1], sys.argv[2]
gid = grp.getgrnam("swarm").gr_gid
def own(path, mode):
    os.chown(path, 0, gid)
    os.chmod(path, mode)
def admin_off(text):
    # the admin API off: already there, or in the file's global block, or a block of its own
    if re.search(r"^\s*admin off\s*$", text, re.M):
        return text
    lines = text.splitlines(True)
    first = next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")), None)
    if first is not None and lines[first].strip() == "{":
        return "".join(lines[:first + 1] + ["    admin off\n"] + lines[first + 1:])
    if text.count(":80 {\n") == 1:
        return text.replace(":80 {\n", ADMIN_ONLY + ":80 {\n", 1)
    return text
def write(path, out, suffix):
    tmp = path + suffix
    with open(tmp, "w") as f:
        f.write(out)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
with open(caddyfile) as f:
    text = f.read()
auth = len(re.findall(r"^\s*basic_?auth\b", text, re.M))
if text.count(BLOCK) == 1 and auth == 1:
    if not os.path.isfile(listfile):
        sys.exit("ERROR: the Caddyfile imports the access list but the list is missing")
    own(LIST_DIR, 0o750)
    own(listfile, 0o640)
    out = admin_off(text)
    if out == text:
        print("on")
        sys.exit(0)
    write(caddyfile, out, ".access-new")
    print("changed")
    sys.exit(0)
found = list(INLINE.finditer(text))
if len(found) != 1 or auth != 1:
    print("unknown")
    sys.exit(0)
m = found[0]
if not os.path.isdir(LIST_DIR):
    os.mkdir(LIST_DIR, 0o700)
own(LIST_DIR, 0o750)
tmp = listfile + ".fix-new"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(m.group(2) + " " + m.group(3) + "\n")
own(tmp, 0o640)
os.replace(tmp, listfile)
write(caddyfile, admin_off(text[:m.start()] + BLOCK + text[m.end():]), ".access-new")
print("changed")
'''


def access_fix_editor_source() -> str:
    """The Python the update step runs on an existing Hive (see above)."""
    return (_ACCESS_FIX_EDITOR_TEMPLATE
            .replace("__USER__", ACCESS_USER_PATTERN)
            .replace("__HASH__", ACCESS_HASH_PATTERN)
            .replace("__BLOCK__", repr(_ACCESS_BLOCK))
            .replace("__LIST_DIR__", repr(ACCESS_LIST_DIR))
            .replace("__ADMIN_ONLY__", repr(ADMIN_OFF_GLOBAL)))


# Shared by the update step and the apply script: the code Caddy answers on *path* once it
# is up, and the checks both run after a restart: /health is answered, and a protected path
# without a key is refused (401, or 403 from the origin guard first).
_ACCESS_CHECK_SH = r"""access_code() {  # $1 path: the code Caddy answers once it is up
  local code="000" i
  for i in $(seq 1 15); do
    code=$(curl --path-as-is -s -o /dev/null -w '%{http_code}' "http://127.0.0.1$1" || true)
    if [ "$code" != "000" ]; then
      break
    fi
    sleep 1
  done
  echo "$code"
}
access_checks() {  # prints nothing when Caddy answers as it should, else the reason
  local code
  code=$(access_code /health)
  case "$code" in
    000|401|403) echo "/health answered $code"; return ;;
  esac
  code=$(access_code /api/access-check)
  case "$code" in
    401|403) ;;
    *) echo "a protected path answered $code without a key" ;;
  esac
}"""


# The update script's step after the /health step, before the install is touched: an
# existing Hive moves to the access list (editor above), its hive-auth.env becomes
# root-only, and the step prints "Access list: on" when the Hive has the list (the Host
# then gives it its own key: hive/access.migrate) or "Access list: off". A failure after the
# edit restores the Caddyfile and the list as they were and stops the update.
_ACCESS_FIX_SH_TEMPLATE = r"""# ── Caddy: one access list for both auth layers (hive/user_data.py) ──
ACCESS_CADDYFILE="/etc/caddy/Caddyfile"
ACCESS_LIST="__LIST__"
ACCESS_FIX_PY=$(echo '__EDITOR_B64__' | base64 -d)
__CHECK_SH__
access_fix_fail() {
  cp -p "$ACCESS_CADDYFILE.access-bak" "$ACCESS_CADDYFILE"
  rm -f "$ACCESS_CADDYFILE.access-bak"
  if [ "$ACCESS_HAD_LIST" = 1 ]; then
    cp -p "$ACCESS_LIST.access-bak" "$ACCESS_LIST"
  else
    rm -f "$ACCESS_LIST"
  fi
  rm -f "$ACCESS_LIST.access-bak"
  if [ "$1" = restarted ]; then
    systemctl restart caddy || true
  fi
  echo "ERROR: Caddy access list: $2; previous Caddyfile restored, Hive not updated"
  exit 1
}
if [ -f /etc/swarmai/hive-auth.env ]; then
  chown root:root /etc/swarmai/hive-auth.env
  chmod 600 /etc/swarmai/hive-auth.env
fi
if [ -f "$ACCESS_CADDYFILE" ]; then
  cp -p "$ACCESS_CADDYFILE" "$ACCESS_CADDYFILE.access-bak"
  ACCESS_HAD_LIST=0
  if [ -f "$ACCESS_LIST" ]; then
    ACCESS_HAD_LIST=1
    cp -p "$ACCESS_LIST" "$ACCESS_LIST.access-bak"
  fi
  ACCESS_FIX=$(python3 -c "$ACCESS_FIX_PY" "$ACCESS_CADDYFILE" "$ACCESS_LIST") || access_fix_fail no "the edit failed"
  case "$ACCESS_FIX" in
    on)
      rm -f "$ACCESS_CADDYFILE.access-bak" "$ACCESS_LIST.access-bak"
      echo "Access list: on"
      ;;
    changed)
      caddy validate --config "$ACCESS_CADDYFILE" --adapter caddyfile >/dev/null 2>&1 \
        || access_fix_fail no "caddy validate rejected the edited file"
      systemctl restart caddy || access_fix_fail restarted "caddy did not restart"
      ACCESS_WHY=$(access_checks)
      if [ -n "$ACCESS_WHY" ]; then
        access_fix_fail restarted "$ACCESS_WHY"
      fi
      rm -f "$ACCESS_CADDYFILE.access-bak" "$ACCESS_LIST.access-bak"
      echo "Access list: on"
      ;;
    unknown)
      rm -f "$ACCESS_CADDYFILE.access-bak" "$ACCESS_LIST.access-bak"
      echo "WARN: Caddy access list: the basic_auth block of $ACCESS_CADDYFILE is not one this update can move to the access list; left as it is"
      echo "Access list: off"
      ;;
    *)
      access_fix_fail no "unexpected editor result"
      ;;
  esac
else
  echo "WARN: no $ACCESS_CADDYFILE; the access list step was skipped"
  echo "Access list: off"
fi"""


def access_list_fix_sh() -> str:
    """The update-script step that moves an existing Hive to the access list."""
    import base64
    editor_b64 = base64.b64encode(access_fix_editor_source().encode()).decode()
    return (_ACCESS_FIX_SH_TEMPLATE
            .replace("__EDITOR_B64__", editor_b64)
            .replace("__LIST__", ACCESS_LIST_PATH)
            .replace("__CHECK_SH__", _ACCESS_CHECK_SH))


# Runs on the instance (python3 3.9, stdlib only, as root) inside access_apply_sh(). argv:
# <new list> <host user> <hive-auth.env>. Exits non-zero, naming the reason and never a
# hash, unless every line is "<user> <bcrypt hash>", no user repeats and the Host's user is
# listed; then writes the Host's line as hive-auth.env's HIVE_USER / HIVE_PASS_HASH (the
# fallback a package older than the access list reads).
_ACCESS_APPLY_CHECK_TEMPLATE = r'''
import os, re, sys
LINE = re.compile(__LINE__)
new, host, envfile = sys.argv[1], sys.argv[2], sys.argv[3]
users = {}
with open(new) as f:
    for line in f.read().splitlines():
        m = LINE.match(line)
        if m is None or m.group(1) in users:
            sys.exit("ERROR: the access list has a malformed or repeated line; nothing changed")
        users[m.group(1)] = m.group(2)
if host not in users:
    sys.exit("ERROR: the access list has no line for the Host's key; nothing changed")
kept = []
if os.path.isfile(envfile):
    with open(envfile) as f:
        kept = [l for l in f.read().splitlines()
                if not l.startswith(("HIVE_USER=", "HIVE_PASS_HASH="))]
tmp = envfile + ".apply-new"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(kept + ["HIVE_USER=" + host, "HIVE_PASS_HASH=" + users[host]]) + "\n")
os.chown(tmp, 0, 0)
os.replace(tmp, envfile)
print(len(users))
'''


def access_apply_sh(entries: list[tuple[str, str]], host_user: str) -> str:
    """The SSM script the Host runs to make *entries* this Hive's access list. It refuses
    a Hive that has no list yet (update it first: access_list_fix_sh), checks the list,
    replaces it atomically, validates and restarts Caddy, and checks it answers as it
    should; any failure after the replace puts the previous list back. The backend picks
    the new list up on its next request (no restart). Its output never holds a hash."""
    import base64
    list_b64 = base64.b64encode(access_list_text(entries).encode()).decode()
    if not re.fullmatch(ACCESS_USER_PATTERN, host_user):
        raise ValueError("invalid host user")
    check_b64 = base64.b64encode(
        _ACCESS_APPLY_CHECK_TEMPLATE.replace("__LINE__", repr(ACCESS_LINE_PATTERN)).encode()).decode()
    return f"""#!/bin/bash
set -euo pipefail
ACCESS_LIST="{ACCESS_LIST_PATH}"
CADDYFILE="/etc/caddy/Caddyfile"
ENVFILE="/etc/swarmai/hive-auth.env"
{_ACCESS_CHECK_SH}
if [ ! -f "$ACCESS_LIST" ] || ! grep -qxF '{ACCESS_IMPORT_LINE.rstrip(chr(10))}' "$CADDYFILE"; then
  echo "ERROR: this Hive has no access list yet; update it first"
  exit 3
fi
NEW=$(mktemp "{ACCESS_LIST_DIR}/.hive-users.XXXXXX")
ENV_BAK=$(mktemp /etc/swarmai/.hive-auth.env.XXXXXX)
cleanup() {{ rm -f "$NEW" "$ACCESS_LIST.apply-bak" "$ENV_BAK"; }}
trap cleanup EXIT
echo '{list_b64}' | base64 -d > "$NEW"
cp -p "$ACCESS_LIST" "$ACCESS_LIST.apply-bak"
if [ -f "$ENVFILE" ]; then cp -p "$ENVFILE" "$ENV_BAK"; fi
apply_fail() {{
  cp -p "$ACCESS_LIST.apply-bak" "$ACCESS_LIST"
  if [ -s "$ENV_BAK" ]; then cp -p "$ENV_BAK" "$ENVFILE"; fi
  if [ "$1" = restarted ]; then systemctl restart caddy || true; fi
  echo "ERROR: access list: $2; the previous list was put back"
  exit 1
}}
COUNT=$(python3 -c "$(echo '{check_b64}' | base64 -d)" "$NEW" '{host_user}' "$ENVFILE")
chown root:swarm "$NEW"
chmod 640 "$NEW"
mv -f "$NEW" "$ACCESS_LIST"
caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1 \\
  || apply_fail no "caddy validate rejected the list"
systemctl restart caddy || apply_fail restarted "caddy did not restart"
WHY=$(access_checks)
if [ -n "$WHY" ]; then apply_fail restarted "$WHY"; fi
echo "Access list applied: $COUNT keys"
"""


# ── /tmp on disk ──
# Amazon Linux mounts /tmp as tmpfs (half the RAM, no swap on a Hive), so the scratch
# agents leave there (clones, builds, test dirs) takes memory from the agent sessions.
# Masking tmp.mount puts /tmp on the root disk from the next boot. The directory under the
# mount is made world-writable first (through a bind view of /, which shows the root disk
# without its mounts), and the mask is set only when that worked: a masked mount over a
# directory nobody can write would break every program that needs /tmp.
# On disk /tmp has no size cap and a reboot no longer empties it, so systemd-tmpfiles
# removes what nobody touched for 3 days instead of 10: /etc/tmpfiles.d/tmp.conf is the
# system's own tmp.conf with only that age changed (its other lines, such as the ones
# that keep services' private tmp directories, stay). On a Hive still on tmpfs this also
# frees the RAM sooner. Rendered into the first-boot script (render_user_data) and the
# update script (provisioner.update); every failure only warns, the next update retries.
TMP_ON_DISK_SH = r"""# ── /tmp on disk ──
if root_view=$(mktemp -d /run/swarm-rootfs.XXXXXX); then
    if mount --bind / "$root_view" && chmod 1777 "$root_view/tmp"; then
        if systemctl mask tmp.mount >/dev/null; then
            echo "/tmp: tmp.mount masked, /tmp is on disk from the next boot"
        else
            echo "WARN: could not mask tmp.mount; /tmp stays on tmpfs"
        fi
    else
        echo "WARN: could not make the on-disk /tmp writable; /tmp stays on tmpfs"
    fi
    umount "$root_view" || umount -l "$root_view" || echo "WARN: $root_view is still mounted"
    rmdir "$root_view" 2>/dev/null || true
else
    echo "WARN: no scratch directory under /run; /tmp stays on tmpfs"
fi
TMPFILES_SRC=/usr/lib/tmpfiles.d/tmp.conf
TMPFILES_DST=/etc/tmpfiles.d/tmp.conf
if [ -f "$TMPFILES_SRC" ] && grep -qE '^q /tmp .* 10d$' "$TMPFILES_SRC"; then
    if mkdir -p /etc/tmpfiles.d \
            && sed -E 's#^(q /tmp .*) 10d$#\1 3d#' "$TMPFILES_SRC" > "$TMPFILES_DST.new" \
            && mv "$TMPFILES_DST.new" "$TMPFILES_DST"; then
        echo "/tmp: files untouched for 3 days are removed"
    else
        rm -f "$TMPFILES_DST.new"
        echo "WARN: could not shorten the /tmp cleanup; it stays at 10 days"
    fi
else
    echo "WARN: $TMPFILES_SRC has no 10-day /tmp line; its /tmp cleanup is left as it is"
fi"""


# ── Hive package check ──
# Shared by first boot (render_user_data) and the update script (provisioner.update).
# checksums.txt sits beside the package in the bucket: hive/release.sh uploads it with the
# package, provisioner._sync_release_to_s3 writes it with a package from GitHub Releases.
# Each line is `<sha256>  <file>` (sha256sum's format); the package is accepted only when
# its line exists and its hash is the package's. Every failure prints one ERROR line (to
# stdout and stderr: an update's card shows the stdout one) and returns 1, which stops a
# `set -e` script before the package is unpacked.
PACKAGE_CHECK_SH = r"""verify_hive_package() {  # <package file> <bucket> <version> <region>
    local file="$1" bucket="$2" version="$3" region="$4"
    local name="swarmai-hive-v$version-linux-arm64.tar.gz" sums want got
    if ! sums=$(aws s3 cp "s3://$bucket/v$version/checksums.txt" - --region "$region"); then
        echo "ERROR: the Hive package v$version cannot be checked: s3://$bucket/v$version/checksums.txt could not be read; nothing was installed" | tee /dev/stderr
        return 1
    fi
    want=$(printf '%s\n' "$sums" | awk -v f="$name" '$2 == f || $2 == "*" f { print tolower($1); exit }')
    if ! printf '%s' "$want" | grep -Eq '^[0-9a-f]{64}$'; then
        echo "ERROR: the Hive package v$version cannot be checked: checksums.txt has no sha256 line for $name; nothing was installed" | tee /dev/stderr
        return 1
    fi
    got=$(sha256sum "$file" | cut -d' ' -f1)
    if [ "$got" != "$want" ]; then
        echo "ERROR: the Hive package v$version does not match its checksum (sha256 $got, checksums.txt says $want); nothing was installed" | tee /dev/stderr
        return 1
    fi
    echo "Hive package v$version matches checksums.txt (sha256 $got)"
}"""


# ── Caddy: a pinned release ──
# Caddy is the Hive's only public entry and its auth layer. It is the GitHub release below,
# and the archive is installed only when its SHA-512 is the one that release's
# caddy_<version>_checksums.txt lists for caddy_<version>_linux_arm64.tar.gz (copied here,
# so the value cannot change with the release asset). To move to another release: take its
# checksums file's line for linux_arm64.tar.gz, and check the rendered Caddyfile with that
# binary (`caddy validate --adapter caddyfile`). v2.11.9 validates it.
CADDY_VERSION = "2.11.9"
CADDY_ARM64_SHA512 = ("9e5e76d7888542924de2c25353ae87c5f77e2a22c5b1198633cae13688922282"  # pragma: allowlist secret  (a public release checksum, not a credential)
                      "e05383d142599205c88d8d80eee5e967d291214d7abce4835329a191b20dd6e0")  # pragma: allowlist secret  (a public release checksum, not a credential)
CADDY_INSTALL_SH = r"""install_caddy() {  # <destination>
    local dest="$1" work
    work=$(mktemp -d)
    local archive="$work/caddy.tar.gz"
    local url="https://github.com/caddyserver/caddy/releases/download/v@CADDY_VERSION@/caddy_@CADDY_VERSION@_linux_arm64.tar.gz"
    if ! curl -fsSL "$url" -o "$archive"; then
        echo "ERROR: Caddy v@CADDY_VERSION@ could not be downloaded from $url; nothing was installed" | tee /dev/stderr
        rm -rf "$work"; return 1
    fi
    local got
    got=$(sha512sum "$archive" | cut -d' ' -f1)
    if [ "$got" != "@CADDY_SHA512@" ]; then
        echo "ERROR: Caddy v@CADDY_VERSION@ does not match its release checksum (sha512 $got); nothing was installed" | tee /dev/stderr
        rm -rf "$work"; return 1
    fi
    if ! tar xzf "$archive" -C "$work" caddy || ! install -m 755 "$work/caddy" "$dest"; then
        echo "ERROR: Caddy v@CADDY_VERSION@ could not be unpacked to $dest" | tee /dev/stderr
        rm -rf "$work"; return 1
    fi
    rm -rf "$work"
    echo "Caddy v@CADDY_VERSION@ installed (sha512 checked)"
}""".replace("@CADDY_VERSION@", CADDY_VERSION).replace("@CADDY_SHA512@", CADDY_ARM64_SHA512)

# The SHA-512 of the caddy binary inside that archive (measured from the archive whose
# SHA-512 is CADDY_ARM64_SHA512). An update compares /usr/bin/caddy with it: equal means the
# Hive already runs the pinned build, and nothing is downloaded. Moving to another release
# changes all three values together.
CADDY_ARM64_BINARY_SHA512 = ("e2c95f514e1a62364f7068b6a77035e1cec71687f9017c215cc3cdb20a6191a1"  # pragma: allowlist secret  (a public release checksum, not a credential)
                             "734055285ca4117fa6b2ed169dc5efae2fd4e98c98dc16b430fdd9a6b0f9d536")  # pragma: allowlist secret  (a public release checksum, not a credential)

# The update script's step (provisioner.update) that gives an existing Hive the Caddy binary
# first boot installs. Hives made before the pin run a binary first boot downloaded from
# caddyserver.com unchecked; first boot alone never reaches them. When /usr/bin/caddy is not
# the pinned build: install_caddy (the same function and checksum first boot uses) into a
# scratch directory, check the unpacked binary is the pinned build, `caddy validate` the
# Hive's Caddyfile with it, rename it over /usr/bin/caddy (a running binary cannot be
# written in place), restart Caddy and check both ways: /health is answered, and a request
# without credentials gets 401/403 (a path no Caddyfile layout exempts). A failure before
# the rename leaves the old binary untouched; one after it renames the old binary back and
# restarts Caddy with it. Either way the update stops with the reason before /opt/swarmai is
# touched, like the Caddyfile /health step above it.
_CADDY_BINARY_SH_TEMPLATE = r"""# ── Caddy binary: the pinned release first boot installs (hive/user_data.py) ──
CADDY_BIN="/usr/bin/caddy"
CADDY_CONFIG="/etc/caddy/Caddyfile"
__INSTALL__
caddy_bin_fail() {  # $1 swapped|no, $2 the reason
  if [ "$1" = swapped ]; then
    if [ -f "$CADDY_BIN.update-bak" ]; then
      mv -f "$CADDY_BIN.update-bak" "$CADDY_BIN"
    else
      rm -f "$CADDY_BIN"
    fi
    systemctl restart caddy || true
  fi
  rm -rf "$CADDY_WORK" "$CADDY_BIN.update-new" "$CADDY_BIN.update-bak"
  echo "ERROR: Caddy binary update: $2; previous Caddy kept, Hive not updated"
  exit 1
}
caddy_bin_code() {
  # $1 path: the code Caddy answers once it is up
  local code="000" i
  for i in $(seq 1 15); do
    code=$(curl --path-as-is -s -o /dev/null -w '%{http_code}' "http://127.0.0.1$1" || true)
    if [ "$code" != "000" ]; then
      break
    fi
    sleep 1
  done
  echo "$code"
}
CADDY_HAVE=$(sha512sum "$CADDY_BIN" 2>/dev/null | cut -d' ' -f1 || true)
if [ "$CADDY_HAVE" = "@CADDY_BINARY_SHA512@" ]; then
  echo "Caddy binary: already the pinned v@CADDY_VERSION@"
else
  CADDY_WORK=$(mktemp -d)
  install_caddy "$CADDY_WORK/caddy" \
    || caddy_bin_fail no "the pinned Caddy v@CADDY_VERSION@ could not be downloaded, did not match its checksum or could not be unpacked (line above)"
  CADDY_GOT=$(sha512sum "$CADDY_WORK/caddy" | cut -d' ' -f1)
  if [ "$CADDY_GOT" != "@CADDY_BINARY_SHA512@" ]; then
    caddy_bin_fail no "the unpacked binary is not the pinned v@CADDY_VERSION@ build (sha512 $CADDY_GOT)"
  fi
  "$CADDY_WORK/caddy" validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
    || caddy_bin_fail no "Caddy v@CADDY_VERSION@ rejects $CADDY_CONFIG"
  if [ -e "$CADDY_BIN" ]; then
    cp -p "$CADDY_BIN" "$CADDY_BIN.update-bak" || caddy_bin_fail no "the current binary could not be backed up"
  fi
  install -m 755 "$CADDY_WORK/caddy" "$CADDY_BIN.update-new" \
    || caddy_bin_fail no "the new binary could not be written beside $CADDY_BIN"
  mv -f "$CADDY_BIN.update-new" "$CADDY_BIN" || caddy_bin_fail swapped "the new binary could not be moved to $CADDY_BIN"
  systemctl restart caddy || caddy_bin_fail swapped "caddy did not restart with v@CADDY_VERSION@"
  CODE=$(caddy_bin_code /health)
  case "$CODE" in
    000|401|403) caddy_bin_fail swapped "/health answered $CODE with v@CADDY_VERSION@" ;;
  esac
  CODE=$(caddy_bin_code /swarmai-caddy-check)
  case "$CODE" in
    401|403) ;;
    *) caddy_bin_fail swapped "a request without credentials answered $CODE with v@CADDY_VERSION@, not 401/403" ;;
  esac
  rm -rf "$CADDY_WORK" "$CADDY_BIN.update-bak"
  echo "Caddy binary: now the pinned v@CADDY_VERSION@ (sha512 checked)"
fi"""


def caddy_binary_update_sh() -> str:
    """The update-script step that gives an existing Hive the pinned Caddy binary."""
    return (_CADDY_BINARY_SH_TEMPLATE.replace("__INSTALL__", CADDY_INSTALL_SH)
            .replace("@CADDY_VERSION@", CADDY_VERSION)
            .replace("@CADDY_BINARY_SHA512@", CADDY_ARM64_BINARY_SHA512))


# How long the first boot waits for the backend's /health before it tags the instance
# (provisioner.FIRST_BOOT_WAIT_S counts it).
BACKEND_HEALTH_WAIT_S = 120

# The code-intel grammars, put on disk by the first boot and by every update, never by the
# backend itself: the library's own download holds the GIL and stops every thread of the
# server (core/code_intel/grammars.py). Outside the prebaked-stripped segments, so a
# prebaked AMI runs it too. A no-op when they are there for the installed library version;
# a failure (offline) only warns, and code intel takes its regex path. The deadline bounds a
# stalled download well inside the time the provisioner waits for an update script
# (provisioner.UPDATE_WAIT_POLLS x UPDATE_WAIT_INTERVAL_S); a release-bump rebuild from AWS
# takes seconds.
TREE_SITTER_SETUP_DEADLINE_S = 120
TREE_SITTER_GRAMMARS_SH = r"""# Code-intel grammars (the backend never downloads one itself)
(cd "${INSTALL_DIR:-/opt/swarmai}/backend" && sudo -H -u "${SWARM_USER:-swarm}" .venv/bin/python scripts/bundle_tree_sitter_grammars.py generated/tree_sitter_grammars --deadline @DEADLINE@) \
  || echo "WARN: tree-sitter grammars not bundled; code intel uses its regex path for the missing languages"
""".replace("@DEADLINE@", str(TREE_SITTER_SETUP_DEADLINE_S))


# The host side of Feature Map replay (hive/replay-host.sh): the packages the pinned Chromium
# headless shell needs on AL2023, the replay user, and the firewall rule that keeps that user
# off the instance metadata service. Run as root by the first boot and by every update, outside
# the prebaked-stripped segments (a prebaked AMI runs it too; a no-op when it is all in place).
# The deadline bounds a stalled dnf (the script gives dnf REPLAY_HOST_DNF_DEADLINE_S of it); a
# failure only warns, and Feature Map replay then reports its walks not_run (the engine checks
# the boundary itself before it runs anything, core/feature_map/replay_user.py). This deadline
# and the grammar step's fit the provisioner's wait (provisioner.UPDATE_WAIT_POLLS).
# Root runs a root-owned copy of the script (REPLAY_HOST_BIN), never the file in the install
# tree, which the Hive user owns and could change before root runs it: an update takes it from
# the checked package itself (REPLAY_HOST_TARBALL), the first boot from the tree it unpacked
# from that package moments before (the backend has never run yet).
REPLAY_HOST_DEADLINE_S = 240
REPLAY_HOST_DNF_DEADLINE_S = 200
REPLAY_HOST_BIN = "/usr/local/libexec/swarmai/replay-host.sh"
REPLAY_HOST_SH = r"""# Feature Map replay host: browser packages, the replay user, no metadata service for it
REPLAY_HOST_BIN="${REPLAY_HOST_BIN:-@BIN@}"
{ mkdir -p -m 0755 "${REPLAY_HOST_BIN%/*}" \
  && if [ -n "${REPLAY_HOST_TARBALL:-}" ]; then
       (umask 077; tar xzf "$REPLAY_HOST_TARBALL" --wildcards --no-wildcards-match-slash -O '*/hive/replay-host.sh' > "$REPLAY_HOST_BIN.new") \
         && [ -s "$REPLAY_HOST_BIN.new" ]
     else
       install -m 0700 "${INSTALL_DIR:-/opt/swarmai}/hive/replay-host.sh" "$REPLAY_HOST_BIN.new"
     fi \
  && chmod 0700 "$REPLAY_HOST_BIN.new" && mv -f "$REPLAY_HOST_BIN.new" "$REPLAY_HOST_BIN" \
  && timeout @DEADLINE@ env SWARM_USER="${SWARM_USER:-swarm}" REPLAY_HOST_DNF_DEADLINE_S=@DNF@ bash "$REPLAY_HOST_BIN"; } \
  || echo "WARN: Feature Map replay host step incomplete; replay reports its walks not_run until the next update"
""".replace("@BIN@", REPLAY_HOST_BIN).replace("@DEADLINE@", str(REPLAY_HOST_DEADLINE_S)).replace("@DNF@", str(REPLAY_HOST_DNF_DEADLINE_S))


# ── Prebaked-AMI segments (must match _USER_DATA_TEMPLATE byte-for-byte) ──
# When a pre-baked custom AMI is used, the heavy on-machine install steps are
# already固化 in the image, so render_user_data(prebaked=True) strips these two
# exact segments and replaces them with a skip-echo. They are defined as exact
# substrings of the template so a drift between here and the template FAILS LOUD
# (render raises) instead of silently rendering a script that still runs dnf/pip.
#
# ⚠️ The prebaked AMI MUST pre-install: python3.12 + a fully-populated
# /opt/swarmai/backend/.venv (all pyproject deps) + gcc/make/python3.12-devel.
# gcc/make are required NOT for first boot but because update() (provisioner.py)
# re-runs `pip install -e .` on tarball updates, which may compile C extensions.
_DNF_SEGMENT = """# ── 1. System packages ──
echo "[1/9] Installing system packages..."
dnf install -y python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make 2>&1 | tail -5"""

_DNF_SEGMENT_PREBAKED = """# ── 1. System packages ── (SKIPPED — pre-baked AMI already has them)
echo "[1/9] System packages pre-installed in AMI — skipping dnf." """

_VENV_SEGMENT = """# ── 4. Python venv + deps ──
echo "[4/9] Setting up Python environment..."
cd "$INSTALL_DIR/backend"
sudo -u "$SWARM_USER" python3.12 -m venv .venv
sudo -u "$SWARM_USER" .venv/bin/pip install -q --upgrade pip
sudo -u "$SWARM_USER" .venv/bin/pip install -q -e . 2>&1 | tail -3"""

_VENV_SEGMENT_PREBAKED = """# ── 4. Python venv + deps ── (SKIPPED — pre-baked AMI already has .venv)
echo "[4/9] Python venv + deps pre-installed in AMI — skipping."
# The AMI ships a complete /opt/swarmai/backend/.venv; step [3/9] overwrites the
# source tree (editable install points into it), so no pip step is needed here."""


def render_user_data(
    s3_bucket: str,
    version: str,
    auth_user: str,
    auth_hash: str,
    region: str,
    prebaked: bool = False,
    origin_secret: str | None = None,
    access: list[tuple[str, str]] | None = None,
) -> str:
    """Render the EC2 user-data bash script with parameters.

    Uses string.Template-style substitution. All variables are
    injected into the script — no hardcoded values.

    Defense-in-depth: all values are validated/sanitized before substitution.
    The Caddyfile heredoc uses single-quotes ('CADDY') so shell doesn't
    expand variables — only Python Template substitution runs.

    prebaked: when True (a pre-baked custom AMI is used), strip the [1/9] dnf
    install and [4/9] venv+pip segments — the AMI already固化 them, so the
    instance only pulls the package (step 3) + starts services (steps 5-9),
    cutting build time from minutes to ~10s. prebaked=False (default) renders
    byte-for-byte the original full-install script — zero regression.

    origin_secret: this Hive's secret, which its CloudFront distribution sends as
    X-Swarm-Origin (provisioner.deploy generates it before the launch). The Caddyfile
    then answers 403 to every request but /health whose header does not equal it, before
    basic auth, and its access log drops the header. None renders the Caddyfile without
    the guard (the shape Hives had before it), never a guard nobody can pass.

    auth_user / auth_hash: the Host's own key, the first line of the access list and the
    backend's fallback pair. access: the other keys of the list, ``(user, bcrypt hash)``
    (a redeploy keeps the person keys the Host has for this Hive: hive/access.py).
    """
    from string import Template

    # Validate inputs structurally to prevent shell/config injection.
    # Block characters that are dangerous in bash or Caddyfile contexts:
    # spaces, quotes, backticks, semicolons, pipes, newlines, etc.
    _SAFE = re.compile(r'^[a-zA-Z0-9._\-/]+$')
    # bcrypt hashes contain $ (e.g. $2b$14$...) — safe because Caddyfile
    # heredoc uses single-quote delimiter which prevents shell expansion
    _SAFE_HASH = re.compile(r'^[a-zA-Z0-9._\-/$]+$')
    for name, value, max_len, pattern in [
        ("s3_bucket", s3_bucket, 63, _SAFE),
        ("version", version, 32, _SAFE),
        ("auth_user", auth_user, 64, _SAFE),
        ("auth_hash", auth_hash, 256, _SAFE_HASH),
        ("region", region, 25, _SAFE),
    ]:
        if not value or len(value) > max_len:
            raise ValueError(f"Invalid {name}: length must be 1-{max_len}")
        if not pattern.match(value):
            raise ValueError(
                f"Invalid {name}: contains unsafe characters"
            )
    if origin_secret is not None and not valid_origin_secret(origin_secret):
        # The value is never echoed: it is a credential.
        raise ValueError(f"Invalid origin_secret: must match {ORIGIN_SECRET_PATTERN}")

    # base64-encode the bcrypt hash for the backend env-file write (avoids shell/systemd
    # `$` expansion of $2b$.. — same footgun the Caddyfile heredoc dodges via single-quotes).
    import base64
    auth_hash_b64 = base64.b64encode(auth_hash.encode()).decode()
    access_b64 = base64.b64encode(
        access_list_text([(auth_user, auth_hash)] + list(access or [])).encode()).decode()

    tmpl = Template(_USER_DATA_TEMPLATE)
    values = dict(
        s3_bucket=s3_bucket,
        version=version,
        auth_user=auth_user,
        auth_hash=auth_hash,
        auth_hash_b64=auth_hash_b64,
        access_b64=access_b64,
        region=region,
        tmp_on_disk=TMP_ON_DISK_SH,
        package_check=PACKAGE_CHECK_SH,
        caddy_install=CADDY_INSTALL_SH,
        tree_sitter_grammars=TREE_SITTER_GRAMMARS_SH,
        replay_host=REPLAY_HOST_SH,
        backend_health_wait_s=str(BACKEND_HEALTH_WAIT_S),
    )
    if origin_secret is not None:
        values["origin_secret"] = origin_secret
    result = tmpl.safe_substitute(**values)
    if origin_secret is None:
        result = _GUARD_MARK_RE.sub("", result).replace(":80 {\n", ADMIN_OFF_GLOBAL + ":80 {\n", 1)

    # Prebaked AMI: strip the heavy on-machine install segments. Fail LOUD if a
    # segment isn't found verbatim (template drifted) — never silently render a
    # script that still runs dnf/pip on a prebaked image, and never leave the
    # instance with a broken half-edited script.
    if prebaked:
        for old, new in ((_DNF_SEGMENT, _DNF_SEGMENT_PREBAKED),
                         (_VENV_SEGMENT, _VENV_SEGMENT_PREBAKED)):
            if old not in result:
                raise ValueError(
                    "prebaked render failed: expected install segment not found "
                    "in template (segment constant drifted from _USER_DATA_TEMPLATE)"
                )
            result = result.replace(old, new)

    # M13: Catch misspelled template variables — safe_substitute leaves them as-is.
    # Only check for our known template vars (shell vars like ${i}, ${HASH} are expected).
    _TEMPLATE_VARS = {"s3_bucket", "version", "auth_user", "auth_hash", "auth_hash_b64",
                      "access_b64", "region",
                      "origin_secret", "tmp_on_disk", "package_check", "caddy_install",
                      "tree_sitter_grammars", "replay_host", "backend_health_wait_s"}
    for var in _TEMPLATE_VARS:
        if f"${{{var}}}" in result:
            raise ValueError(f"Unresolved template variable: ${{{var}}}")
    return result
