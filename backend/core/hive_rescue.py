"""What an AgentCore Hive's supervisor runs after its backend died, before anything starts
again: ``python -m core.hive_rescue`` in the backend venv, with the backend's environment
(hive/agentcore/supervisor.py ``rescue``; design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.5).

On an EC2 Hive systemd starts a dead daemon again and its boot closes the turn the death left
open (core/hive_boot). An AgentCore microVM has no systemd, and its session ends at the
platform's lifetime, so the supervisor runs that same close here, in a process of its own,
while no backend runs (no turn writer can race it):

1. every record entry the backend sealed but did not get into S3 is written there
   (core/hive_agentcore ``export_now``: the same export, from the cursor kept on disk);
2. ``hive_boot.close_open_turns`` with ``hive_boot.SealRouter``: the open turn gets its
   ``daemon_stopped`` outcome with each declared repository's facts, its commits bundled
   from the branch head (``SessionRouter._task_repo_facts``), and is sealed into the run
   record; a turn this process did not see start is the only kind it touches;
3. the entries that seal added are written to S3 the same way.

It prints one JSON line, ``{"ok", "uploaded", "sealed", "pending"}`` (``ok``: nothing is
left unwritten), and exits 0 when ``ok``. Only in an AgentCore Hive's environment
(``hive_agentcore.active``); anything else answers ``{"ok": false, "why"}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: hang guard for the export lane's queued writes (bundles up to 100 MiB each); the
#: supervisor's own RESCUE_TIMEOUT_S bounds the whole rescue
FLUSH_S = 600.0


def _upload(workspace: Path) -> tuple[int, Optional[str]]:
    from core import hive_agentcore
    try:
        return hive_agentcore.export_now(workspace), None
    except Exception as exc:  # noqa: BLE001 — what is not written stays pending, reported
        logger.warning("hive_rescue: uploading the record failed (%s)", type(exc).__name__)
        return 0, type(exc).__name__


async def rescue(workspace: Optional[Path] = None) -> dict[str, Any]:
    """Upload, close and seal the open turn, upload again (module doc). Never raises."""
    from core import hive_agentcore, hive_boot
    if not hive_agentcore.active() or hive_agentcore.identity() is None:
        return {"ok": False, "why": "not an AgentCore Hive"}
    ws = Path(workspace or hive_agentcore._workspace())
    uploaded, err = await asyncio.to_thread(_upload, ws)
    sealed = 0
    try:
        sealed = await hive_boot.close_open_turns(hive_boot.SealRouter())
    except Exception as exc:  # noqa: BLE001 — close_open_turns never raises; a guard
        logger.warning("hive_rescue: closing the open turns failed (%s)", type(exc).__name__)
    # the seal queued its entries on the export lane: let it run, then write what is left here
    await asyncio.to_thread(hive_agentcore.flush, FLUSH_S)
    more, err2 = await asyncio.to_thread(_upload, ws)
    pending = await asyncio.to_thread(hive_agentcore.export_pending, ws)
    out = {"ok": not pending, "uploaded": uploaded + more, "sealed": sealed, "pending": pending}
    if err or err2:
        out["error"] = err2 or err
    return out


async def _open_database() -> bool:
    """The backend's database as its lifespan opens an existing one (migrations only, all
    idempotent); False, and nothing created, when there is none yet: a backend that died
    before its first boot finished wrote no rows to seal, and an empty file here would make
    the restarted backend skip creating its schema (main.py, a returning user's store)."""
    from database import get_database, initialize_database
    path = getattr(get_database(), "db_path", None)
    if not path or not Path(path).is_file():
        return False
    await initialize_database(skip_schema=True)
    return True


async def _main() -> dict[str, Any]:
    from core import hive_agentcore
    if not hive_agentcore.active() or hive_agentcore.identity() is None:
        return {"ok": False, "why": "not an AgentCore Hive"}   # no database is opened
    try:
        if not await _open_database():
            logger.info("hive_rescue: no database yet: nothing to seal")
    except Exception as exc:  # noqa: BLE001 — upload what can be: the seal reads no rows then
        logger.warning("hive_rescue: the database did not open (%s)", type(exc).__name__)
    return await rescue()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s hive_rescue %(levelname)s %(message)s")
    got = asyncio.run(_main())
    print(json.dumps(got), flush=True)
    return 0 if got.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
