"""ExpertManager — the SSOT registry over the Expert Library (``backend/experts/``).

An **expert** is SwarmAI's third asset class: not a skill (an invocable capability
an agent calls) and not an agent (a persistent identity), but a stateless,
zero-memory, fresh-context ADVERSARIAL reviewer a Super IC spawns to criticise
finished work. The experts live as markdown files with YAML frontmatter under
``backend/experts/<domain>/<expert_id>.md``; this module enumerates that tree into
a registry keyed by ``expert_id``.

Thin by design (design 2026-10-16 §D5, P6): it REUSES
``core.skill_manager.parse_frontmatter`` — the same frontmatter parser the skill
runtime and ``scripts/lint_skills.py`` use — so an expert's frontmatter can never
be read differently here than anywhere else (no second YAML parser to drift,
avoids the C042 "build a mechanism that already exists" trap).

Consumers:
    * ``GET /api/experts`` (R2 UI) aggregates ``scan_experts()`` by domain.
    * ``scripts/lint_experts.py`` is the fail-closed ADMISSION gate; this module is
      the read-side registry. They are independent on purpose (defense-in-depth):
      the registry enumerates what IS valid, the linter rejects what is NOT.

The ``_core/`` directory holds the shared adversarial-stance skeleton, not a
domain expert — it is excluded from the registry (``domain: _core``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.skill_manager import SkillParseError, parse_frontmatter

# Repo-relative anchor: this file is backend/core/expert_manager.py, so parents[1]
# is backend/. The experts tree is backend/experts/.
_EXPERTS_ROOT = Path(__file__).resolve().parents[1] / "experts"

# The 6 declarative fields every domain expert MUST carry (design §D2). Kept here
# as the read-side contract; the WRITE-side admission gate (lint_experts.py) is the
# authority that REJECTS a violation — this list is what the registry expects to
# find, mirrored there so the two cannot silently disagree about the field set.
REQUIRED_FIELDS = (
    "expert",
    "display_name_zh",
    "display_name_en",
    "domain",
    "scope",
    "visibility",
)

# The _core skeleton (adversarial-stance) is shared scaffolding, not a dispatchable
# expert — it declares domain: _core and is filtered out of the registry.
_CORE_DOMAIN = "_core"


@dataclass(frozen=True)
class ExpertInfo:
    """One enumerated expert. Immutable snapshot of its frontmatter."""

    expert_id: str
    display_name_zh: str
    display_name_en: str
    domain: str
    scope: str
    visibility: str
    path: Path
    missing_fields: tuple[str, ...] = field(default=())

    @property
    def is_valid(self) -> bool:
        """True when every REQUIRED_FIELD was present and non-empty.

        The registry still SURFACES an invalid expert (so the UI / a health view
        can show it is broken) rather than silently dropping it — dropping would
        make a malformed expert indistinguishable from an absent one. The
        fail-closed REJECTION is lint_experts.py's job, not the registry's.
        """
        return not self.missing_fields


class ExpertManager:
    """Enumerates ``backend/experts/`` into an ``{expert_id: ExpertInfo}`` registry."""

    def __init__(self, experts_root: Path | None = None) -> None:
        self._root = experts_root or _EXPERTS_ROOT

    def scan_experts(self) -> dict[str, ExpertInfo]:
        """Scan every ``<domain>/<id>.md`` under the experts root → registry.

        Keyed by ``expert_id`` (the frontmatter ``expert:`` field, which is the
        identity contract — NOT the filename, so a rename of either is caught as a
        mismatch by the admission gate). ``_core`` scaffolding is excluded.

        Raises:
            SkillParseError: if a file's frontmatter is malformed YAML. A malformed
                expert is a hard error, not a skipped file — a silently-dropped
                expert reads as "not installed", the exact ambiguity the admission
                gate exists to prevent.
        """
        registry: dict[str, ExpertInfo] = {}
        if not self._root.exists():
            return registry

        for md in sorted(self._root.rglob("*.md")):
            meta, _body = parse_frontmatter(md)
            if not meta:
                # No frontmatter at all → treat as a fully-missing expert so the
                # health surface shows it, keyed by filename stem as a last resort.
                registry[md.stem] = ExpertInfo(
                    expert_id=md.stem, display_name_zh="", display_name_en="",
                    domain="", scope="", visibility="",
                    path=md, missing_fields=REQUIRED_FIELDS,
                )
                continue

            domain = str(meta.get("domain", "") or "")
            if domain == _CORE_DOMAIN:
                continue  # shared skeleton, not a dispatchable expert

            missing = tuple(
                f for f in REQUIRED_FIELDS
                if not str(meta.get(f, "") or "").strip()
            )
            expert_id = str(meta.get("expert", "") or md.stem)
            registry[expert_id] = ExpertInfo(
                expert_id=expert_id,
                display_name_zh=str(meta.get("display_name_zh", "") or ""),
                display_name_en=str(meta.get("display_name_en", "") or ""),
                domain=domain,
                scope=str(meta.get("scope", "") or ""),
                visibility=str(meta.get("visibility", "") or ""),
                path=md,
                missing_fields=missing,
            )
        return registry

    def by_domain(self) -> dict[str, list[ExpertInfo]]:
        """Registry grouped by domain — the shape ``GET /api/experts`` aggregates."""
        grouped: dict[str, list[ExpertInfo]] = {}
        for info in self.scan_experts().values():
            grouped.setdefault(info.domain, []).append(info)
        for experts in grouped.values():
            experts.sort(key=lambda e: e.expert_id)
        return grouped


# Module-level convenience singleton (mirrors skill_manager's module-level fns).
_default_manager = ExpertManager()


def scan_experts() -> dict[str, ExpertInfo]:
    """Enumerate the default experts tree. See ``ExpertManager.scan_experts``."""
    return _default_manager.scan_experts()


__all__ = [
    "ExpertManager",
    "ExpertInfo",
    "scan_experts",
    "REQUIRED_FIELDS",
    "SkillParseError",
]
