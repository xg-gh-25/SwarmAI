#!/usr/bin/env python3
"""eval_spine_probe.py — deterministic spine canaries for the BVT gate
(run_5edf2cc0 C4, gap G4).

The pre-existing gate set covered only factual_accuracy/capability dims. These
probes add the SPINE: the invariants whose failure means "Swarm is unsafe or
degraded to run at all" — context-isolation (privacy), the gate's own freshness
binding, and prompt-budget. Each subcommand prints `<NAME>_OK` (exit 0) or
`<NAME>_FAIL ...` (exit 1). A trailing `negative` arg flips the assertion so the
teeth gate (golden_case_validator.gate_teeth) can prove the probe goes RED on a
broken invariant — structural, no LLM, sub-second.

Subcommands:
  safe_group_exclude   — group-channel context MUST drop MEMORY.md + USER.md
  safe_nonowner_exclude — non-owner (light) channel MUST drop EVOLUTION.md + PROJECTS.md
  gate_freshness       — ci_eval_gate's code_digest changes when an eval input changes
  prompt_budget        — effective context budget stays within the model window cap
  assembly_floor       — under HARD over-budget, _enforce_token_budget keeps the
                         non-truncatable identity floor (P0-P2: SWARMAI/IDENTITY/
                         SOUL/SELF) byte-INTACT, fits the budget, and truncates
                         lowest-priority first (alive != correct: GS_COST001 only
                         checks the budget NUMBER, never that assembly OUTPUT
                         respects it under truncation)
  memory_select        — the MEMORY.md SELECTIVE-injection branch (≥30K tokens,
                         what production runs every session at ~49K) keeps L0
                         (index + Open Threads), loads keyword-matched sections
                         within budget, and emits a 'Not loaded' manifest — none
                         of which the full-injection path does
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _ok(name: str) -> int:
    print(f"{name}_OK")
    return 0


def _teeth(name: str) -> int:
    """Negative-mode success: the probe proved its teeth (it ran the real wire,
    saw the invariant broken, and the positive check correctly FAILED). Emits a
    DISTINCT ``<NAME>_TEETH`` token — never the positive ``<NAME>_OK`` marker —
    so the runtime teeth check (eval_runner._verify_canary_teeth) can affirm
    discrimination without colliding with the positive marker. Returns 0: from
    the gate's view the negative ran SUCCESSFULLY (the teeth bit)."""
    print(f"{name}_TEETH")
    return 0


def _fail(name: str, why: str) -> int:
    print(f"{name}_FAIL {why}")
    return 1


def _check_exclude(name: str, attr: str, required: set, negative: bool) -> int:
    """Assert the REAL context_directory_loader exclude-set covers `required`.

    negative mode: monkeypatch the actual module attribute to a BROKEN set
    (missing one required file) and assert this very check then returns FAIL.
    This tests the REAL invariant + the check's own teeth (not local set-arithmetic
    — adversarial MEDIUM #4: the prior version subtracted from a local copy, a
    tautology of set semantics that never touched the imported symbol)."""
    import core.context_directory_loader as cdl
    if negative:
        saved = getattr(cdl, attr)
        try:
            setattr(cdl, attr, frozenset(set(saved) - {next(iter(required))}))
            broke = _check_exclude(name, attr, required, negative=False)
        finally:
            setattr(cdl, attr, saved)
        # The positive check MUST have failed (rc 1) on the broken set.
        return _teeth(name) if broke != 0 else _fail(name, "negative did not break the real invariant")
    current = set(getattr(cdl, attr))
    return _ok(name) if required.issubset(current) else _fail(name, f"{attr} missing {required - current}")


def safe_group_exclude(negative: bool) -> int:
    """Group-channel prompts must exclude MEMORY.md + USER.md (privacy)."""
    return _check_exclude("SAFE_GROUP", "GROUP_CHANNEL_EXCLUDE", {"MEMORY.md", "USER.md"}, negative)


