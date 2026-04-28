#!/bin/bash
# SwarmAI Hive — EC2 one-shot setup script
#
# Installs SwarmAI on a fresh Amazon Linux 2023 (ARM64) EC2 instance.
# Idempotent — safe to re-run.
#
# Usage: sudo bash setup.sh
#
# Prerequisites:
#   - EC2 instance with IAM role (bedrock:InvokeModel*)
#   - Security group: 443 + 80 inbound, 22 (SSH) inbound
#   - Elastic IP attached (for DNS)
#   - DNS A record pointing to Elastic IP

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SWARM_USER="swarm"
INSTALL_DIR="/opt/swarmai"
REPO_URL="${SWARMAI_REPO_URL:-https://github.com/xg-gh-25/SwarmAI.git}"
REPO_BRANCH="${SWARMAI_REPO_BRANCH:-main}"
# Pin to a known-good commit for integrity (update on each release)
REPO_COMMIT="${SWARMAI_REPO_COMMIT:-}"  # e.g. "fa492d1" — if set, checkout after clone

echo "============================================="
echo "  SwarmAI Hive Setup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================="

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------

echo "[1/10] Installing system packages..."
dnf update -y -q
dnf install -y -q \
    python3.12 python3.12-pip python3.12-devel \
    nodejs20 npm \
    git gcc make \
    2>/dev/null || true

# Verify
python3.12 --version
node --version
npm --version
git --version

# ---------------------------------------------------------------------------
# 2. Create swarm user (if not exists)
# ---------------------------------------------------------------------------

echo "[2/10] Creating swarm user..."
if ! id "${SWARM_USER}" &>/dev/null; then
    useradd -m -s /bin/bash "${SWARM_USER}"
    echo "[hive] Created user: ${SWARM_USER}"
else
    echo "[hive] User ${SWARM_USER} already exists"
fi

# Ensure .swarm-ai directory exists
sudo -u "${SWARM_USER}" mkdir -p "/home/${SWARM_USER}/.swarm-ai"

# ---------------------------------------------------------------------------
# 3. Clone or update SwarmAI repo
# ---------------------------------------------------------------------------

echo "[3/10] Setting up SwarmAI codebase..."
if [ -d "${INSTALL_DIR}/.git" ]; then
    echo "[hive] Repo exists, pulling latest..."
    cd "${INSTALL_DIR}"
    sudo -u "${SWARM_USER}" git pull --ff-only origin "${REPO_BRANCH}" || true
else
    echo "[hive] Cloning repo..."
    git clone --branch "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
    chown -R "${SWARM_USER}:${SWARM_USER}" "${INSTALL_DIR}"
fi
# Pin to specific commit if SWARMAI_REPO_COMMIT is set (integrity verification)
if [ -n "${REPO_COMMIT}" ]; then
    echo "[hive] Checking out pinned commit: ${REPO_COMMIT}"
    cd "${INSTALL_DIR}"
    sudo -u "${SWARM_USER}" git checkout "${REPO_COMMIT}"
fi

# ---------------------------------------------------------------------------
# 4. Python venv + dependencies
# ---------------------------------------------------------------------------

echo "[4/10] Setting up Python virtual environment..."
cd "${INSTALL_DIR}/backend"
if [ ! -d ".venv" ]; then
    sudo -u "${SWARM_USER}" python3.12 -m venv .venv
fi
sudo -u "${SWARM_USER}" .venv/bin/pip install --quiet --upgrade pip
sudo -u "${SWARM_USER}" .venv/bin/pip install --quiet -e ".[all]" 2>/dev/null \
    || sudo -u "${SWARM_USER}" .venv/bin/pip install --quiet -r requirements.txt 2>/dev/null \
    || echo "[hive] WARNING: pip install may have had errors, check logs"

# ---------------------------------------------------------------------------
# 5. Build React frontend
# ---------------------------------------------------------------------------

echo "[5/10] Building React frontend..."
cd "${INSTALL_DIR}/desktop"
sudo -u "${SWARM_USER}" npm ci --quiet 2>/dev/null || sudo -u "${SWARM_USER}" npm install --quiet
sudo -u "${SWARM_USER}" VITE_API_URL="" npm run build

