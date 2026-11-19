"""Feature Map: a Brain knows what its product does, and can prove each feature still works.

Design: docs/design/2026-11-18-feature-map.md (v3). Two layers:

- what SwarmAI owns: the feature knowledge (``features.yaml``: what each feature is, how
  a user reaches it, what working looks like, its target) and the loop around it (a
  change selects the features it can reach, the results decide a pipeline run from
  facts the CLI recorded, the evidence reaches the person);
- what SwarmAI rents: the execution. A web walk is a standard Playwright Test spec, run
  by a pinned Playwright the product installs itself (``runtime``).

Modules:

- ``files``    — the feature folder: schemas, where it lives, content hashes
- ``ledger``   — the append-only record of every walk result, and the states it implies
- ``reach``    — which features a change can touch (by target)
- ``runtime``  — the pinned Node + @playwright/test + Chromium, installed on first use
- ``sandbox``  — the product-built environment a replay runs in
- ``replay``   — verify / replay: sandbox → Playwright or commands → results → ledger
- ``pipeline`` — the facts a pipeline run's TEST is decided from, and its REPORT lines

The CLI is ``scripts/feature_map.py``. The daemon never runs a replay.
"""