def safe_nonowner_exclude(negative: bool) -> int:
    """Non-owner light-channel prompts must exclude EVOLUTION.md + PROJECTS.md."""
    return _check_exclude("SAFE_NONOWNER", "CHANNEL_LIGHT_EXCLUDE", {"EVOLUTION.md", "PROJECTS.md"}, negative)


def gate_freshness(negative: bool) -> int:
    """The gate's code_digest MUST change when an eval input changes — the
    meta-test that the freshness binding actually binds (if this breaks, every
    other gate is fake-green)."""
    from scripts.eval_runner import compute_code_digest, _find_swarmai_repo, _GATE_CODE_PATHS
    name = "GATE_FRESH"
    root = Path.home() / ".swarm-ai" / "SwarmWS"
    try:
        repo = _find_swarmai_repo()
    except Exception as e:
        return _fail(name, f"cannot find repo: {e}")
    base = compute_code_digest(root, code_root=repo)
    if negative:
        # Broken invariant: a digest computed over an empty path list must NOT
        # equal the real digest (proves the digest actually depends on inputs).
        import scripts.eval_runner as er
        saved = er._GATE_CODE_PATHS
        try:
            er._GATE_CODE_PATHS = []
            empty = compute_code_digest(root, code_root=repo)
        finally:
            er._GATE_CODE_PATHS = saved
        return _teeth(name) if empty != base else _fail(name, "digest ignored its inputs")
    # Positive: digest is a stable non-empty 16-hex over real inputs.
    again = compute_code_digest(root, code_root=repo)
    stable = base == again and len(base) == 16 and bool(_GATE_CODE_PATHS)
    return _ok(name) if stable else _fail(name, f"unstable/empty digest base={base} again={again}")


def prompt_budget(negative: bool) -> int:
    """The effective context budget must stay within the model window (over-budget
    = silent truncation = degraded cognition)."""
    from core.context_directory_loader import ContextDirectoryLoader
    name = "PROMPT_BUDGET"
    loader = ContextDirectoryLoader(Path.home() / ".swarm-ai" / "SwarmWS" / ".context")
    window = 1_000_000

    def _within(w: int) -> bool:
        # Real invariant: the REAL compute_token_budget must return a positive
        # budget strictly inside the window (over-budget = silent truncation).
        b = loader.compute_token_budget(w)
        return 0 < b < w

    if negative:
        # compute_token_budget is correctly bounded at every real window (it scales
        # the tier DOWN), so there is no breaking INPUT. To prove the check has
        # teeth, monkeypatch the REAL method to an over-window impl and assert this
        # very check then catches it (same discipline as the exclude probes).
        saved = loader.compute_token_budget
        try:
            loader.compute_token_budget = lambda w: w + 1  # broken: exceeds window
            broke = _within(window)
        finally:
            loader.compute_token_budget = saved
        return _teeth(name) if not broke else _fail(name, "negative did not break the real budget check")
    return _ok(name) if _within(window) else _fail(name, f"budget out of bounds for window {window}")


def _assembly_floor_holds(loader, sections, budget: int) -> tuple[bool, str]:
    """Run the REAL _enforce_token_budget and check the NO-TRUNCATE contract.

    Returns (ok, why). Pure check — no printing — so the negative path can
    assert this very check FAILs on a broken invariant (mirrors _check_exclude).

    ⚠️ CONTRACT CHANGED (pure-filesystem recall design §3.5/DoD1, 2026-06-28):
    the assembly line NO LONGER truncates by size. On budget overshoot it WARNs
    and injects FULL content untruncated (size governance is the write-side
    management line's job; the read line trusts its inputs are healthy). The old
    INV2 "budget converged" + INV3 "priority truncation order" invariants are
    GONE — they asserted truncation that was removed. The surviving, strengthened
    invariants:

    INV1 EVERYTHING byte-intact: after enforcement EVERY section (the P0-P2
      identity floor AND the truncatable ones) is byte-IDENTICAL to input —
      nothing is shortened or dropped. This is now THE load-bearing invariant:
      if any section shrinks, truncation crept back in.
    INV2 no truncation marker: no section carries the "[Truncated:" indicator
      (the removed path's signature). Its presence = a regression.
    """
    before = {name: content for _p, name, content, _t, _tf in sections}
    out = loader._enforce_token_budget(list(sections), budget=budget)
    out_by_name = {name: content for _p, name, content, _t, _tf in out}

    # INV1 — EVERY section byte-intact (floor AND truncatable: nothing is cut).
    for name, content in before.items():
        if out_by_name.get(name) != content:
            return False, (f"INV1 no-truncate BROKEN: '{name}' was altered — "
                           f"the read line must never shorten content (§3.5)")

    # INV2 — no truncation marker anywhere (the removed path's signature).
    for name, content in out_by_name.items():
        if "[Truncated:" in (content or ""):
            return False, (f"INV2 truncation regression: '{name}' carries a "
                           f"'[Truncated:' marker — truncation was removed (§3.5)")
    return True, "ok"


