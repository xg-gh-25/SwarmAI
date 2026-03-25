"""
Swarm Job System — Path Configuration

All filesystem paths used by the job system. Resolves from SWARMWS root.
Runtime data (state, config, logs) lives in SwarmWS/Services/swarm-jobs/.
System job definitions live in code (system_jobs.py), not YAML.
"""

from __future__ import annotations

from pathlib import Path

# Root workspace
SWARMWS = Path.home() / ".swarm-ai" / "SwarmWS"

# Job system data directory (workspace-level)
JOBS_DATA_DIR = SWARMWS / "Services" / "swarm-jobs"
STATE_FILE = JOBS_DATA_DIR / "state.json"
CONFIG_FILE = JOBS_DATA_DIR / "config.yaml"
USER_JOBS_FILE = JOBS_DATA_DIR / "user-jobs.yaml"
LOG_DIR = JOBS_DATA_DIR / "logs"

# SwarmAI data
DB_PATH = Path.home() / ".swarm-ai" / "data.db"
CONTEXT_DIR = SWARMWS / ".context"
DAILY_DIR = SWARMWS / "Knowledge" / "DailyActivity"
SIGNALS_DIR = SWARMWS / "Knowledge" / "Signals"
PROJECTS_DIR = SWARMWS / "Projects"
JOB_RESULTS_DIR = SWARMWS / "Knowledge" / "JobResults"
JOB_RESULTS_JSONL = JOB_RESULTS_DIR / ".job-results.jsonl"
SIGNAL_DIGEST_FILE = SWARMWS / "Services" / "signals" / "signal_digest.json"

# MCP config
MCPS_DIR = SWARMWS / ".claude" / "mcps"
