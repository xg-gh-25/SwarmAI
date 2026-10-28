"""CDF S7 after-session review (core/ingestion/sources/s7_review.py; worker.run_session_job;
the §9 agent-self rule in pipeline_v2). Design: docs/design/2026-10-29-cdf-session-review.md.

What is tested:
- signals and the threshold are code-neutral: 3 user messages (brainstorm), 10 tool calls
  (research), any written file (a deck), or a rule/correction; 2 chat lines do not
  qualify; session type code / files / conversation.
- review_session on real databases: the fork is asked through the injectable runner with
  the flat schema; the prompt carries the review method, examples per task type, the
  flagged messages and the evidence index; units citing the index are enqueued as S7,
  others rejected; the attempt records type and sample; the watermark advances; nothing
  new means no call; no sdk id or no CLI transcript means unresumable.
- run_session_job runs the user pass always, the S2 main pass when unresumable.
- an S7 scope=agent correction entailed by the user's words reaches EVOLUTION/MEMORY; not
  entailed, it stays out of them.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from tests.test_cdf_v2_e2e import (  # noqa: F401  (cdf_env is a fixture)
    SESSION, Judge, _drain, _record_person_message, cdf_env,
)
from core.ingestion.sources import s7_review


def _user(i, text="ok"):
    return {"id": f"u{i}", "role": "user", "content": text, "created_at": f"2026-10-29T00:00:{i:02d}"}


def _tool(i, name="Read", **inp):
    return {"id": f"a{i}", "role": "assistant", "created_at": f"2026-10-29T00:01:{i:02d}",
            "content": [{"type": "tool_use", "name": name, "input": inp}]}


@pytest.mark.parametrize("rows,ok,kind", [
    ([_user(1), _user(2)], False, "conversation"),                       # two chat lines
    ([_user(1), _user(2), _user(3)], True, "conversation"),               # a brainstorm
    ([_user(1)] + [_tool(i) for i in range(10)], True, "conversation"),   # research
    ([_user(1), _tool(1, "Write", file_path="/w/deck.md")], True, "files"),
    ([_user(1), _tool(1, "Edit", file_path="/w/app.py")], True, "code"),
    ([_user(1, "不对，以后都要先出 mockup")], True, "conversation"),       # a correction
])
def test_signals_are_code_neutral(rows, ok, kind) -> None:
    sig = s7_review.signals(rows)
    assert s7_review.qualifies(sig) is ok and s7_review.session_type(sig) == kind


def test_sample_is_deterministic_and_about_ten_percent() -> None:
    ids = [f"s{i}" for i in range(2000)]
    picked = [i for i in ids if s7_review.sampled(i)]
    assert picked == [i for i in ids if s7_review.sampled(i)]
    assert 140 <= len(picked) <= 260


def _cli_transcript(tmp_path: Path, monkeypatch) -> str:
    sdk = str(uuid.uuid4())
    folder = tmp_path / "claude" / "projects" / "-w"
    folder.mkdir(parents=True)
    (folder / f"{sdk}.jsonl").write_text(json.dumps({"type": "summary"}) + "\n"
                                         + json.dumps({"cwd": "/w", "type": "user"}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return sdk


def _conversation(env) -> list[str]:
    ids = []
    for i, text in enumerate(["我们来 brainstorm 一下首页", "不对，以后都要先出 mockup 再写代码",
                              "好，就按第二个方向"], 1):
        ids += _record_person_message(env, f"m{i}", text)[:1]
    asyncio.run(env["db"].messages.put({"id": "r1", "session_id": SESSION, "role": "assistant",
                                        "content": [{"type": "text", "text": "好的。"}]}))
    return ids


def _agent_correction(anchor: str) -> dict:
    return {"kind": "cognition", "type": "correction", "scope": "agent",
            "title": "Mockup before code", "what": "Show a mockup before writing UI code.",
            "why": "The user corrected me for coding first.",
            "applicability": "When asked to change a page's layout.", "evidence": [anchor]}


def test_review_session_end_to_end(cdf_env, tmp_path, monkeypatch) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    seen = {}

    def runner(sdk_id, cwd, prompt, schema):
        seen.update(sdk=sdk_id, cwd=cwd, prompt=prompt, schema=schema)
        return {"units": [_agent_correction(f"event:{users[1]}"),
                          _agent_correction("event:not-in-index")]}

    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=runner)
    assert out["status"] == "reviewed" and out["units"] == 1 and out["invalid"] == 1
    assert seen["sdk"] == sdk and seen["cwd"] == "/w"
    from core.ingestion.sources import extract_llm
    assert seen["schema"] == extract_llm.flat_unit_schema(s7_review.ALLOWED_KINDS)
    prompt = seen["prompt"]
    for needle in ("After-action review", "brainstorming", "decks", f"event:{users[1]}",
                   "以后都要先出 mockup", "m2", 'scope "agent"'):
        assert needle in prompt, needle

    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        inbox = conn.execute("SELECT source_v2 FROM ingestion_inbox").fetchall()
        attempt = conn.execute("SELECT evidence_range, status FROM extraction_attempts "
                               "WHERE source='S7'").fetchone()
    finally:
        conn.close()
    assert [r[0] for r in inbox] == ["S7"]
    rng = json.loads(attempt[0])
    assert attempt[1] == "ok" and rng["session_type"] == "conversation" and "sampled" in rng

    again = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=out["reviewed_to"],
                                     runner=lambda *a: pytest.fail("nothing new to review"))
    assert again["status"] == "nothing_new"


def test_review_needs_a_resumable_session(cdf_env, tmp_path, monkeypatch) -> None:
    _conversation(cdf_env)
    fail = lambda *a: pytest.fail("must not resume")  # noqa: E731
    assert s7_review.review_session(SESSION, sdk_session_id=None, watermark=None,
                                    runner=fail)["status"] == "unresumable"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
    assert s7_review.review_session(SESSION, sdk_session_id=str(uuid.uuid4()), watermark=None,
                                    runner=fail)["status"] == "unresumable"


def test_job_falls_back_to_s2_main_and_keeps_the_watermark(cdf_env, tmp_path, monkeypatch) -> None:
    from core.ingestion import s2_jobs, worker
    from core.ingestion.api import connect_rw
    from core.ingestion.sources import s2_session
    _conversation(cdf_env)
    ran = []
    monkeypatch.setattr(s2_session, "extract_session_sync",
                        lambda sid, passes=(): ran.append(passes) or {})
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    s2_jobs.request(SESSION, "save_button")
    conn = connect_rw()
    try:
        job = s2_jobs.claim(conn)
        out = worker.run_session_job(conn, job, runner=lambda *a: pytest.fail("no sdk id"))
        assert out["status"] == "unresumable" and ran == [("user_utterance",), ("main",)]

        sdk = _cli_transcript(tmp_path, monkeypatch)
        ran.clear()
        monkeypatch.setattr(s7_review, "sampled", lambda sid: False)
        out = worker.run_session_job(conn, {**job, "sdk_session_id": sdk},
                                     runner=lambda *a: {"units": []})
        assert out["status"] == "reviewed" and ran == [("user_utterance",)]
        assert conn.execute("SELECT reviewed_to FROM ingestion_s2_jobs WHERE session_id=?",
                            (SESSION,)).fetchone()[0] == out["reviewed_to"]
    finally:
        conn.close()


@pytest.mark.parametrize("entailed", [True, False])
def test_agent_self_correction_reaches_evolution_only_when_entailed(
        cdf_env, tmp_path, monkeypatch, entailed) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    # the session also read a web page: everything it says is tainted by lineage
    asyncio.run(cdf_env["db"].messages.put({
        "id": "r2", "session_id": SESSION, "role": "assistant",
        "content": [{"type": "tool_use", "name": "WebFetch", "input": {"url": "x"}},
                    {"type": "tool_result", "content": "some page"}]}))
    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                             runner=lambda *a: {"units": [_agent_correction(f"event:{users[1]}")]})
    rows = _drain(cdf_env, Judge(entailed=entailed))
    assert rows
    context = cdf_env["workspace"] / ".context"
    landed = any("Mockup before code" in (context / f"{n}.md").read_text(encoding="utf-8")
                 or "mockup before writing UI code" in (context / f"{n}.md").read_text(encoding="utf-8")
                 for n in ("EVOLUTION", "MEMORY"))
    assert landed is entailed, [(r["state"], r.get("destination"), r.get("reason")) for r in rows]


def test_button_reviews_below_the_threshold(cdf_env, tmp_path, monkeypatch) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    _record_person_message(cdf_env, "only", "这个周报的结构以后都这样写")
    asyncio.run(cdf_env["db"].messages.put({"id": "r0", "session_id": SESSION, "role": "assistant",
                                            "content": "好"}))
    monkeypatch.setattr(s7_review, "_rule_patterns", lambda: ())   # no rule signal either
    asked = []
    run = lambda *a: asked.append(1) or {"units": []}  # noqa: E731
    assert s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                                    runner=run)["status"] == "below_threshold"
    assert s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, force=True,
                                    runner=run)["status"] == "reviewed"
    assert asked == [1]


def test_sdk_runner_forks_without_tools_and_drops_the_fork(tmp_path, monkeypatch) -> None:
    """The options the design depends on (§16): resume a fork, no tools at all
    (``tools=[]``; ``allowed_tools=[]`` would allow every tool), structured output; the
    fork's own transcript is removed; a missing conversation is Unresumable."""
    import claude_agent_sdk
    original = _cli_transcript(tmp_path, monkeypatch)
    fork = str(uuid.uuid4())
    fork_file = tmp_path / "claude" / "projects" / "-w" / f"{fork}.jsonl"
    fork_file.write_text("{}\n")
    seen = {}
    monkeypatch.setattr("jobs.executor._get_aws_credentials", lambda: {})
    monkeypatch.setattr("jobs.executor._resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr("core.ingestion.llm_usage.record", lambda usage: seen.setdefault("usage", usage))

    def result(**over):
        base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                    num_turns=1, session_id=fork, usage={"input_tokens": 5, "output_tokens": 2},
                    structured_output={"units": []})
        return claude_agent_sdk.ResultMessage(**{**base, **over})

    def fake_query(prompt, options):
        seen["options"] = options

        async def gen():
            yield seen.pop("reply", result())
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    assert s7_review.sdk_runner(original, "/w", "p", {"type": "object"}) == {"units": []}
    opts = seen["options"]
    assert opts.resume == original and opts.fork_session is True and opts.tools == []
    assert opts.output_format == {"type": "json_schema", "schema": {"type": "object"}}
    assert opts.cwd == "/w" and not fork_file.exists()
    assert (tmp_path / "claude" / "projects" / "-w" / f"{original}.jsonl").exists()
    assert seen["usage"] == {"input_tokens": 5, "output_tokens": 2}

    seen["reply"] = result(is_error=True, errors=["No conversation found with session ID"],
                           session_id=original)
    with pytest.raises(s7_review.Unresumable):
        s7_review.sdk_runner(original, "/w", "p", {"type": "object"})