def _floor_fixture() -> list[tuple]:
    """Synthetic sections that FORCE hard truncation. Each P0-P2 identity
    section is LARGE (would be cut if it were truncatable — defeats the
    'generous budget = vacuous pass' trap the skeptic flagged). P4/P10 are
    truncatable and even larger, so a correct enforcer cuts THEM, not the floor."""
    big = ("word " * 400).strip()      # ~533 tokens each
    huge = ("token " * 1200).strip()   # ~1600 tokens each
    # Three truncatable tiers (P4 < P7 < P10) so INV3 checks a full monotone
    # ordering, not just a 2-way compare (adversarial LOW-2).
    return [
        (0, "SWARMAI", big, False, "tail"),
        (1, "IDENTITY", big, False, "tail"),
        (2, "SOUL", big, False, "tail"),
        (2, "SELF", big, False, "tail"),
        (4, "MidPrio4", huge, True, "tail"),
        (7, "MidPrio7", huge, True, "tail"),
        (10, "LowPrio10", huge, True, "tail"),
    ]


def assembly_floor(negative: bool) -> int:
    """Under HARD over-budget, the REAL _enforce_token_budget must inject ALL
    content byte-INTACT (no truncation) and emit a warning — NOT shorten anything
    (pure-filesystem recall design §3.5/DoD1, 2026-06-28). The read line never
    arbitrates by size; size governance is the write-side management line's job."""
    from pathlib import Path as _P

    from core.context_directory_loader import ContextDirectoryLoader
    name = "ASSEMBLY_FLOOR"
    loader = ContextDirectoryLoader(_P.home() / ".swarm-ai" / "SwarmWS" / ".context")
    sections = _floor_fixture()
    # Budget deliberately FAR below the fixture total (~6950 tok) so the old code
    # WOULD have truncated hard. The no-truncate contract requires every section
    # survives byte-intact regardless — proving the read line ignores the budget
    # for content purposes (warns only).
    budget = 4200

    if negative:
        # Teeth (mirrors _check_exclude:52 / prompt_budget:115): monkeypatch the
        # REAL _enforce_token_budget to a BROKEN impl that ignores `truncatable`
        # and shortens EVERY section (violating the identity floor). Then assert
        # this very probe's positive check (_assembly_floor_holds) FAILs on it.
        # If the check still passes, the probe has no teeth → FAIL.
        def _broken_enforce(secs, budget=None):
            out = []
            for p, n, c, trunc, tf in secs:
                # truncate ALL sections, including the non-truncatable floor
                out.append((p, n, (c[:20] + " [Truncated: floor BROKEN]"), trunc, tf))
            return out

        saved = loader._enforce_token_budget
        try:
            loader._enforce_token_budget = _broken_enforce
            ok, _why = _assembly_floor_holds(loader, sections, budget)
        finally:
            loader._enforce_token_budget = saved
        # The check MUST have failed (floor was altered by the broken enforcer).
        return _teeth(name) if not ok else _fail(
            name, "negative did not bite: floor check passed even when the "
                  "enforcer truncated the non-truncatable identity sections")

    ok, why = _assembly_floor_holds(loader, sections, budget)
    return _ok(name) if ok else _fail(name, why)


