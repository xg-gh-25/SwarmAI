"""The usage view's API (design docs/design/2026-10-29-usage-accounting.md §5, §11c).

- ``GET /api/usage/report?window=7d|30d|all`` — the view's figures (core/usage_report),
  with the titles the ledger holds only an id for: a session's title, a pipeline run's
  requirement and report, a Hive dispatch's title; and the monthly budget.
- ``PUT /api/usage/budget`` — set the monthly budget (``defaults.max_monthly_spend_usd``
  in the scheduler's config.yaml). The budget is held to the whole platform's spend
  this month; when it is reached the scheduler starts no new agent job until the 1st of
  next month. Nothing else stops: chat, pipeline runs and work already running go on.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])

_RUN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_TITLE_MAX = 160
MAX_BUDGET_USD = 10_000_000


def _session_titles(conn, ids: list[str]) -> dict[str, str]:
    marks = ",".join("?" for _ in ids)
    try:
        return {r[0]: r[1] for r in conn.execute(f"SELECT id, title FROM sessions WHERE id IN ({marks})", ids) if r[1]}
    except Exception:  # noqa: BLE001 — a title is a nicety; the row keeps its id
        return {}


def _run_info(workspace: Optional[str], run_id: str) -> Optional[dict[str, Any]]:
    """A pipeline run's requirement (its title) and its report's workspace path."""
    if not workspace or not _RUN_ID.fullmatch(run_id):
        return None
    root = Path(workspace)
    for run_json in root.glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        req = data.get("requirement") if isinstance(data, Mapping) else None
        title = str(req).strip().splitlines()[0][:_TITLE_MAX] if req else None
        report = run_json.with_name("REPORT.md")
        return {"title": title, "report_path": report.relative_to(root).as_posix() if report.is_file() else None}
    return None


def _dispatch_titles() -> dict[str, str]:
    try:
        from core import swarm_out
        return {str(r["dispatch_id"]): str(r.get("title") or "")[:_TITLE_MAX]
                for r in swarm_out._read_log() if r.get("dispatch_id")}
    except Exception:  # noqa: BLE001
        return {}


def _budget() -> Optional[float]:
    try:
        from jobs.config_io import read_config
        from jobs.scheduler import load_defaults
        return float(load_defaults(read_config()).max_monthly_spend_usd)
    except Exception:  # noqa: BLE001
        return None


def _build(window: str) -> dict[str, Any]:
    from core import swarm_workspace_manager, usage_report
    try:
        workspace = swarm_workspace_manager.get_workspace_path()
    except Exception:  # noqa: BLE001
        workspace = None
    conn = usage_report.connect()
    try:
        return usage_report.report(
            window, conn,
            session_titles=lambda ids: _session_titles(conn, ids),
            run_info=lambda rid: _run_info(workspace, rid),
            dispatch_titles=_dispatch_titles(),
            budget_usd=_budget())
    finally:
        conn.close()


@router.get("/report")
async def usage_report_view(window: str = "7d") -> dict[str, Any]:
    from core import usage_report
    if window not in usage_report.WINDOWS:
        raise HTTPException(status_code=400, detail="window must be 7d, 30d or all")
    try:
        return await asyncio.to_thread(_build, window)
    except Exception as e:  # noqa: BLE001
        logger.warning("usage report failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="usage ledger not readable") from None


class BudgetBody(BaseModel):
    monthly_usd: float


def _set_budget(usd: float) -> None:
    from jobs.config_io import mutate_config

    def put(config: dict) -> None:
        # an unreadable or corrupt config.yaml reads as {}: writing it back would drop
        # every feed and default, so refuse (mutate_config leaves the file untouched)
        if not config:
            raise ValueError("config.yaml unreadable")
        defaults = config.get("defaults")
        if not isinstance(defaults, dict):
            defaults = config["defaults"] = {}
        defaults["max_monthly_spend_usd"] = usd

    mutate_config(put)


@router.put("/budget")
async def set_budget(body: BudgetBody) -> dict[str, Any]:
    usd = body.monthly_usd
    if not math.isfinite(usd) or not 0 < usd <= MAX_BUDGET_USD:
        raise HTTPException(status_code=400, detail=f"monthly_usd must be above 0 and at most {MAX_BUDGET_USD}")
    usd = round(usd, 2)
    try:
        await asyncio.to_thread(_set_budget, usd)
    except Exception as e:  # noqa: BLE001
        logger.warning("usage budget not written: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail="budget not saved") from None
    return {"monthly_usd": usd}
