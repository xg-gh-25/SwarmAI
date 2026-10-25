"""Filesystem-based Experts API endpoints (the Expert Library read surface).

Mirrors ``routers/skills.py``: pure filesystem via ``ExpertManager`` (no DB), and
the route is unauthenticated + context-free (same local-desktop-daemon convention
as skills.py:88 — a read-only projection of a static tree, no user input reaches
the filesystem, no mutation). The ``_expert_info_to_response`` converter is the
load-bearing piece Gate-1 (R2) required: ``ExpertInfo`` is a ``frozen=True``
dataclass and cannot feed FastAPI's ``response_model`` directly — it is projected
into the pydantic ``ExpertResponse`` explicitly (the ``_skill_info_to_response``
pattern).

Key endpoint:

- ``GET /experts`` — all experts grouped by domain (``{domain: [ExpertResponse]}``)
"""

import logging

from fastapi import APIRouter

from core.expert_manager import ExpertInfo, ExpertManager
from schemas.expert import ExpertResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_manager = ExpertManager()


def _expert_info_to_response(info: ExpertInfo) -> ExpertResponse:
    """Project a frozen ``ExpertInfo`` dataclass into the API ``ExpertResponse``.

    ``is_valid`` is a derived property (``not missing_fields``) — the health
    signal the UI surfaces. All other fields are the raw frontmatter projection.
    """
    return ExpertResponse(
        expert_id=info.expert_id,
        display_name_zh=info.display_name_zh,
        display_name_en=info.display_name_en,
        domain=info.domain,
        scope=info.scope,
        visibility=info.visibility,
        is_valid=info.is_valid,
    )


@router.get("", response_model=dict[str, list[ExpertResponse]])
async def list_experts() -> dict[str, list[ExpertResponse]]:
    """Return all enumerated experts grouped by domain.

    Projection of ``ExpertManager.by_domain()`` — each domain maps to its experts
    (already sorted by ``expert_id`` in ``by_domain``). Read-only; the registry
    SURFACES a malformed expert (``is_valid=False``) rather than dropping it.
    """
    grouped = _manager.by_domain()
    return {
        domain: [_expert_info_to_response(info) for info in experts]
        for domain, experts in grouped.items()
    }
