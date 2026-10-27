"""Brain bootstrap — what a new brain learns about its repos before anything is written.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.2–§5.3. Every module here is
deterministic: no LLM, no network, and nothing in a repo is ever executed. A repo the
user binds is untrusted input, so files are read only inside the repo, secret files are
never opened, and every git command runs with hooks and fsmonitor disabled
(``repo_read``).

- ``repo_read``  — the safe primitives: confined reads, secret-file rule, hardened git,
                   tracked-file listing, fix/revert history.
- ``packages``   — monorepo package boundaries from workspace manifests.
- ``docs``       — document discovery, the four tiers, links, CHANGELOG sections, API
                   contract summaries.
- ``commands``   — dev commands parsed from CI, Makefile, justfile, package.json, tox, nox.
- ``survey``     — the Asset Survey: one ``survey.json`` per project, the input of the
                   P2 extraction, the Brain Hub Specs panel and CDF's S7 later.
- ``doc_index``  — the repo's docs indexed in place under ``bind:<project>/<repo>/``.
"""