def _memory_fixture() -> str:
    """A >30K-token synthetic MEMORY.md that FORCES the selective branch.

    Section HEADINGS must equal what _key_to_section maps the index keys to
    (KD/DEC->'Decisions', GUI->'Guidelines', PIT->'Pitfalls') or the matched
    section silently skips (memory_index.py:1103-1105 — the vacuity trap Gate-0
    flagged). 'Decisions' is small + keyword-matchable ('caching prefix proxy');
    'Guidelines'/'Pitfalls' are padded huge so the total comfortably exceeds 30K
    (~48K, a ~60% margin so an estimate_tokens drift can't silently drop it under
    threshold — adversarial MEDIUM) and they land in the 'Not loaded' manifest
    (proving budget-bounded selection)."""
    def _pad(word: str, n: int) -> str:
        return (word + " ") * n
    idx = (
        "<!-- MEMORY_INDEX_START -->\n"
        "## Memory Index\n"
        "- [DEC01] caching prefix proxy decision | caching, prefix, proxy\n"
        "- [GUI01] widget layout guideline | widget, layout\n"
        "- [PIT01] resonance drift pitfall | resonance, drift\n"
        "<!-- MEMORY_INDEX_END -->"
    )
    return idx + (
        "\n\n## Open Threads\n- P0 thread alpha still open\n"
        "\n## Decisions\n- [DEC01] caching prefix proxy decision — "
        + _pad("cachingdetail", 300)
        + "\n\n## Guidelines\n- [GUI01] widget layout guideline — "
        + _pad("widgetfill", 18000)
        + "\n\n## Pitfalls\n- [PIT01] resonance drift pitfall — "
        + _pad("pitfill", 18000) + "\n"
    )


def _memory_select_holds(mem: str) -> tuple[bool, str]:
    """Run the REAL select_memory_sections on the >30K fixture and check the
    selective-branch invariants. Pure check (no printing) so the negative path
    can assert it FAILs on a broken invariant. memory_embeddings=False → no
    Bedrock; keyword-only is the verified production path (test_memory_wiring).

    Hermetic (adversarial MEDIUM): jobs.paths.DB_PATH is patched to a
    nonexistent path so the SessionRecall block (memory_index.py:1138-1155,
    gated on db_path.exists()) is skipped — the probe never pulls live-DB
    content into its output, regardless of machine DB state."""
    import jobs.paths as _jp
    from pathlib import Path as _P
    from unittest.mock import patch as _patch

    from core.context_directory_loader import ContextDirectoryLoader
    from core.memory_index import DEFAULT_MAX_TOKENS, select_memory_sections

    est = ContextDirectoryLoader.estimate_tokens

    def _select(**kw):
        # DB_PATH is imported inside select_memory_sections per-call, so patching
        # the module attribute is sufficient to neutralise the SessionRecall path.
        with _patch.object(_jp, "DB_PATH", _P("/nonexistent/eval-probe-no-db.sqlite")):
            return select_memory_sections(mem, session_signals={},
                                          memory_embeddings=False, **kw)

    # Matched-query run (drives keyword selection)
    hit = _select(user_message="caching prefix proxy")
    # INV1 MODE-SWITCH: selective emits the 'Not loaded' manifest tail; full
    # injection never does (the discriminator — NOT length, which a huge budget
    # could make vacuous).
    if "Not loaded" not in hit:
        return False, "INV1 mode-switch: no 'Not loaded' manifest → took FULL injection, not selective"
    # INV2 BUDGET: per-section add is strict (memory_index.py:1110). Exercised
    # with a TIGHT budget against a query that matches the LARGE 'Guidelines'
    # section (~16K) — under a 5K cap that section MUST be excluded (lands in
    # the manifest) and the output MUST stay <= the cap. A default-budget check
    # would be vacuous (the small matched section never approaches the cap, so
    # ignoring the budget would still pass — verified). This sub-case forces the
    # budget to be the binding constraint.
    TIGHT = 5000
    tight = _select(user_message="widget layout", max_tokens=TIGHT)
    tight_tok = est(tight)
    if tight_tok > TIGHT:
        return False, f"INV2 budget: output {tight_tok} > tight cap {TIGHT} (budget not enforced)"
    if "## Guidelines" in tight:
        return False, "INV2 budget: large 'Guidelines' section (~24K) loaded under a 5K cap (budget bypassed)"
    # also assert the default-budget run stays within its (larger) bound
    if est(hit) > DEFAULT_MAX_TOKENS:
        return False, f"INV2 budget: default-run output {est(hit)} > max {DEFAULT_MAX_TOKENS}"
    # INV3 L0-ALWAYS (matched case): index markers + the Open Threads SECTION.
    # Assert the section HEADER '## Open Threads', NOT the bare substring — the
    # string "Open Threads" also appears in the index block AND the 'Not loaded'
    # manifest, so a bare-substring check is vacuous (verified: it survives
    # dropping the real L0 load). The '## ' header only appears when the section
    # is actually loaded.
    if "<!-- MEMORY_INDEX_START -->" not in hit or "## Open Threads" not in hit:
        return False, "INV3 L0: index block or '## Open Threads' section missing in selective output"
    # INV4 KEYWORD-HIT: the query-matched 'Decisions' section actually loaded
    # (non-vacuous — heading equals _key_to_section('DEC01')).
    if "## Decisions" not in hit:
        return False, "INV4 keyword-hit: matched 'Decisions' section did NOT load"
    # INV5 NEVER-EMPTY (distinct from INV3): a garbage query with embed off must
    # STILL yield at least index + Open Threads (recall never returns empty).
    garbage = _select(user_message="zzz unrelated weather quux")
    if "<!-- MEMORY_INDEX_START -->" not in garbage or "## Open Threads" not in garbage:
        return False, "INV5 never-empty: garbage query lost index or '## Open Threads' section"
    return True, "ok"


