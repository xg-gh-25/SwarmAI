"""CDF card 3 and unit-model property tests.

Methodology: categorical relation inputs are fully enumerated; Hypothesis permutes
hit sets to prove aggregation is order-independent. Offsets never define identity.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from hypothesis import given, strategies as st

from core.ingestion.card3 import HitRelation, aggregate_hits, relation_for_hit
from core.ingestion.contracts import ClaimVerdict, HitJudgment
from core.ingestion.units import Supersedes, active_head, layer_for, parse_units

OVERLAPS = ("same", "contained", "contains", "related", "unrelated")
VERDICTS = ("agrees", "refines", "refutes", "independent")
SCOPES = ("equal", "narrower", "wider", "disjoint")
EVIDENCE = ("inferred", "run_lesson", "review", "measured", "user_explicit")
TIMES = (None, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
RELATIONS = ("blocked", "replace", "exception", "duplicate", "refine", "independent")


def _hit(overlap="related", same_topic=False, verdict="independent", scope="equal",
         candidate_evidence="inferred", existing_evidence="inferred",
         candidate_time=None, existing_time=None, anchor="a", score=1.0):
    return HitJudgment(anchor=anchor, score=score, overlap=overlap, same_topic=same_topic,
                       claim_verdicts=[ClaimVerdict(verdict=verdict)], scope_relation=scope,
                       candidate_evidence=candidate_evidence, existing_evidence=existing_evidence,
                       candidate_observed_at=candidate_time, existing_observed_at=existing_time)


def test_relation_for_hit_full_categorical_enumeration():
    seen = set()
    for values in itertools.product(OVERLAPS, (False, True), VERDICTS, SCOPES,
                                    EVIDENCE, EVIDENCE, TIMES, TIMES):
        relation = relation_for_hit(_hit(*values))
        assert relation.relation in RELATIONS
        seen.add(relation.relation)
    assert seen == set(RELATIONS)


def test_relation_precedence_examples():
    assert relation_for_hit(_hit(verdict="refutes", scope="narrower")).relation == "exception"
    assert relation_for_hit(_hit(verdict="refutes", scope="equal",
                                 candidate_evidence="measured", existing_evidence="review")).relation == "replace"
    assert relation_for_hit(_hit(verdict="refutes", scope="equal",
                                 candidate_evidence="review", existing_evidence="measured")).relation == "blocked"
    assert relation_for_hit(_hit(overlap="same", verdict="agrees")).relation == "duplicate"
    assert relation_for_hit(_hit(same_topic=True, verdict="refines")).relation == "refine"


def test_equal_evidence_replace_requires_two_trusted_times_and_newer_candidate():
    assert relation_for_hit(_hit(verdict="refutes", candidate_time="2026-01-02T00:00:00Z",
                                 existing_time="2026-01-01T00:00:00Z")).relation == "replace"
    assert relation_for_hit(_hit(verdict="refutes", candidate_time=None,
                                 existing_time="2026-01-01T00:00:00Z")).relation == "blocked"
    assert relation_for_hit(_hit(verdict="refutes", candidate_time="2026-01-01T00:00:00Z",
                                 existing_time="2026-01-02T00:00:00Z")).relation == "blocked"


@given(st.lists(st.sampled_from(RELATIONS), min_size=0, max_size=8), st.data())
def test_aggregate_hits_order_independent(kinds, data):
    relations = [HitRelation(kind, f"a{index:02d}", float(index % 3)) for index, kind in enumerate(kinds)]
    permuted = data.draw(st.permutations(relations)) if relations else []
    assert aggregate_hits(relations) == aggregate_hits(permuted)


def test_aggregate_full_enumeration_to_length_three():
    for length in range(4):
        for kinds in itertools.product(RELATIONS, repeat=length):
            relations = [HitRelation(kind, f"a{index}", float(index)) for index, kind in enumerate(kinds)]
            expected = aggregate_hits(relations)
            for permutation in itertools.permutations(relations):
                assert aggregate_hits(permutation) == expected


def test_refine_tie_uses_lexical_anchor_and_never_edits_in_place():
    result = aggregate_hits([HitRelation("refine", "z", 2.0), HitRelation("refine", "a", 2.0)])
    assert result.action == "refine"
    assert result.target_anchor == "a"
    assert result.action != "edit"


def test_parse_units_identity_survives_offset_shift(tmp_path: Path):
    path = tmp_path / "MEMORY.md"
    path.write_text("## Guidelines\n\n- [guideline] **Stable** body <!-- cand:c.1 -->\n", encoding="utf-8")
    first = parse_units(path, root=tmp_path)[0]
    path.write_text("# Heading\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    second = parse_units(path, root=tmp_path)[0]
    assert first.identity == second.identity == "cand:c.1"
    assert first.byte_start != second.byte_start
    assert first.byte_hash == second.byte_hash


def test_active_head_only_follows_supersedes_and_fails_closed():
    edges = [Supersedes("new", "old"), Supersedes("newest", "new")]
    assert active_head("old", edges, existing_anchors={"old", "new", "newest"}) == "newest"
    assert active_head("old", edges + [Supersedes("old", "newest")]) is None
    assert active_head("missing", edges, existing_anchors={"old", "new", "newest"}) is None


def test_layer_for_is_closed():
    assert layer_for("Guidelines", "guideline") == "operational"
    assert layer_for("Architecture", "principle") == "domain"
    assert layer_for("What Makes It Different", "model") == "domain"
    assert layer_for("Key Subsystems", "model") == "domain"
    try:
        layer_for("Guidelines", "model")
    except ValueError as exc:
        assert str(exc) == "invalid_layer_combination"
    else:
        raise AssertionError("invalid pair was accepted")


def test_exception_with_duplicate_is_incompatible():
    """§7.1 aggregate rule 2: replace OR exception together with duplicate -> incompatible_hits."""
    from core.ingestion.card3 import HitRelation, aggregate_hits
    out = aggregate_hits([HitRelation(relation="exception", anchor="a", score=1.0),
                          HitRelation(relation="duplicate", anchor="b", score=1.0)])
    assert (out.action, out.reason) == ("reject", "incompatible_hits")


def _spec_aggregate(kinds: tuple[str, ...]) -> tuple[str, str]:
    """Independent oracle written from design §7.1 aggregate_hits rules 1-8 (action, reason)."""
    k = set(kinds)
    if "blocked" in k:
        return ("reject", "conflicts_stronger")
    if ("replace" in k or "exception" in k) and "duplicate" in k:
        return ("reject", "incompatible_hits")
    if kinds.count("replace") >= 2:
        return ("reject", "incompatible_hits")
    if kinds.count("replace") == 1:
        return ("replace", "replace")
    if "duplicate" in k:
        return ("reject", "already_exists")
    if "exception" in k:
        return ("add_exception", "exception")
    if "refine" in k:
        return ("refine", "refine")
    return ("add", "independent")


def test_aggregate_matches_spec_oracle_full_enumeration():
    for length in range(5):
        for kinds in itertools.product(RELATIONS, repeat=length):
            relations = [HitRelation(kind, f"a{index}", float(index)) for index, kind in enumerate(kinds)]
            got = aggregate_hits(relations)
            assert (got.action, got.reason) == _spec_aggregate(kinds), kinds


def _spec_relation(overlap, same_topic, verdict, scope, cand_ev, exist_ev, cand_t, exist_t):
    """Independent oracle from design §7.1 relation_for_hit rules 1-6 (r15 observed_at tie rule)."""
    from core.ingestion.contracts import EVIDENCE_ORDER
    refutes = verdict == "refutes"
    if refutes and scope == "narrower":
        return "exception"
    if refutes and scope in {"equal", "wider"}:
        if EVIDENCE_ORDER[cand_ev] > EVIDENCE_ORDER[exist_ev]:
            return "replace"
        if EVIDENCE_ORDER[cand_ev] == EVIDENCE_ORDER[exist_ev] and cand_t and exist_t and cand_t > exist_t:
            return "replace"
        return "blocked"
    if refutes and scope == "disjoint":
        return "independent"
    if overlap in {"same", "contained"}:
        return "duplicate"
    if same_topic:
        return "refine"
    return "independent"


def test_relation_for_hit_matches_spec_oracle():
    for values in itertools.product(OVERLAPS, (False, True), VERDICTS, SCOPES,
                                    EVIDENCE, EVIDENCE, TIMES, TIMES):
        assert relation_for_hit(_hit(*values)).relation == _spec_relation(*values), values