def test_review_health_reports_miss_rate_per_session_type(cdf_env) -> None:
    """A sampled review's S2 main units: written = missed, reinforced / already_exists
    = covered (§12). Unsampled sessions do not count."""
    from core.ingestion import queries
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        def attempt(aid, sid, kind, sampled_flag, units):
            conn.execute("INSERT INTO extraction_attempts(attempt_id,source,evidence_range,"
                         "extractor_version,started_at,status,units_emitted) VALUES (?,?,?,?,?,?,?)",
                         (aid, "S7", json.dumps({"session_id": sid, "session_type": kind,
                                                 "sampled": sampled_flag}),
                          "v", "2099-01-01T00:00:00", "ok", units))

        def unit(uid, sid, state, reason=None, source="S2", passname="main", dest=None):
            conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,"
                         "payload_schema,content_hash,envelope,project_snapshot,created_at,source_v2,"
                         "extraction_pass) VALUES (?,?,?,?,'session','{}','v','h','{}','{}',"
                         "'2099-01-01T00:00:00',?,?)", (uid, uid, source, sid, source, passname))
            conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,reason,"
                         "destination,updated_at) VALUES (?,?,'{}',?,?,?,'2099-01-01T00:00:00')",
                         (uid + ".u", uid, state, reason, dest))

        attempt("a1", "s-code", "code", True, 3)
        attempt("a2", "s-chat", "conversation", False, 2)
        unit("c1", "s-code", "written")
        unit("c2", "s-code", "reinforced")
        unit("c3", "s-code", "rejected", "already_exists")
        unit("c4", "s-code", "rejected", "quality")       # not final evidence either way
        unit("x1", "s-chat", "written")                     # not sampled: ignored
        unit("e1", "s-chat", "written", source="S7", dest='{"store": "EVOLUTION"}')
        out = queries.review_health(conn, "2000-01-01")
    finally:
        conn.close()
    code = out["by_type"]["code"]
    assert (code["reviews"], code["units"], code["sampled"]) == (1, 3, 1)
    assert (code["missed"], code["covered"], code["miss_rate"]) == (1, 2, 0.333)
    assert out["by_type"]["conversation"]["miss_rate"] is None
    assert out["agent_self_written"] == 1


