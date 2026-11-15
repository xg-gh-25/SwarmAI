"""User-data script renderer for Hive EC2 instances.

Generates a parameterized bash script that EC2 runs on first boot.
Downloads the hive package from S3 and checks it against the checksums.txt beside it,
installs dependencies, installs a pinned Caddy release (checksum checked) and configures
it with basic auth, starts services, and tags the instance ready.
"""

import re
import secrets

_USER_DATA_TEMPLATE = r"""#!/bin/bash
# SwarmAI Hive — EC2 user-data (runs once on first boot)
# Fully automated: pull from S3 -> install -> start -> tag ready
set -euo pipefail
exec > /var/log/hive-setup.log 2>&1

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

# ── 5. Install Caddy ──
echo "[5/9] Installing Caddy..."
# A pinned release from GitHub, checked against that release's checksum (CADDY_VERSION).
# H2: a failure aborts the setup: continuing without Caddy = instance looks healthy but
# 401s everything.
${caddy_install}
install_caddy /usr/bin/caddy
mkdir -p /etc/caddy /var/log/caddy

# ── 6. Configure Caddy (HTTP, CloudFront terminates TLS) ──
echo "[6/9] Configuring Caddy with basic auth..."
cat > /etc/caddy/Caddyfile << 'CADDY'
# ORIGIN-GUARD-GLOBAL-BEGIN
# Only this Hive's CloudFront distribution sends X-Swarm-Origin with this Hive's secret.
# Caddy runs basic_auth before respond by default; this order makes the 403 below come first.
# The default logger (Caddy's error log, in the journal) writes request headers too.
{
    order respond before basic_auth
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
        ${auth_user} ${auth_hash}
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

# ── 6b. Shared Hive auth credential (app-layer defense-in-depth) ──
# The SAME credential Caddy validates (basic_auth @protected above) is also given to
# the backend so it can enforce app-layer auth (middleware/hive_auth.py) as the INNER
# layer of defense-in-depth. Written to a root-only env file that the backend systemd
# unit reads via EnvironmentFile=. The bcrypt hash is base64-encoded here to avoid ANY
# shell/systemd `$` expansion of the $2b$.. hash, then decoded when the file is written.
# ROTATION NOTE: provisioner.reset_password() MUST update BOTH this file AND the
# Caddyfile inline hash, or the two layers drift (R27 two-consumer credential).
echo "[6b/9] Writing shared hive-auth credential for the backend..."
# Pre-create the dir 0700-owned BEFORE the secret file exists (install -d sets the
# dir mode atomically) so /etc/swarmai is never world-traversable during first boot.
install -m 700 -d /etc/swarmai
HIVE_HASH_DECODED=$(echo '${auth_hash_b64}' | base64 -d)
# Pre-create the file 0600-owned BEFORE any secret is written — `install -m 600`
# is atomic re: mode, so there is never a window where the credential exists at a
# looser permission (belt to umask 077's suspenders). Then fill it in place.
install -m 600 /dev/null /etc/swarmai/hive-auth.env
umask 077
cat > /etc/swarmai/hive-auth.env << HIVEAUTHENV
HIVE_USER=${auth_user}
HIVE_PASS_HASH=$HIVE_HASH_DECODED
HIVEAUTHENV
chown swarm:swarm /etc/swarmai/hive-auth.env
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
for i in $(seq 1 120); do
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


def generate_password(word_count: int = 4) -> str:
    """Generate a memorable passphrase for Caddy basic auth.

    Produces a dash-separated passphrase like 'tiger-cloud-seven-lamp'.
    4 words from a 256-word list = ~32 bits of entropy, sufficient for
    bcrypt-hashed credentials behind CloudFront + SG restrictions.
    Much easier to type than random chars like 'hdsgEcX2#SyXyOHs'.
    """
    # Compact word list: common, short, unambiguous English words.
    # 256 words = 8 bits per word, 4 words = 32 bits.
    _WORDS = [
        "ace", "air", "ant", "ape", "arc", "arm", "art", "ash",
        "axe", "bag", "ban", "bar", "bat", "bay", "bed", "bee",
        "big", "bit", "bow", "box", "bud", "bug", "bus", "cab",
        "cam", "cap", "car", "cat", "cob", "cod", "cog", "cop",
        "cow", "cry", "cub", "cup", "cut", "dam", "day", "den",
        "dew", "dig", "dim", "dip", "dog", "dot", "dry", "dug",
        "dye", "ear", "eel", "egg", "elk", "elm", "emu", "end",
        "era", "eve", "eye", "fan", "far", "fat", "fax", "fed",
        "few", "fig", "fin", "fir", "fit", "fix", "fly", "fog",
        "fox", "fun", "fur", "gag", "gap", "gas", "gem", "gin",
        "got", "gum", "gun", "gut", "gym", "ham", "hat", "hay",
        "hen", "hex", "hid", "him", "hip", "hit", "hog", "hop",
        "hot", "how", "hub", "hue", "hug", "hum", "hut", "ice",
        "imp", "ink", "inn", "ion", "ire", "ivy", "jab", "jam",
        "jar", "jaw", "jay", "jet", "jig", "job", "jog", "joy",
        "jug", "key", "kid", "kin", "kit", "lab", "lag", "lap",
        "law", "lay", "leg", "let", "lid", "lip", "lit", "log",
        "lot", "low", "lug", "map", "mat", "may", "men", "met",
        "mid", "mix", "mob", "mod", "mop", "mud", "mug", "nap",
        "net", "new", "nib", "nil", "nip", "nod", "nor", "not",
        "now", "nut", "oak", "oar", "oat", "odd", "oil", "old",
        "one", "opt", "orb", "ore", "our", "out", "owe", "owl",
        "own", "pad", "pan", "paw", "pay", "pea", "peg", "pen",
        "pet", "pie", "pig", "pin", "pit", "pod", "pop", "pot",
        "pry", "pub", "pug", "pun", "pup", "put", "rag", "ram",
        "ran", "rat", "raw", "ray", "red", "rib", "rid", "rig",
        "rim", "rip", "rod", "rot", "row", "rub", "rug", "rum",
        "run", "rut", "rye", "sad", "sag", "sap", "sat", "saw",
        "say", "sea", "set", "shy", "sin", "sip", "sit", "six",
        "ski", "sky", "sly", "sob", "sod", "son", "soy", "spy",
        "sum", "sun", "tab", "tag", "tan", "tap", "tar", "tax",
        "tea", "ten", "the", "tie", "tin", "tip", "toe", "top",
    ]
    return "-".join(secrets.choice(_WORDS) for _ in range(word_count))


def caddy_hash_password(password: str) -> str:
    """Generate a bcrypt hash for Caddy basicauth.

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
if mode == "apply":
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
elif mode != "remove":
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

    tmpl = Template(_USER_DATA_TEMPLATE)
    values = dict(
        s3_bucket=s3_bucket,
        version=version,
        auth_user=auth_user,
        auth_hash=auth_hash,
        auth_hash_b64=auth_hash_b64,
        region=region,
        tmp_on_disk=TMP_ON_DISK_SH,
        package_check=PACKAGE_CHECK_SH,
        caddy_install=CADDY_INSTALL_SH,
    )
    if origin_secret is not None:
        values["origin_secret"] = origin_secret
    result = tmpl.safe_substitute(**values)
    if origin_secret is None:
        result = _GUARD_MARK_RE.sub("", result)

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
    _TEMPLATE_VARS = {"s3_bucket", "version", "auth_user", "auth_hash", "auth_hash_b64", "region",
                      "origin_secret", "tmp_on_disk", "package_check", "caddy_install"}
    for var in _TEMPLATE_VARS:
        if f"${{{var}}}" in result:
            raise ValueError(f"Unresolved template variable: ${{{var}}}")
    return result
