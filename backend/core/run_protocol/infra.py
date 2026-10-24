"""Infrastructure failures of a Pipeline v1 run (v13 §A1.2, conformance F11).

Exactly three kinds of failure end a run ``failed`` with ``infrastructure_failed``:
the audit area cannot be written, the hook-token service is unreachable, or git
cannot write objects. Each is raised as a subclass of ``InfrastructureError`` by the
layer that meets it; the layer that catches it (the CLI) records the
event. Every other exception propagates as before.

Public symbols:
- ``InfrastructureError`` — base, with ``component`` (one of ``COMPONENTS``).
- ``COMPONENTS`` — the three kinds.
- ``find`` — the InfrastructureError in an exception's cause / context chain, or None.
"""
from __future__ import annotations

COMPONENTS = frozenset({"audit_store", "hook_token", "git_objects"})


class InfrastructureError(RuntimeError):
    """A run's infrastructure failed; ``component`` names which."""

    component = ""

    def __init__(self, message: str, *, component: str | None = None):
        super().__init__(message)
        if component is not None:
            self.component = component
        if self.component not in COMPONENTS:
            raise ValueError(f"unknown infrastructure component {self.component!r}")


def find(exc: BaseException | None) -> InfrastructureError | None:
    """The InfrastructureError *exc* is or was raised from (cause/context chain)."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, InfrastructureError):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None
