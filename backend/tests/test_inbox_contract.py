"""The Inbox shared contract: the types every Inbox part builds on keep their shape."""
from datetime import datetime, timezone

from core.inbox.models import Identity, Lane, ReplyTarget, ThreadRef, target_sha
from core.inbox.sources import base


def test_target_sha_changes_when_recipients_change():
    ref = ThreadRef("mail", {"conversation_id": "c1"})
    a = ReplyTarget(label="Priya", recipients=("priya@example.com",))
    b = ReplyTarget(label="Priya", recipients=("billing@other.example",))
    assert target_sha(a, ref) == target_sha(ReplyTarget(label="Priya", recipients=("priya@example.com",)), ref)
    assert target_sha(a, ref) != target_sha(b, ref)
    assert target_sha(a, ref) != target_sha(a, ThreadRef("mail", {"conversation_id": "c2"}))


def test_identity_matches_any_alias_case_insensitively():
    me = Identity(ids=frozenset({"U123", "XG@Example.com"}))
    assert me.is_me("xg@example.com") and me.is_me("U123")
    assert not me.is_me("someone@example.com")


def test_registry_replaces_and_reports_missing():
    class Fake:
        kind = "fake"

    base.register_source("fake", lambda cfg: Fake())
    assert isinstance(base.create_source(base.SourceConfig(id="s1", kind="fake", backend="x")), Fake)
    try:
        base.create_source(base.SourceConfig(id="s2", kind="nope", backend="x"))
    except base.SourceNotFound:
        pass
    else:
        raise AssertionError("an unregistered kind must raise SourceNotFound")


def test_lanes_are_the_designed_four_plus_skip():
    assert {l.value for l in Lane} == {"reply", "proposed", "aware", "trend", "skip"}


def test_send_failed_carries_an_optional_fix():
    assert base.SendFailed("nope").fix == ""
    failed = base.SendFailed("token revoked", fix="paste a new token")
    assert str(failed) == "token revoked" and failed.fix == "paste a new token"


def test_refusal_and_source_shapes():
    from schemas import inbox as contract
    refusal = contract.SendRefusalOut(code="redacted", spans=[{"start": 1, "end": 4, "replacement": "[x]"}])
    assert refusal.spans[0].end == 4 and refusal.target is None and refusal.message == ""
    assert set(contract.SendRefusalOut.model_fields) == {"code", "message", "spans", "target", "reason"}
    assert {"status_detail", "enabled", "options"} <= set(contract.SourceOut.model_fields)
    secret = contract.SourceCreateRequest(kind="slack", backend="slack-web", secrets={"user_token": "s3cr3t"})
    assert "s3cr3t" not in repr(secret) and "s3cr3t" not in secret.model_dump_json()
