"""Deterministic CDF card 2 routing.

- ``route_card2`` validates project selection and returns one route.
- ``reroute_answer`` validates the one-hop shape mapping.
"""

from __future__ import annotations

from typing import Any

from .contracts import CARD1_ADAPTER, REROUTE_ALLOWED, Card1Answer, Destination, route_for

CARD2_VERSION = "cdf-card2-r15.1-v1"
_PROJECT_SHAPES = {"UserSingle", "ProjectSingle", "WorldSingle"}


class RoutingError(ValueError):
    """A deterministic routing failure carrying a reason code."""


def route_card2(answer: Card1Answer, project_candidates: tuple[str, ...]) -> Destination:
    """Resolve exactly one card 2 row without inventing a project."""
    if answer.shape in _PROJECT_SHAPES:
        if not project_candidates or answer.project not in project_candidates:
            raise RoutingError("ambiguous_project")
    if answer.shape == "Reference" and answer.project is not None:
        if not project_candidates or answer.project not in project_candidates:
            raise RoutingError("ambiguous_project")
    try:
        return route_for(answer)
    except ValueError as exc:
        raise RoutingError(str(exc)) from exc


def reroute_answer(answer: Card1Answer, reroute_to: str, *, project: str | None = None) -> Card1Answer:
    """Build a one-hop rerouted answer while preserving shared judged fields.

    Destination-specific fields that cannot be derived safely must have been
    supplied by the original answer or by ``project``. Unsupported conversions
    fail closed with ``destination_mismatch``.
    """
    if reroute_to not in REROUTE_ALLOWED.get(answer.shape, frozenset()):
        raise RoutingError("destination_mismatch")
    base = answer.model_dump(by_alias=True, exclude={"shape", "reroute_to"})
    base["shape"] = reroute_to
    base["reroute_to"] = None
    if reroute_to in _PROJECT_SHAPES:
        chosen = project or getattr(answer, "project", None)
        topic = getattr(answer, "topic", None)
        if not chosen or not topic:
            raise RoutingError("destination_mismatch")
        base.update(project=chosen, topic=topic)
    if reroute_to == "UserCross":
        if "user_kind" not in base:
            raise RoutingError("destination_mismatch")
        base.pop("project", None)
        base.pop("topic", None)
    if reroute_to == "WorldCrossGlobal":
        base["global_relevance"] = True
        base.pop("project", None)
        base.pop("topic", None)
    try:
        return CARD1_ADAPTER.validate_python(base)
    except Exception as exc:
        raise RoutingError("destination_mismatch") from exc
