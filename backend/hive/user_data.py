"""User-data script renderer for Hive EC2 instances.

Generates a parameterized bash script that EC2 runs on first boot.
Downloads the hive package from S3, installs dependencies, configures
Caddy with basic auth, starts services, and tags the instance ready.
"""

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
    TAG_STATUS="error"
fi
mkdir -p /etc/caddy /var/log/caddy

# ── 6. Configure Caddy (HTTP, CloudFront terminates TLS) ──
echo "[6/9] Configuring Caddy with basic auth..."
cat > /etc/caddy/Caddyfile << 'CADDY'
:80 {
    basicauth * {
        ${auth_user} ${auth_hash}
    }
    handle /api/chat/stream {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
        }
    }
    handle /api/chat/answer-question {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
        }
    }
    handle /api/chat/cmd-permission-continue {
        reverse_proxy 127.0.0.1:18321 {
            flush_interval -1
        }
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:18321
    }
    handle /health {
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
        -Server
    }
}
CADDY

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


def render_user_data(
    s3_bucket: str,
    version: str,
    auth_user: str,
    auth_hash: str,
    region: str,
) -> str:
    """Render the EC2 user-data bash script with parameters.

    Uses string.Template-style substitution. All variables are
    injected into the script — no hardcoded values.

    Defense-in-depth: all values are validated/sanitized before substitution.
    The Caddyfile heredoc uses single-quotes ('CADDY') so shell doesn't
    expand variables — only Python Template substitution runs.
    """
    import re
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

    tmpl = Template(_USER_DATA_TEMPLATE)
    return tmpl.safe_substitute(
        s3_bucket=s3_bucket,
        version=version,
        auth_user=auth_user,
        auth_hash=auth_hash,
        region=region,
    )
