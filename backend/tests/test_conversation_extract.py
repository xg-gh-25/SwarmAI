"""DoD2 (run_e346b8ed): conservative conversation→DDD extractor.

Drives the REAL extract_candidates. The ONLY thing mocked is the LLM boundary
(invoke_fn) — everything else (tier re-assert, owner-ratification precondition,
candidate hygiene) is exercised for real.

Conservatism is proven by the structural gates that fire BEFORE the LLM:
  - unauthorized/unknown-tier messages are dropped (tier re-assert on READ)
  - owner-absent batch → [] WITHOUT calling the LLM (§9-D3)
  - evidence-less candidates from the LLM are dropped (traceability hygiene)
"""

import json

from core.conversation_extract import extract_candidates, _authorized_inbound


def _row(text, tier, direction="inbound", name="U"):
    return {
        "direction": direction,
        "content": text,
        "created_at": "2026-07-07T00:00:00+00:00",
        "metadata": {"sender_tier": tier, "sender_display_name": name},
    }


def _llm_returning(candidates):
    """A fake invoke_fn that returns a fixed candidate list as the LLM would."""
    payload = json.dumps({"candidates": candidates})

    def _fn(prompt, **kwargs):
        _fn.called = True
        _fn.last_prompt = prompt
        return payload, 100, 50

    _fn.called = False
    return _fn


# ── Structural gates (fire BEFORE the LLM) ────────────────────────────────────

def test_unauthorized_messages_excluded():
    rows = [
        _row("public noise", "public"),
        _row("unknown tier", None),
        _row("owner decision", "owner"),
        _row("trusted input", "trusted"),
    ]
    kept = _authorized_inbound(rows)
    tiers = {m["sender_tier"] for m in kept}
    assert tiers == {"owner", "trusted"}, "only owner/trusted survive the read gate"


def test_outbound_excluded():
    rows = [_row("bot reply", "owner", direction="outbound")]
    assert _authorized_inbound(rows) == []


def test_owner_absent_returns_empty_without_llm():
    """§9-D3: no owner in-thread → 0 candidates, and the LLM is NEVER called."""
    llm = _llm_returning([{"content": "x", "evidence": "y"}])
    rows = [_row("trusted A says do X", "trusted"), _row("trusted B agrees", "trusted")]
    result = extract_candidates(rows, "SwarmAI", invoke_fn=llm)
    assert result == []
    assert llm.called is False, "owner-absent must short-circuit BEFORE the LLM"


def test_no_authorized_messages_returns_empty_without_llm():
    llm = _llm_returning([{"content": "x", "evidence": "y"}])
    rows = [_row("public chatter", "public")]
    result = extract_candidates(rows, "SwarmAI", invoke_fn=llm)
    assert result == []
    assert llm.called is False


# ── LLM boundary + hygiene ────────────────────────────────────────────────────

def test_happy_path_emits_candidates_with_owner_present():
    llm = _llm_returning([
        {"content": "Adopt scheduled daily digest for conversation→DDD.",
         "target_doc": "TECH.md", "target_section": "Architecture",
         "evidence": "XG: let's go with the daily digest", "confidence": 0.8},
    ])
    rows = [
        _row("SDE: should we use session-wrap or digest?", "trusted"),
        _row("XG: let's go with the daily digest", "owner", name="XG"),
    ]
    result = extract_candidates(rows, "SwarmAI", invoke_fn=llm)
    assert llm.called is True
    assert len(result) == 1
    assert result[0]["evidence"] == "XG: let's go with the daily digest"


def test_evidence_less_candidates_dropped():
    """A candidate with no evidence quote is dropped (traceability hygiene) even
    if the LLM returned it — never surface an unverifiable extraction."""
    llm = _llm_returning([
        {"content": "has evidence", "evidence": "XG: yes", "confidence": 0.7},
        {"content": "no evidence quote", "evidence": "", "confidence": 0.9},
        {"content": "", "evidence": "XG: empty content", "confidence": 0.9},
    ])
    rows = [_row("XG: yes", "owner", name="XG")]
    result = extract_candidates(rows, "SwarmAI", invoke_fn=llm)
    assert len(result) == 1
    assert result[0]["content"] == "has evidence"


def test_llm_failure_is_fail_closed():
    """If the LLM boundary raises, extract nothing (never crash the digest job)."""
    def _boom(prompt, **kwargs):
        raise RuntimeError("bedrock down")
    rows = [_row("XG: do X", "owner", name="XG")]
    assert extract_candidates(rows, "SwarmAI", invoke_fn=_boom) == []


def test_unparseable_llm_response_returns_empty():
    def _garbage(prompt, **kwargs):
        return "not json at all", 10, 5
    rows = [_row("XG: do X", "owner", name="XG")]
    assert extract_candidates(rows, "SwarmAI", invoke_fn=_garbage) == []
