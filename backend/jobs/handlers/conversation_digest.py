"""Conversation → DDD daily digest (capability C, DoD3, run_e346b8ed).

The TRIGGER layer of capability C. Runs as a scheduled daily job (chosen over
session-wrap for stability, §9-D2): deterministic cron, idempotent over a fixed
time window, and it sees the whole day's conversation arc — exactly what the
conservative extractor needs to tell a settled decision from a mid-discussion.

DORMANT BY DEFAULT (C037 discipline, §7): this job does NOTHING until a channel
is explicitly opted-in via `Services/swarm-jobs/conversation-digest.yaml`
(`enabled_channels: [...]`). No channel enabled → the handler returns a `skipped`
result WITHOUT reading any conversation, calling the LLM, or writing anything.
This is how the whole capability ships inert: XG turns it on per channel.

Flow when a channel IS opted-in:
  channel_messages (authorized rows) → conversation_extract.extract_candidates
  → ddd_cultivation.cultivate_from_conversation → (never auto-applies; every
  candidate escalates to the human-gate at routers/cultivation.py).
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("swarm.jobs.conversation_digest")

_CONFIG_REL = "Services/swarm-jobs/conversation-digest.yaml"


def _load_enabled_channels(workspace: Path) -> list[dict]:
    """Read the opt-in config. Returns [] (dormant) if the file is absent,
    unreadable, malformed, or lists no channels — fail-closed to DORMANT."""
    cfg_path = workspace / _CONFIG_REL
    if not cfg_path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001 — any config error → dormant, never run
        logger.warning("conversation_digest: config unreadable (%s) — dormant", exc)
        return []
    if not isinstance(data, dict):
        # A non-mapping root (list/scalar — a natural yaml mistake) → dormant,
        # never crash. .get() below would raise AttributeError otherwise.
        logger.warning("conversation_digest: config root is %s not a mapping — dormant",
                       type(data).__name__)
        return []
    channels = data.get("enabled_channels") or []
    if not isinstance(channels, list):
        return []
    # Each entry must name a channel_session_id + project to be actionable.
    return [
        c for c in channels
        if isinstance(c, dict) and c.get("channel_session_id") and c.get("project")
    ]


async def run_conversation_digest(
    *,
    workspace: Optional[Path] = None,
    db=None,
    extract_fn=None,
    cultivate_fn=None,
) -> dict:
    """Run one daily digest pass over opted-in channels.

    All collaborators are injectable for testing; production defaults wire the
    real DB, extractor, and cultivation sink.

    Returns a summary dict. status="skipped" when dormant (no opted-in channel)
    — the EXPECTED steady state until XG enables a channel.
    """
    if workspace is None:
        from config import get_app_data_dir
        workspace = get_app_data_dir() / "SwarmWS"

    enabled = _load_enabled_channels(workspace)
    if not enabled:
        logger.info("conversation_digest: no opted-in channels — dormant, nothing to do")
        return {"status": "skipped", "reason": "dormant (no enabled_channels)",
                "channels": 0, "escalated": 0}

    if db is None:
        from database import get_database
        db = get_database()
    if extract_fn is None:
        from core.conversation_extract import extract_candidates as extract_fn
    if cultivate_fn is None:
        from core.ddd_cultivation import cultivate_from_conversation as cultivate_fn

    total_escalated = 0
    processed = 0
    for ch in enabled:
        sid = ch["channel_session_id"]
        project = ch["project"]
        # Per-channel isolation: the WHOLE read→extract→cultivate chain for one
        # channel is wrapped, so any single channel's failure (db error, LLM
        # error that escaped extract's own guard, write error) logs + continues
        # to the next channel rather than aborting the digest.
        try:
            rows = await db.channel_messages.list_by_session(sid)
            candidates = extract_fn(rows, project)  # tier re-assert + owner-ratify inside
            if candidates:
                project_dir = workspace / "Projects" / project
                result = cultivate_fn(candidates, sid, project, project_dir)
                total_escalated += result.get("escalated", 0)
            processed += 1
        except Exception as exc:  # noqa: BLE001 — isolate per-channel failures
            logger.warning("conversation_digest: channel %s failed (%s) — skipping", sid, exc)
            continue

    logger.info("conversation_digest: %d channel(s), %d proposal(s) escalated",
                processed, total_escalated)
    return {"status": "success", "channels": processed, "escalated": total_escalated}