def test_agent_pitfall_grounded_in_a_measured_result_reaches_evolution(
        cdf_env, tmp_path, monkeypatch) -> None:
    """§9: a measured result (here a test run's exit code and failure count) is enough
    grounding; the entailment judge sees its value, read back from the transcript."""
    sdk = _cli_transcript(tmp_path, monkeypatch)
    _conversation(cdf_env)
    asyncio.run(cdf_env["db"].messages.put({
        "id": "r3", "session_id": SESSION, "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}},
                    {"type": "tool_result", "content": json.dumps({"exit_code": 1, "failed": 3})}]}))
    seen = {}

    def runner(sdk_id, cwd, prompt, schema):
        anchor = next(line.split()[1] for line in prompt.splitlines()
                      if "(measured exit_code)" in line)
        seen["prompt_has_value"] = "(measured exit_code): 1" in prompt
        return {"units": [{
            "kind": "cognition", "type": "pitfall", "scope": "agent",
            "title": "Run the suite before claiming done",
            "what": "Run the test suite before saying a change is finished.",
            "why": "I said it was done and the run then failed.",
            "applicability": "When about to report a code change as finished.",
            "evidence": [anchor]}]}

    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=runner)
    assert seen["prompt_has_value"]

    class Seeing(Judge):
        def entailment(self, text, *rest):
            seen["grounding"] = text
            return super().entailment(text, *rest)

    _drain(cdf_env, Seeing(entailed=True))
    assert "measured exit_code = 1" in seen["grounding"]
    evolution = (cdf_env["workspace"] / ".context" / "EVOLUTION.md").read_text(encoding="utf-8")
    memory = (cdf_env["workspace"] / ".context" / "MEMORY.md").read_text(encoding="utf-8")
    assert "Run the test suite before saying a change is finished" in evolution + memory


