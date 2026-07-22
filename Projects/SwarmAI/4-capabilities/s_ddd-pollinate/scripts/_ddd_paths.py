"""Portable path resolution for the DDD-native pollinate engine.

Replaces SwarmAI's hardcoded ``~/.swarm-ai`` locations. A DDD shipped to Kiro /
Claude Code / an AIM package has no ``~/.swarm-ai`` — state lives under the DDD's
own workspace, resolved from ``$SWARM_WORKSPACE`` (fallback: cwd), mirroring the
DDD-native pipeline engine's convention.

State layout (all under ``<workspace>/.artifacts/pollinate/``):
  - accounts.yaml          publish account identity (optional)
  - publish-log.jsonl      append-only publish log
  - backlog.json           topic backlog
  - studio/                video-render scratch
  - output/{slug}/         generated content packages
"""
from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    """Resolve the workspace root: $SWARM_WORKSPACE, else cwd. Never ~/.swarm-ai."""
    return Path(os.environ.get("SWARM_WORKSPACE") or os.getcwd())


def pollinate_dir() -> Path:
    """`<workspace>/.artifacts/pollinate/` — the DDD-local pollinate state root."""
    d = workspace_root() / ".artifacts" / "pollinate"
    return d


def accounts_path() -> Path:
    """Publish account identity file (optional; publish degrades if absent)."""
    return pollinate_dir() / "accounts.yaml"


def publish_log_path() -> Path:
    """Append-only publish log."""
    return pollinate_dir() / "publish-log.jsonl"


def backlog_path() -> Path:
    """Topic backlog store."""
    return pollinate_dir() / "backlog.json"


def studio_dir() -> Path:
    """Video-render scratch dir."""
    return pollinate_dir() / "studio"


def output_dir() -> Path:
    """Generated content packages root (`output/{slug}/`)."""
    return pollinate_dir() / "output"
