"""
Swarm Job System — Path Configuration

All filesystem paths used by the job system. Resolves from SWARMWS root.
Runtime data (state, config, logs) lives in SwarmWS/Services/swarm-jobs/.
System job definitions live in code (system_jobs.py), not YAML.
"""

from __future__ import annotations


from config import get_app_data_dir

# Root paths (derived from single source of truth: config.get_app_data_dir())
APP_DATA_DIR = get_app_data_dir()
SWARMWS = APP_DATA_DIR / "SwarmWS"

# Job system data directory (workspace-level)
JOBS_DATA_DIR = SWARMWS / "Services" / "swarm-jobs"
STATE_FILE = JOBS_DATA_DIR / "state.json"
CONFIG_FILE = JOBS_DATA_DIR / "config.yaml"
USER_JOBS_FILE = JOBS_DATA_DIR / "user-jobs.yaml"
LOG_DIR = JOBS_DATA_DIR / "logs"

# SwarmAI data
DB_PATH = APP_DATA_DIR / "data.db"
CONTEXT_DIR = SWARMWS / ".context"
DAILY_DIR = SWARMWS / "Knowledge" / "DailyActivity"
SIGNALS_DIR = SWARMWS / "Knowledge" / "Signals"
PROJECTS_DIR = SWARMWS / "Projects"
JOB_RESULTS_DIR = SWARMWS / "Knowledge" / "JobResults"
JOB_RESULTS_JSONL = JOB_RESULTS_DIR / ".job-results.jsonl"
SIGNAL_DIGEST_FILE = SWARMWS / "Services" / "signals" / "signal_digest.json"

# MCP config
MCPS_DIR = SWARMWS / ".claude" / "mcps"

# Estimation learner (EMA-based job duration prediction)
ESTIMATION_LEARNER_FILE = APP_DATA_DIR / "estimation_learner.json"

# Daemon binary location
DAEMON_DIR = APP_DATA_DIR / "daemon"

# Port discovery file
PORT_FILE = APP_DATA_DIR / "backend.port"

# Runtime state (ephemeral files: checkpoint, corrections log)
# Distinct from CONTEXT_DIR (SwarmWS/.context/) which holds git-tracked context files.
STATE_DIR = APP_DATA_DIR / "state"


def _migrate_legacy_state_dir() -> None:
    """One-time migration: move files from ~/.swarm-ai/.context/ to ~/.swarm-ai/state/.

    The old `.context/` name collided with SwarmWS/.context/ (workspace context files),
    causing confusion. Runtime state (checkpoint, corrections) now lives in `state/`.
    Safe to call multiple times — only moves files that exist at the old location.
    """
    old_dir = APP_DATA_DIR / ".context"
    if not old_dir.is_dir():
        return
    # Only migrate the two known runtime files — anything else is unexpected
    _FILES_TO_MIGRATE = ("session_checkpoint.json", "corrections.jsonl")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for fname in _FILES_TO_MIGRATE:
        old_path = old_dir / fname
        new_path = STATE_DIR / fname
        if old_path.exists() and not new_path.exists():
            try:
                old_path.rename(new_path)
            except (FileNotFoundError, OSError):
                pass  # race: file deleted between check and move — benign
    # Remove old dir only if empty (don't delete unexpected files)
    try:
        old_dir.rmdir()  # only succeeds if empty
    except OSError:
        pass  # not empty — leave it


# Called explicitly in main.py lifespan, not at import time.
# This avoids surprising filesystem mutations during test imports.