def memory_select(negative: bool) -> int:
    """The selective-injection branch (MEMORY.md ≥ 30K) is what PRODUCTION runs
    every session (live MEMORY ~49K). It keeps the index + Open Threads (L0),
    loads keyword-matched sections within budget, and emits a 'Not loaded'
    manifest — none of which the full-injection path does. Guards the 5
    invariants (mode-switch / budget / L0 / keyword-hit / never-empty)."""
    import core.memory_index as mi
    name = "MEMORY_SELECT"
    mem = _memory_fixture()

    if negative:
        # Teeth (mirrors the spine module-attr monkeypatch idiom): raise the
        # threshold so high the fixture can NEVER trip selective → full path →
        # no manifest tail → the positive check MUST fail. Proves the probe
        # discriminates a broken mode-switch.
        saved = mi.FULL_INJECTION_THRESHOLD
        try:
            mi.FULL_INJECTION_THRESHOLD = 10 ** 9
            ok, _why = _memory_select_holds(mem)
        finally:
            mi.FULL_INJECTION_THRESHOLD = saved
        return _teeth(name) if not ok else _fail(
            name, "negative did not bite: selective invariants held even when "
                  "the threshold forced full injection")

    ok, why = _memory_select_holds(mem)
    return _ok(name) if ok else _fail(name, why)


_PROBES = {
    "safe_group_exclude": safe_group_exclude,
    "safe_nonowner_exclude": safe_nonowner_exclude,
    "gate_freshness": gate_freshness,
    "prompt_budget": prompt_budget,
    "assembly_floor": assembly_floor,
    "memory_select": memory_select,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _PROBES:
        print(f"usage: eval_spine_probe.py <{'|'.join(_PROBES)}> [negative]", file=sys.stderr)
        return 2
    negative = len(sys.argv) > 2 and sys.argv[2] == "negative"
    return _PROBES[sys.argv[1]](negative)


if __name__ == "__main__":
    sys.exit(main())
