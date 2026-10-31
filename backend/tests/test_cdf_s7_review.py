"""CDF S7 after-session review (core/ingestion/sources/s7_review.py; worker.run_session_job).
Design: docs/design/2026-10-30-cdf-agent-decides.md (D1, D3, D4, D10, D16).

What is tested:
- no threshold: every session a person wrote in is reviewed, one with no user message at
  all is passed over with no model call; a stretch after the person's last message is
  reviewed against that message; the session type is a fact.
- turn one forks the session with its own model and carries the evidence index (user
  messages in full, labelled by kind) and the record since the last review; units citing
  the index are enqueued as S7, others recorded invalid; no unit cap; nothing new, no call.
- turn two, in the review's own conversation: each unit with the entries it may relate
  to; the declarations it returns are set on the units (a malformed one stands for nothing).
- a session that cannot be resumed is reviewed from its record alone; a long record is
  reviewed in windows; the review's conversation file is removed.
- the job runs the review only, and moves the watermark.
- the user's words reach EVOLUTION only through a verbatim quote; a measurement opens it.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from tests.test_cdf_v2_e2e import (  # noqa: F401  (cdf_env is a fixture)
    SESSION, _drain, _record_person_message, cdf_env,
)
from core.ingestion.sources import s7_review


def _user(i, text="ok"):
    return {"id": f"u{i}", "role": "user", "content": text, "created_at": f"2026-10-29T00:00:{i:02d}"}


def _tool(i, name="Read", **inp):
    return {"id": f"a{i}", "role": "assistant", "created_at": f"2026-10-29T00:01:{i:02d}",
            "content": [{"type": "tool_use", "name": name, "input": inp}]}


@pytest.mark.parametrize("rows,kind", [
    ([_user(1)], "conversation"),
    ([_user(1), _tool(1, "Write", file_path="/w/deck.md")], "files"),
    ([_user(1), _tool(1, "Edit", file_path="/w/app.py")], "code"),
])
def test_the_session_type_is_a_fact(rows, kind) -> None:
    sig = s7_review.signals(rows)
    assert s7_review.session_type(sig) == kind and sig["user_messages"] == 1
    assert not hasattr(s7_review, "qualifies") and not hasattr(s7_review, "MAX_UNITS")


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


def _agent_correction(anchor: str, quote: str | None = "以后都要先出 mockup 再写代码") -> dict:
    return {"kind": "cognition", "type": "correction", "scope": "agent",
            "title": "Mockup before code", "what": "Show a mockup before writing UI code.",
            "why": "The user corrected me for coding first.",
            "applicability": "When asked to change a page's layout.", "evidence": [anchor],
            "user_quote": quote}


class Runner:
    """The two review turns, recorded; turn one answers *units*, turn two *relations*."""

    def __init__(self, units, relations=None, fork="fork-1"):
        self.units, self.relations, self.fork = units, relations, fork
        self.calls: list[dict] = []

    def __call__(self, resume, cwd, prompt, schema, *, fork, model):
        self.calls.append(dict(resume=resume, cwd=cwd, prompt=prompt, schema=schema, fork=fork, model=model))
        if schema is s7_review.RELATION_SCHEMA:
            return (self.relations or {"relations": []}), resume
        return {"cognition_units": self.units}, self.fork


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(s7_review, "session_model", lambda sid, sdk: "session-model[1m]")


def test_review_session_two_turns(cdf_env, tmp_path, monkeypatch, model) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    memory = cdf_env["workspace"] / ".context" / "MEMORY.md"
    memory.write_text(memory.read_text(encoding="utf-8").replace(
        "## Guidelines\n", "## Guidelines\n\n- [guideline] **Mockup first** Show a mockup before writing UI code. <!-- cand:cand-m.unit -->\n", 1),
        encoding="utf-8")
    from core.ingestion import relations_v2
    entry = next(e for e in relations_v2.pool(cdf_env["workspace"], None) if "Mockup first" in e.text)
    dropped = []
    monkeypatch.setattr(s7_review, "_drop_fork", lambda fork, original: dropped.append((fork, original)))
    run = Runner([_agent_correction(f"event:{users[1]}"), _agent_correction("event:not-in-index")],
                 {"relations": [{"unit": 1, "relation": "same", "target": entry.shown()}]})
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=run)
    assert out["status"] == "reviewed" and out["units"] == 1 and out["invalid"] == 1
    first, second = run.calls
    assert (first["resume"], first["fork"], first["cwd"], first["model"]) == (sdk, True, "/w", "session-model[1m]")
    from core.ingestion.sources import extract_llm
    assert first["schema"] == extract_llm.unit_schema(s7_review.ALLOWED_KINDS)
    assert "event_units" in first["schema"]["properties"]
    prompt = first["prompt"]
    index, record = prompt.split("<session-record>")
    for needle in ("After-action review", "brainstorming", "user_quote",
                   f'event:{users[1]} (user message): "不对，以后都要先出 mockup 再写代码"'):
        assert needle in index, needle               # the index quotes each message in full
    assert "好的。" in record and f"event:{users[1]}" in record      # the record, with its anchors
    # only what may be cited carries an anchor: the agent's own words and tool output
    # are read, never cited (the check below refuses them, so offering them loses units)
    shown = [json.loads(line) for line in record.split("</session-record>")[0].strip().splitlines()]
    citable = {line.split()[1] for line in index.splitlines() if line.startswith("- event:")}
    assert any(item["anchors"] for item in shown)
    assert all(set(item["anchors"]) <= citable for item in shown), shown
    assert [item["anchors"] for item in shown if item.get("role") == "assistant"] == [[]]
    assert (second["resume"], second["fork"], second["schema"]) == ("fork-1", False, s7_review.RELATION_SCHEMA)
    assert entry.shown() in second["prompt"] and "Unit 1:" in second["prompt"]
    assert dropped == [("fork-1", sdk)]

    from core.ingestion.api import connect_rw
    conn = connect_rw()
    try:
        payloads = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM ingestion_inbox WHERE source_v2='S7'")]
        attempt = conn.execute("SELECT evidence_range, status FROM extraction_attempts WHERE source='S7'").fetchone()
    finally:
        conn.close()
    assert [p.get("same") for p in payloads] == [entry.shown()]
    rng = json.loads(attempt[0])
    assert attempt[1] == "ok" and rng["forked"] is True and rng["model"] == "session-model[1m]"

    again = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=out["reviewed_to"],
                                     runner=lambda *a, **k: pytest.fail("nothing new to review"))
    assert again["status"] == "nothing_new"


def test_one_user_message_is_enough_and_none_needs_no_call(cdf_env, tmp_path, monkeypatch, model) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    asyncio.run(cdf_env["db"].messages.put({"id": "r0", "session_id": SESSION, "role": "assistant",
                                            "content": "好"}))
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                                   runner=lambda *a, **k: pytest.fail("no user message"))
    assert (out["status"], out["units"]) == ("reviewed", 0)
    _record_person_message(cdf_env, "only", "这个周报的结构以后都这样写")
    run = Runner([])
    assert s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=out["reviewed_to"],
                                    runner=run)["status"] == "reviewed"
    assert len(run.calls) == 1                     # no unit: no second turn


def test_every_unit_is_enqueued(cdf_env, tmp_path, monkeypatch, model) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    many = [{**_agent_correction(f"event:{users[1]}"), "title": f"Lesson {i}"} for i in range(19)]
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=Runner(many))
    assert out["units"] == 19 and out["invalid"] == 0


def test_an_unresumable_session_is_reviewed_from_its_record(cdf_env, tmp_path, monkeypatch, model) -> None:
    users = _conversation(cdf_env)
    run = Runner([_agent_correction(f"event:{users[1]}")], fork="record-1")
    out = s7_review.review_session(SESSION, sdk_session_id=None, watermark=None, runner=run)
    assert out["status"] == "reviewed" and out["units"] == 1
    assert (run.calls[0]["resume"], run.calls[0]["fork"]) == (None, True)
    assert run.calls[1]["resume"] == "record-1"
    # a fork the CLI refuses (gone, too long): the same review from the record
    sdk = _cli_transcript(tmp_path, monkeypatch)
    tries = []

    def refusing(resume, cwd, prompt, schema, *, fork, model):
        tries.append((resume, fork))
        if resume == sdk:
            raise s7_review.Unresumable("No conversation found")
        return {"cognition_units": []}, "record-2"
    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=refusing)
    assert tries == [(sdk, True), (None, False)]


def test_a_long_record_is_reviewed_in_windows(cdf_env, tmp_path, monkeypatch, model) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    for i in range(6):
        _record_person_message(cdf_env, f"w{i}", f"message {i} " + "x" * 300)
    monkeypatch.setattr(s7_review, "WINDOW_CHARS", 900)
    run = Runner([])
    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=run)
    parts = [c["prompt"] for c in run.calls]
    assert len(parts) >= 2 and "part 1 of" in parts[0] and f"part {len(parts)} of {len(parts)}" in parts[-1]
    assert all('"message 5 ' in "".join(parts) for _ in [0])


def test_declarations_the_contract_refuses_stand_for_nothing() -> None:
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    guideline = SOURCE_UNIT.validate_python({**_agent_correction("event:e"), "type": "guideline"})
    correction = SOURCE_UNIT.validate_python(_agent_correction("event:e"))
    ref = "0123abcd | - [guideline] **Mockup first** Show a mockup"
    out = s7_review.apply_relations([guideline, correction], {"relations": [
        {"unit": 1, "relation": "new", "repeats": ref},            # repeats: corrections only
        {"unit": 2, "relation": "adds", "target": ref, "repeats": ref, "overrides": [ref]},
        {"unit": 9, "relation": "same", "target": ref},           # names no unit
        {"unit": 1, "relation": "same", "target": "not a ref"},   # malformed
    ]})
    assert out[0].repeats is None and out[0].same is None
    assert (out[1].adds, out[1].repeats, out[1].overrides) == (ref, ref, [ref])


def test_the_job_runs_the_review_only_and_moves_the_watermark(cdf_env, tmp_path, monkeypatch, model) -> None:
    from core.ingestion import s2_jobs, worker
    from core.ingestion.api import connect_rw
    _conversation(cdf_env)
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    s2_jobs.request(SESSION, "save_button")
    conn = connect_rw()
    try:
        job = s2_jobs.claim(conn)
        out = worker.run_session_job(conn, job, runner=Runner([]))
        assert out["status"] == "reviewed"
        assert conn.execute("SELECT reviewed_to FROM ingestion_s2_jobs WHERE session_id=?",
                            (SESSION,)).fetchone()[0] == out["reviewed_to"]
    finally:
        conn.close()
    from core.ingestion.sources import s2_session
    assert not hasattr(s2_session, "extract_session_sync")


def test_sdk_runner_options_per_turn(tmp_path, monkeypatch) -> None:
    """Turn one forks the session, turn two resumes the review's own conversation, a
    record review resumes nothing; never any tool; the session's model; structured output;
    a fork the CLI refuses is Unresumable, any other error is not."""
    import claude_agent_sdk
    seen = {}
    monkeypatch.setattr("jobs.executor._get_aws_credentials", lambda: {})
    monkeypatch.setattr("jobs.executor._resolve_claude_cli", lambda: "/bin/claude")
    monkeypatch.setattr("core.ingestion.llm_usage.record", lambda usage: seen.setdefault("usage", usage))

    def result(**over):
        base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                    num_turns=1, session_id="fork-9", usage={"input_tokens": 5, "output_tokens": 2},
                    structured_output={"units": []})
        return claude_agent_sdk.ResultMessage(**{**base, **over})

    def fake_query(prompt, options):
        seen["options"] = options

        async def gen():
            yield seen.pop("reply", result())
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    assert s7_review.sdk_runner("orig", "/w", "p", {"type": "object"}, fork=True, model="m[1m]") == ({"units": []}, "fork-9")
    opts = seen["options"]
    assert (opts.resume, opts.fork_session, opts.tools, opts.model, opts.cwd) == ("orig", True, [], "m[1m]", "/w")
    assert opts.output_format == {"type": "json_schema", "schema": {"type": "object"}}
    s7_review.sdk_runner("fork-9", "/w", "p", {"type": "object"}, fork=False, model="m[1m]")
    assert (seen["options"].resume, seen["options"].fork_session) == ("fork-9", False)
    s7_review.sdk_runner(None, "/w", "p", {"type": "object"}, fork=True, model="m[1m]")
    assert (seen["options"].resume, seen["options"].fork_session) == (None, False)
    assert seen["usage"] == {"input_tokens": 5, "output_tokens": 2}
    seen["reply"] = result(is_error=True, errors=["Prompt is too long"], session_id="orig")
    with pytest.raises(s7_review.Unresumable):
        s7_review.sdk_runner("orig", "/w", "p", {"type": "object"}, fork=True, model="m")
    seen["reply"] = result(is_error=True, errors=["Prompt is too long"], session_id="fork-9")
    with pytest.raises(RuntimeError):
        s7_review.sdk_runner("fork-9", "/w", "p", {"type": "object"}, fork=False, model="m")


def test_the_review_runs_on_the_sessions_own_model(tmp_path, monkeypatch) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    path = tmp_path / "claude" / "projects" / "-w" / f"{sdk}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8"}}) + "\n")
    from core.prompt_builder import PromptBuilder
    seen = []
    monkeypatch.setattr(PromptBuilder, "resolve_model", lambda self, cfg: seen.append(cfg) or f"resolved:{cfg.get('model')}")
    from types import SimpleNamespace
    from core import session_registry
    monkeypatch.setattr(session_registry, "session_router",
                        SimpleNamespace(get_unit=lambda sid: SimpleNamespace(_model_name="live-model[1m]") if sid == "live" else None))
    assert s7_review.session_model("live", sdk) == "live-model[1m]"
    assert s7_review.session_model("gone", sdk) == "resolved:claude-opus-4-8"
    assert s7_review.session_model("gone", None) == "resolved:None"


@pytest.mark.parametrize("quote,lands", [("以后都要先出 mockup 再写代码", True),
                                         ("以后都直接写代码", False)])
def test_agent_self_correction_reaches_evolution_only_on_the_users_words(
        cdf_env, tmp_path, monkeypatch, quote, lands) -> None:
    sdk = _cli_transcript(tmp_path, monkeypatch)
    users = _conversation(cdf_env)
    # the session also read a web page: everything it says is tainted by lineage
    asyncio.run(cdf_env["db"].messages.put({
        "id": "r2", "session_id": SESSION, "role": "assistant",
        "content": [{"type": "tool_use", "name": "WebFetch", "input": {"url": "x"}},
                    {"type": "tool_result", "content": "some page"}]}))
    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None,
                             runner=lambda *a, **k: {"cognition_units": [_agent_correction(f"event:{users[1]}", quote)]})
    rows = _drain(cdf_env)
    assert rows
    context = cdf_env["workspace"] / ".context"
    landed = any("Mockup before code" in (context / f"{n}.md").read_text(encoding="utf-8")
                 or "mockup before writing UI code" in (context / f"{n}.md").read_text(encoding="utf-8")
                 for n in ("EVOLUTION", "MEMORY"))
    assert landed is lands, [(r["state"], r.get("destination"), r.get("reason")) for r in rows]


def test_agent_pitfall_grounded_in_a_measured_result_reaches_evolution(
        cdf_env, tmp_path, monkeypatch) -> None:
    """§2 K1: a measured result (here a test run's exit code and failure count) opens
    EVOLUTION; the review sees its value, read back from the transcript."""
    sdk = _cli_transcript(tmp_path, monkeypatch)
    _conversation(cdf_env)
    asyncio.run(cdf_env["db"].messages.put({
        "id": "r3", "session_id": SESSION, "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}},
                    {"type": "tool_result", "content": json.dumps({"exit_code": 1, "failed": 3})}]}))
    seen = {}

    def runner(sdk_id, cwd, prompt, schema, **_):
        if schema is s7_review.RELATION_SCHEMA:
            return {"relations": []}, sdk_id
        anchor = next(line.split()[1] for line in prompt.splitlines()
                      if "(measured exit_code)" in line)
        seen["prompt_has_value"] = "(measured exit_code): 1" in prompt
        return {"cognition_units": [{
            "kind": "cognition", "type": "pitfall", "scope": "agent",
            "title": "Run the suite before claiming done",
            "what": "Run the test suite before saying a change is finished.",
            "why": "I said it was done and the run then failed.",
            "applicability": "When about to report a code change as finished.",
            "evidence": [anchor]}]}

    s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=runner)
    assert seen["prompt_has_value"]

    _drain(cdf_env)
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





def test_work_after_the_persons_last_message_is_reviewed_against_it(cdf_env, tmp_path, monkeypatch, model) -> None:
    """A long task, or a review that comes mid-task, leaves a stretch with no user message:
    it is reviewed, and the person's last message before it is listed and citable."""
    sdk = _cli_transcript(tmp_path, monkeypatch)
    (asked,) = _record_person_message(cdf_env, "m1", "把 Swarm Out 的 repo spec 安全审一遍")[:1]
    first = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=None, runner=Runner([]))
    for i in range(3):
        asyncio.run(cdf_env["db"].messages.put({"id": f"w{i}", "session_id": SESSION, "role": "assistant",
                                                "content": [{"type": "text", "text": f"审查第 {i} 项"}]}))
    unit = {"kind": "cognition", "type": "pitfall", "scope": "project:SwarmAI",
            "title": "A missing access field makes every repo writable",
            "what": "The repo spec parser treats a missing access field as writable.",
            "applicability": "When parsing a repo spec.", "evidence": [f"event:{asked}"]}
    run = Runner([unit], {"relations": [{"unit": 1, "relation": "new"}]})
    out = s7_review.review_session(SESSION, sdk_session_id=sdk, watermark=first["reviewed_to"], runner=run)
    assert out["units"] == 1 and out["invalid"] == 0
    line = next(l for l in run.calls[0]["prompt"].splitlines() if l.startswith(f"- event:{asked}"))
    assert "before this record" in line
