"""What this instance is for (design docs/design/2026-10-29-hive-run-record-and-worker-role.md §3.1).

A Hive (``SWARMAI_MODE=hive``) runs in one of two roles, read from ``SWARMAI_ROLE``:

- ``worker`` (the default, and what any value this module does not know means): it
  executes dispatched work and writes no cognition of its own. Its CDF only extracts
  (the S2/S7 session jobs and S3 at a pipeline landing put lessons in its inbox, for
  the host); the judge and writer, DailyActivity, the cognition-writing session hooks
  and system jobs do not run.
- ``reference``: a full SwarmAI, as before (a public showcase instance).

Any other mode is a person's own instance (``personal``). Launchers set both variables
before the process starts; every place that depends on the role calls ``is_worker()``
here, none reads the environment itself.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

WORKER = "worker"
REFERENCE = "reference"
PERSONAL = "personal"


def role() -> str:
    if os.environ.get("SWARMAI_MODE") != "hive":
        return PERSONAL
    value = (os.environ.get("SWARMAI_ROLE") or "").strip().lower()
    if value == REFERENCE:
        return REFERENCE
    if value and value != WORKER:
        # an unknown role is the one that writes nothing of its own
        logger.warning("SWARMAI_ROLE=%r is not a role; running as a worker", value[:40])
    return WORKER


def is_worker() -> bool:
    return role() == WORKER
