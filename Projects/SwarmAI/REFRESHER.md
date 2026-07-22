# ⑥ Refresher — SwarmAI

Section ⑥ GOVERNs the projection of this DDD's **governed asset(s)**. It is a
**self-contained mechanism that REGENERATES the projection from the asset's source** —
its shape follows the asset `kind` (code→`code-intel.json`, data→schema, corpus→index).
It ships the refresher (capability), never the projection (derived data). The default
code refresher is `s_repo-to-ddd` (narrow refresh mode; see `aim.json` native_skills).

**Asset shape (this DDD):** SwarmAI is a **code-repo** brain — it governs one
`code-repo` asset (the SwarmAI product source), so the code refresher applies directly.

**Activation:** ⑥ activates when an asset is BOUND (see ⑤ `bindings.yaml`). For a
**0-asset brain (pure knowledge) it would be a no-op**. Once the code asset is bound
and a dev-consumer profile pulls this DDD, the refresher regenerates the projection
LOCALLY (never PR-flowed-back — the derived-projection rule, spec §3.6).
