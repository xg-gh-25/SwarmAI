"""Pure offline logic for Swarm Cognitive Distillation Flow Phase A.

- No function in this package writes governed files or opens a database.
- ``pipeline.run_unit`` evaluates one normalized claim-group.

Implementation notes
--------------------
* ``project_snapshot`` may express explicit projects, repository bindings, and
  touched repositories with several source-specific keys. The normalizer accepts
  only strings already present in the snapshot and preserves the normative
  precedence: explicit binding, source repository binding, then one touched repo.
* A rule with no trustworthy structural signal is rejected rather than inferred.
* Phase A relations are supplied as a read-only mapping; malformed or cyclic
  chains have no active head.
* ``content_form`` is authoritative for taint, while the discriminated shape is
  authoritative for routing. A tainted reference with any shape except
  ``Reference`` is rejected.
"""

from .contracts import Candidate, UnitDecision
from .pipeline import run_unit

__all__ = ["Candidate", "UnitDecision", "run_unit"]