def test_review_health_counts_what_the_user_withdrew(cdf_env) -> None:
    from core.ingestion import queries
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        for uid, anchor in (("k1", "unit:kept"), ("w1", "unit:gone")):
            conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,"
                         "payload_schema,content_hash,envelope,project_snapshot,created_at,source_v2,"
                         "extraction_pass) VALUES (?,?,'S7','s','session','{}','v','h','{}','{}',"
                         "'2099-01-01T00:00:00','S7','main')", (uid, uid))
            conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,"
                         "destination,updated_at) VALUES (?,?,'{}','written','{\"store\": \"MEMORY\"}',"
                         "'2099-01-01T00:00:00')", (uid + ".u", uid))
            conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,"
                         "anchors_present,step,created_at,updated_at) VALUES "
                         "(?,'ingestion_unit',?,'MEMORY.md',?,'done','t','t')",
                         ("op-" + uid, uid + ".u", json.dumps([anchor])))
        conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,target_path,relations,step,"
                     "created_at,updated_at) VALUES ('r1','user_redact','MEMORY.md',?,'done','t','t')",
                     (json.dumps([{"redact_anchor": "unit:gone"}]),))
        out = queries.review_health(conn, "2000-01-01")
    finally:
        conn.close()
    assert (out["agent_self_written"], out["agent_self_withdrawn"],
            out["agent_self_withdrawn_rate"]) == (2, 1, 0.5)
