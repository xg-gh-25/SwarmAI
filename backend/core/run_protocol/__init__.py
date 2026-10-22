"""Pipeline v1's deterministic run protocol.

Public modules:
- ``events`` — immutable event envelopes, identities, blobs and chain checks.
- ``store`` — locked per-run/session event, blob, contract and seal persistence.
- ``answers`` — strict Gate-0 and REVIEW answer-envelope parsing.
- ``hook_bridge`` — exact Agent tool-use to SubagentStop event correlation.
- ``lifecycle``, ``checks``, ``schemas``, ``findings``, ``probe``, ``auth`` and
  ``decision`` — frozen v13 protocol projections and validators.
- ``git_tree``, ``validations``, ``runner``, ``final``, ``cache`` and ``carry`` —
  real tree identity, base-pinned verification, sealed final evidence and reuse.
- ``review`` — lightweight expert roster, exact handouts/reuse, R2 and generated docs.
- ``documents`` — validated REQUIREMENTS/DESIGN sources, generated Markdown, checks and decisions.
- ``landing`` / ``retention`` — tree-bound manual landing and permanent snapshot handling.
"""
