"""User-data script renderer for Hive EC2 instances.

Generates a parameterized bash script that EC2 runs on first boot.
Downloads the hive package from S3, installs dependencies, configures
Caddy with basic auth, starts services, and tags the instance ready.
"""

import re
import secrets

_USER_DATA_TEMPLATE = r"""#!/bin/bash
# SwarmAI Hive — EC2 user-data (runs once on first boot)
# Fully automated: pull from S3 -> install -> start -> tag ready
set -euo pipefail
exec > /var/log/hive-setup.log 2>&1

echo "=== SwarmAI Hive Setup — $(date) ==="

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
curl -sL "https://caddyserver.com/api/download?os=linux&arch=arm64" -o /tmp/caddy
if file /tmp/caddy | grep -q "ELF.*executable"; then
    mv /tmp/caddy /usr/bin/caddy && chmod +x /usr/bin/caddy
else
    echo "ERROR: Caddy download failed integrity check" >&2
    # H2: Abort immediately — continuing without Caddy = instance looks healthy but 401s everything
    exit 1
fi
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

    # ORIGIN-GUARD-RULE-BEGIN
    # /health is exempt only as the literal request path: the path matcher cleans dot
    # segments, but the backend gets the raw path, so /x/../health must not pass.
    @foreign_origin {
        not expression `{http.request.uri.path} == "/health"`
        not header X-Swarm-Origin ${origin_secret}
    }
    respond @foreign_origin 403
    request_header -X-Swarm-Origin
    # ORIGIN-GUARD-RULE-END
    @protected not path /health
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
# X-Swarm-Origin (OriginCustomHeaders); the Caddyfile answers 403 to every other request
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
# (block name, the line it sits next to in the Caddyfile, before/after that line)
_GUARD_ANCHORS = (
    ("GLOBAL", ":80 {\n", "before"),
    ("RULE", "    @protected not path /health\n", "before"),
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
                     "Redeploy the Hive, or add the guard by hand")
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


def valid_origin_secret(value) -> bool:
    return isinstance(value, str) and bool(_ORIGIN_SECRET_RE.fullmatch(value))


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
                      "origin_secret"}
    for var in _TEMPLATE_VARS:
        if f"${{{var}}}" in result:
            raise ValueError(f"Unresolved template variable: ${{{var}}}")
    return result
