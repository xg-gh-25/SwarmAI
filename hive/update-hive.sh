#!/bin/bash
# SwarmAI Hive — update script (run from local Mac)
#
# Builds frontend locally, syncs to EC2, restarts services.
# No git pull / npm ci / vite build on EC2 — deploy pre-built artifacts.
#
# Usage: ./hive/update-hive.sh [IP_ADDRESS]
#        ./hive/update-hive.sh all          # update all hives

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."
SSH_KEY="${HIVE_SSH_KEY:-${HOME}/.ssh/swarmai-hive.pem}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# Known Hive instances (override via env: HIVE_XG=1.2.3.4 ./update-hive.sh)
HIVE_XG="${HIVE_XG:-100.50.177.246}"
HIVE_WB="${HIVE_WB:-34.193.172.137}"
ALL_HIVES=("${HIVE_XG}" "${HIVE_WB}")

# ---------------------------------------------------------------------------
# Step 1: Build frontend locally (one build, guaranteed consistent)
# ---------------------------------------------------------------------------

echo "[hive-update] Building frontend locally..."
cd "${PROJECT_ROOT}/desktop"
npm run build 2>&1 | tail -2
echo "[hive-update] Frontend built at desktop/dist/"

# ---------------------------------------------------------------------------
# Step 2: Deploy to target(s)
# ---------------------------------------------------------------------------

deploy_to() {
    local IP=$1
    echo ""
    echo "[hive-update] Deploying to ${IP}..."

    # Sync frontend (pre-built, no build on EC2)
    rsync -az --delete \
        -e "ssh -i ${SSH_KEY} ${SSH_OPTS}" \
        "${PROJECT_ROOT}/desktop/dist/" \
        "ec2-user@${IP}:/tmp/hive-dist/"

    # Sync backend source (Python doesn't need build step)
    rsync -az --delete \
        --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='tests/' --exclude='.pytest_cache' \
        -e "ssh -i ${SSH_KEY} ${SSH_OPTS}" \
        "${PROJECT_ROOT}/backend/" \
        "ec2-user@${IP}:/tmp/hive-backend/"

    # Sync hive config
    rsync -az \
        -e "ssh -i ${SSH_KEY} ${SSH_OPTS}" \
        "${PROJECT_ROOT}/hive/" \
        "ec2-user@${IP}:/tmp/hive-config/"

    # Apply on EC2
    ssh -i "${SSH_KEY}" ${SSH_OPTS} "ec2-user@${IP}" << 'REMOTE'
# Swap in new files
sudo rsync -a /tmp/hive-dist/ /opt/swarmai/desktop/dist/
sudo rsync -a --exclude='.venv' /tmp/hive-backend/ /opt/swarmai/backend/
sudo cp /tmp/hive-config/swarmai-hive.sh /opt/swarmai/hive/swarmai-hive.sh
sudo chmod +x /opt/swarmai/hive/swarmai-hive.sh
sudo chown -R swarm:swarm /opt/swarmai/desktop/dist /opt/swarmai/backend /opt/swarmai/hive

# Sync pip dependencies (catches new imports from pyproject.toml)
sudo -u swarm /opt/swarmai/backend/.venv/bin/pip install -q -e '/opt/swarmai/backend[all]' 2>&1 | tail -1

# Restart
sudo systemctl restart swarmai-hive
sleep 8

# Verify
STATUS=$(curl -sf http://localhost:18321/health | python3.12 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['version'])" 2>/dev/null)
echo "Health: ${STATUS}"
rm -rf /tmp/hive-dist /tmp/hive-backend /tmp/hive-config
REMOTE

    echo "[hive-update] ${IP} done"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TARGET="${1:-all}"

if [ "$TARGET" = "all" ]; then
    for IP in "${ALL_HIVES[@]}"; do
        deploy_to "$IP"
    done
elif [ "$TARGET" = "xg" ]; then
    deploy_to "$HIVE_XG"
elif [ "$TARGET" = "wb" ]; then
    deploy_to "$HIVE_WB"
else
    # Assume it's an IP
    deploy_to "$TARGET"
fi

echo ""
echo "[hive-update] All deployments complete."
