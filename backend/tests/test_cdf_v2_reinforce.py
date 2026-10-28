"""Reinforcement and card-3b safety invariants for CDF v2."""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.ingestion.api_v2 import ResolvedEvidence, SCHEMA_V2_DDL
from core.ingestion.contracts import (
    Card3bAnswer, ExistingClaim, ProjectSingle, QualityAnswers,
)
from core.ingestion.pipeline_v2 import _validate_card3b, reinforce_or_reject


QUALITY = QualityAnswers(
    atomic=True, valuable=True, self_contained=True, durable=True,
    evidenced=True, internally_consistent=True, concise=True,
)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("PRAGMA foreign_keys=OFF;" + SCHEMA_V2_DDL)
    return conn


def add_root(conn: sqlite3.Connection, root: str, *, locator: str = "source",
             trust: str = "external", parents=()) -> None:
    conn.execute(
        """INSERT INTO evidence_roots
           (root_id,root_kind,locator,content_hash,trust,parent_root_ids,observed_at,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (root, "web_document", locator, root, trust, json.dumps(list(parents)),
         "2026-10-27T00:00:00Z", "2026-10-27T00:00:00Z"),
    )


def resolved(*roots: str) -> ResolvedEvidence:
    return ResolvedEvidence(root_ids=tuple(roots), lineage_root_ids=tuple(roots),
                            strength="inferred", tainted=True)


def test_echo_descended_from_target_is_not_novel() -> None:
    conn = db()
    anchor = "Knowledge/Learned/repos/p.md#Conventions#cand:old"
    add_root(conn, "target", locator=anchor)
    add_root(conn, "echo", parents=("target",))
    result = reinforce_or_reject(anchor, resolved("echo"), source_unit_id="new", conn=conn)
    assert not result.reinforced
    assert conn.execute("SELECT COUNT(*) FROM claim_support").fetchone()[0] == 0


def test_same_url_root_from_two_sessions_counts_once() -> None:
    conn = db()
    anchor = "Knowledge/Library/Links/example.com.md##cand:old"
    add_root(conn, "url-root", locator="https://example.com")
    first = reinforce_or_reject(anchor, resolved("url-root"), source_unit_id="one", conn=conn)
    second = reinforce_or_reject(anchor, resolved("url-root"), source_unit_id="two", conn=conn)
    assert first.reinforced and not second.reinforced
    assert conn.execute("SELECT COUNT(*) FROM claim_support").fetchone()[0] == 1


@pytest.mark.parametrize("trust,weight", [("user", 1.0), ("measured", 0.8), ("external", 0.3)])
def test_independent_root_reinforces_with_trust_weight(trust: str, weight: float) -> None:
    conn = db()
    add_root(conn, "independent", trust=trust)
    result = reinforce_or_reject("a#b#c", resolved("independent"),
                                 source_unit_id="new", conn=conn)
    assert result.reinforced
    assert conn.execute("SELECT weight,polarity FROM claim_support").fetchone() == (weight, "support")


def test_independent_refute_is_recorded_as_refute() -> None:
    conn = db()
    add_root(conn, "refute", trust="measured")
    result = reinforce_or_reject("a#b#c", resolved("refute"), source_unit_id="new",
                                 polarity="refute", conn=conn)
    assert result.reinforced
    assert conn.execute("SELECT polarity FROM claim_support").fetchone()[0] == "refute"


def answer(claims: list[ExistingClaim], effective: str) -> Card3bAnswer:
    card1 = ProjectSingle(
        shape="ProjectSingle", type="guideline", applicability="When changing files.",
        claims=["The replacement is complete."], quality=QUALITY,
        project="P", topic="architecture",
    )
    return Card3bAnswer(claims=claims, effective_unit=effective, card1=card1)


class EntailJudge:
    def card3b_entailment(self, claims, effective):
        return [{"claim_id": claim["claim_id"], "entailed": claim["claim"] in effective}
                for claim in claims]


def test_card3b_requires_every_independently_split_old_claim() -> None:
    old = "What remains. How it works. Why it matters. Another old claim."
    complete = answer([
        ExistingClaim(text=value, sentence=value, disposition="preserve", output_sentence=value)
        for value in ("What remains.", "How it works.", "Why it matters.", "Another old claim.")
    ], old)
    assert _validate_card3b(EntailJudge(), old, "candidate", complete, [])
    for missing in ("What remains.", "How it works.", "Why it matters.", "Another old claim."):
        mutated = complete.model_copy(update={
            "claims": [claim for claim in complete.claims if claim.sentence != missing],
            "effective_unit": old.replace(missing, ""),
        })
        assert not _validate_card3b(EntailJudge(), old, "candidate", mutated, [])
