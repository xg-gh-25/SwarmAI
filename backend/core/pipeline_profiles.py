"""Pipeline profile stage lists for readers outside the run protocol.

A view of ``core.run_protocol.lifecycle.PROFILE_STAGES``, the only source of which
profiles exist and what they do (``PROFILE_CAPS``; design
docs/design/2026-10-26-pipeline-two-ends.md §4). Nothing here decides anything about a
profile: the gate, rigor and alias helpers that used to live here are gone with their
last caller.

Public symbols:
- ``PIPELINE_PROFILES`` — profile name → ordered stage list.
"""

from core.run_protocol.lifecycle import PROFILE_STAGES as _V1_STAGES

PIPELINE_PROFILES: dict[str, list[str]] = {name: list(stages)
                                           for name, stages in _V1_STAGES.items()}
