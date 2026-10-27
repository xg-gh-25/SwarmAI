"""requirements_text returns only the prose of a REQUIREMENTS document.

The design recall (run-knowledge §3.6.3) queries with the user's words plus the
REQUIREMENTS text. Enum values (EARS pattern, analysis kind/resolution,
answered_by, decided_by), ids and refs are not prose: before this fix they went
into the query as search words (``missing_edge``, ``rewritten``, ``ubiquitous``).

Properties:
- every prose field's text is kept, including strings nested in root_cause;
- no schema enum value, id, ref or boolean reaches the text;
- the text is a valid REQUIREMENTS document's (validated, so the fixture cannot
  drift from the schema).
"""
from core.run_protocol import run_knowledge, schemas

DOC = {
    "requirements": [{
        "id": "R1",
        "story": "As an operator I want quiet hours",
        "acceptance": [
            {"id": "R1.1", "pattern": "ubiquitous", "response": "skip midnight sends",
             "negative": False, "universal": True},
            {"id": "R1.2", "pattern": "event", "condition": "when the clock is UTC",
             "response": "convert before comparing", "negative": True, "universal": False,
             "text": "timezone conversion"},
        ],
    }],
    "analysis": [{"kind": "missing_edge", "refs": ["R1.1"], "note": "daylight saving gap",
                  "resolution": "rewritten"}],
    "clarifications": [{"qid": "Q1", "question": "which timezone", "answer": "operator local",
                        "answered_by": "evidence", "decided_by": "agent_after_cap"}],
    "understanding": {"claim": "sends ignore local time", "readings": ["scheduler reads UTC"],
                      "evidence": ["cron log at 00:03"],
                      "root_cause": {"where": "scheduler clock", "why": "no conversion"}},
    "knowledge_refs": ["0123abcd"],
}

ENUMS = (schemas.EARS_PATTERNS | schemas.ANALYSIS_KINDS | schemas.ANALYSIS_RESOLUTIONS
         | {"user", "evidence", "agent_after_cap"})


def test_fixture_is_a_valid_requirements_document():
    assert schemas.validate_requirements(DOC, variant="full", profile="full") == []


def test_prose_is_kept():
    text = run_knowledge.requirements_text(DOC)
    for prose in ("quiet hours", "skip midnight sends", "when the clock is UTC",
                  "convert before comparing", "timezone conversion", "daylight saving gap",
                  "which timezone", "operator local", "sends ignore local time",
                  "scheduler reads UTC", "cron log at 00:03", "scheduler clock",
                  "no conversion"):
        assert prose in text


def test_enum_values_ids_and_refs_are_left_out():
    words = set(run_knowledge.requirements_text(DOC).split())
    leaked = {w for w in words if w in ENUMS or w in {"R1", "R1.1", "R1.2", "Q1", "0123abcd",
                                                       "user", "True", "False"}}
    # "evidence" is the answered_by enum value; the readings/evidence prose holds no
    # bare "evidence" word, so any occurrence would be the enum leaking
    assert "evidence" not in words
    assert leaked == set()


def test_design_query_has_no_enum_words():
    query = run_knowledge.query_for("quiet hours", run_knowledge.requirements_text(DOC))
    assert not set(query.split()) & {"missing_edge", "rewritten", "ubiquitous", "event",
                                     "agent_after_cap"}
