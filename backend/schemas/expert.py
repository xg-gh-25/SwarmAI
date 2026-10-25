"""API response schemas for the Expert Library (``backend/experts/``).

Mirrors ``schemas/skill.py``: ``ExpertInfo`` (a ``frozen=True`` dataclass in
``core/expert_manager.py``) is NOT directly serializable by FastAPI's
``response_model`` — it must be projected into a pydantic ``BaseModel`` via an
explicit converter in ``routers/experts.py`` (the same ``_skill_info_to_response``
pattern skills.py uses). Gate-1 (R2) flagged the frozen-dataclass serialization
gap; this module is the response contract that closes it.

The ``GET /api/experts`` endpoint returns experts grouped by domain:
``{domain: [ExpertResponse]}`` — projected from ``ExpertManager.by_domain()``.
"""

from pydantic import BaseModel


class ExpertResponse(BaseModel):
    """API response for a single enumerated expert.

    Projection of ``ExpertInfo`` — the 6 declarative frontmatter fields plus the
    derived ``is_valid`` health signal (``True`` when every REQUIRED_FIELD was
    present; ``False`` surfaces a malformed expert without dropping it).
    """

    expert_id: str
    display_name_zh: str
    display_name_en: str
    domain: str
    scope: str
    visibility: str
    is_valid: bool