echo "[hive] Frontend built at: ${INSTALL_DIR}/desktop/dist/"

# ---------------------------------------------------------------------------
# 6. Install Caddy
# ---------------------------------------------------------------------------

echo "[6/10] Installing Caddy..."
if ! command -v caddy &>/dev/null; then
    dnf install -y 'dnf-command(copr)' 2>/dev/null || true
    dnf copr enable @caddy/caddy -y 2>/dev/null || true
    dnf install -y caddy 2>/dev/null || {
        # Fallback: direct binary install with verification
        echo "[hive] dnf install failed, installing Caddy binary directly..."
        curl -sL "https://caddyserver.com/api/download?os=linux&arch=arm64" -o /tmp/caddy
        # Verify it's a real binary (ELF header check)
        if file /tmp/caddy | grep -q "ELF.*executable"; then
            mv /tmp/caddy /usr/bin/caddy
            chmod +x /usr/bin/caddy
        else
            echo "[hive] ERROR: Downloaded Caddy binary failed integrity check" >&2
            rm -f /tmp/caddy
            exit 1
        fi
    }
fi
caddy version

# Create log directory
mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy 2>/dev/null || chown root:root /var/log/caddy

# ---------------------------------------------------------------------------
# 7. Configure Caddy
# ---------------------------------------------------------------------------

echo "[7/10] Configuring Caddy..."
cp "${INSTALL_DIR}/hive/Caddyfile" /etc/caddy/Caddyfile 2>/dev/null \
    || mkdir -p /etc/caddy && cp "${INSTALL_DIR}/hive/Caddyfile" /etc/caddy/Caddyfile

echo ""
echo "  !! IMPORTANT: Edit /etc/caddy/Caddyfile to set:"
echo "     - Your domain (replace hive.example.com)"
echo "     - Basic auth password hash"
echo "     - Run: caddy hash-password --plaintext 'your-password'"
echo ""

# ---------------------------------------------------------------------------
# 8. Make hive entry script executable
# ---------------------------------------------------------------------------

echo "[8/10] Setting up Hive entry point..."
chmod +x "${INSTALL_DIR}/hive/swarmai-hive.sh"

# ---------------------------------------------------------------------------
# 9. Install systemd services
# ---------------------------------------------------------------------------

echo "[9/10] Installing systemd services..."
cp "${INSTALL_DIR}/hive/swarmai-hive.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable swarmai-hive
systemctl enable caddy

# ---------------------------------------------------------------------------
# 9b. Configure log rotation for backend.log
# ---------------------------------------------------------------------------

echo "[9b/10] Configuring log rotation..."
sudo tee /etc/logrotate.d/swarmai > /dev/null << 'LOGROTATE'
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

# ---------------------------------------------------------------------------
# 10. Initialize SwarmWS workspace
# ---------------------------------------------------------------------------

echo "[10/10] Initializing SwarmWS workspace..."
sudo -u "${SWARM_USER}" bash -c "
    cd ${INSTALL_DIR}/backend
    .venv/bin/python -c '
from core.swarm_workspace_manager import SwarmWorkspaceManager
SwarmWorkspaceManager().ensure_workspace()
print(\"[hive] SwarmWS initialized\")
' 2>/dev/null || echo '[hive] WARNING: workspace init may need manual review'
"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "============================================="
echo "  SwarmAI Hive Setup Complete!"
echo "============================================="
echo ""
echo "  Next steps:"
echo "  1. Edit /etc/caddy/Caddyfile (domain + auth)"
echo "  2. Start services:"
echo "     sudo systemctl start swarmai-hive"
echo "     sudo systemctl start caddy"
echo "  3. Check status:"
echo "     sudo systemctl status swarmai-hive"
echo "     curl -s http://localhost:18321/health"
echo "  4. Configure Slack (via API after startup):"
echo "     curl -X POST https://your-domain/api/channels/..."
echo ""
echo "  Logs:"
echo "     journalctl -u swarmai-hive -f"
echo "     journalctl -u caddy -f"
echo ""
