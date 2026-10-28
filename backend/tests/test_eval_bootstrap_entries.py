"""The New Brain offline cases: their generator, their fixture brains and the
deterministic half of their verdict (design docs/design/2026-10-27-new-brain-initial-ddd.md
§6 and r12).

Each case is generated from one template plus one answer key
(``templates/eval/fixtures/answer-keys/<Brain>.yaml``) by ``scripts/eval_fixture_brains``.
The judge sees only the agent's answer, so what code can decide is decided here, by
``eval_runner.bootstrap_counts``, on every raw entry: sources are the material, no
cognition entry in a section nothing supports, no status log; shape refusals are
counted, not failed."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from scripts import eval_fixture_brains as efb  # noqa: E402
from scripts import eval_runner as er  # noqa: E402
from scripts.probe_ddd import _GOOD  # noqa: E402

FIXTURE = BACKEND / "templates" / "eval" / "fixtures" / "workspace"
KEYS = BACKEND / "templates" / "eval" / "fixtures" / "answer-keys"


def _cases() -> dict:
    cases = yaml.safe_load((BACKEND / "templates/eval/golden_set.yaml").read_text())["cases"]
    return {c["id"]: c for c in cases if c["id"].startswith("OFF_BRAIN_FIRST_ENTRIES")}


def _check(case_id: str) -> dict:
    return _cases()[case_id]["bootstrap_check"]


def _answer(entries) -> str:
    return "Here are the first entries.\n\n```json\n" + json.dumps(entries) + "\n```\n"


@pytest.fixture
def ws(tmp_path):
    """A copy of the fixture workspace, as the agent ran in it."""
    w = tmp_path / "workspace"
    shutil.copytree(FIXTURE, w)
    return w


def _counts(case_id: str, entries, ws: Path, text: str | None = None) -> dict:
    return er.bootstrap_counts(_check(case_id), text if text is not None else _answer(entries), ws)


def _doc(ws: Path, name: str) -> str:
    return f"file:{ws / 'inbox' / 'oncall' / name}"


ONCALL_GOOD = [
    {"kind": "cognition", "type": "pitfall", "section": "Runtime Traps",
     "title": "Never restart the settlement worker with a batch in its queue",
     "what": "A restart mid-batch posts the batch twice; drain it first.",
     "applicability": "any restart of the settlement worker", "sources": ["DOC:handbook.md"]},
    {"kind": "cognition", "type": "decision", "section": "Recent Decisions",
     "title": "Keep a single settlement worker", "what": "Only one worker runs.",
     "why": "two workers could post the same batch twice",
     "rejected_alternatives": ["two workers sharing a lock", "idempotent settlements now"],
     "sources": ["DOC:postmortem-2026-03-14.md"]},
    {"kind": "gap", "section": "What to Watch For", "title": "Strategic Priorities: no evidence yet",
     "what": "The documents state no priorities."},
]


def _oncall(ws: Path, entries=None) -> list:
    out = []
    for e in entries or ONCALL_GOOD:
        e = dict(e)
        if "sources" in e:
            e["sources"] = [_doc(ws, s[4:]) if s.startswith("DOC:") else s for s in e["sources"]]
        out.append(e)
    return out


# ── the generator: one template + one answer key per case ───────────────────

def test_the_shipped_cases_and_fixture_brains_are_what_the_generator_writes_now() -> None:
    # Fails after a change to an answer key, a fixture repo or document, the survey or
    # contract code, or the template, until `py -m scripts.eval_fixture_brains --write`.
    assert efb.drift() == []


def test_every_answer_key_has_its_case_and_nothing_else_is_generated() -> None:
    keys = {yaml.safe_load(p.read_text())["case"]["id"] for p in KEYS.glob("*.yaml")}
    assert keys == set(_cases()) == {"OFF_BRAIN_FIRST_ENTRIES_LIB", "OFF_BRAIN_FIRST_ENTRIES_ADR",
                                     "OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE"}


def test_editing_an_answer_key_or_a_fixture_puts_the_case_out_of_date(tmp_path) -> None:
    w, k = tmp_path / "ws", tmp_path / "keys"
    shutil.copytree(FIXTURE, w)
    shutil.copytree(KEYS, k)
    gs = tmp_path / "golden_set.yaml"
    shutil.copy(BACKEND / "templates/eval/golden_set.yaml", gs)
    assert efb.drift(w, k, gs) == []
    key = k / "Tokensign.yaml"
    key.write_text(key.read_text().replace("- Success Criteria\n", ""))
    assert efb.drift(w, k, gs) == ["case OFF_BRAIN_FIRST_ENTRIES_LIB"]
    readme = w / "repos/tokensign/README.md"
    readme.write_text(readme.read_text() + "\nOne more line.\n")
    assert "Projects/Tokensign/.artifacts/bootstrap/survey.json" in efb.drift(w, k, gs)


def test_write_replaces_only_the_generated_cases(tmp_path) -> None:
    gs = tmp_path / "golden_set.yaml"
    original = (BACKEND / "templates/eval/golden_set.yaml").read_text()
    gs.write_text(original)
    _files, cases = efb.build()
    changed = {cid: {**c, "title": c["title"] + " (x)"} for cid, c in cases.items()}
    efb._write_cases(changed, gs)
    after = yaml.safe_load(gs.read_text())["cases"]
    before = yaml.safe_load(original)["cases"]
    assert [c["id"] for c in after] == [c["id"] for c in before]
    for a, b in zip(after, before):
        assert a == (changed[a["id"]] if a["id"] in changed else b)


@pytest.mark.parametrize("key_file", sorted(p.name for p in KEYS.glob("*.yaml")))
def test_every_quote_in_an_answer_key_is_in_its_file(key_file) -> None:
    # A fixture edit that drops one of these makes a must-have unanswerable, a fact
    # unsupported, or a trap disappear.
    key = yaml.safe_load((KEYS / key_file).read_text())
    items = key["must_have"] + key["facts"] + key["traps"] + [
        {"where": s["path"], "quote": s["quote"]} for s in key["status_sources"]]
    for item in items:
        assert (FIXTURE / item["where"]).is_file(), item["where"]
        if item.get("quote"):
            assert item["quote"] in (FIXTURE / item["where"]).read_text(encoding="utf-8"), item


def test_each_case_names_its_material_contract_and_survey() -> None:
    from core.brain_bootstrap import apply, survey
    for c in _cases().values():
        check, prompt = c["bootstrap_check"], c["scenario"]["prompt"]
        bdir = f"Projects/{check['brain']}/.artifacts/bootstrap/"
        contract = json.loads((FIXTURE / bdir / apply.CONTRACT_FILE.name).read_text())
        assert f"`{bdir}{apply.CONTRACT_FILE.name}`" in prompt
        assert "Read entry-contract.json" in c["expected_trajectory"]
        if check.get("repo"):
            assert f"`{check['repo']}`" in prompt and (FIXTURE / check["repo"]).is_dir()
            assert contract["repos"]["owned"] == [Path(check["repo"]).name]
            data = json.loads((FIXTURE / bdir / survey.SURVEY_REL.name).read_text())
            (r,) = data["repos"]
            assert r["root"] == check["repo"] and any(d["tier"] == 1 and d.get("text") for d in r["docs"])
        else:
            assert contract["repos"]["owned"] == []
            assert not (FIXTURE / bdir / survey.SURVEY_REL.name).exists()
            for d in check["documents"]:
                assert f"`{d}`" in prompt and (FIXTURE / d).is_file()


def test_no_fixture_file_names_a_port_another_case_asks_about() -> None:
    # OFF_WRONG_PREMISE / OFF_RIGHT_PREMISE ask for the Demo port (7421) against a
    # wrong 8080; a fixture repo or document holding either number is a false lead.
    for top in ("repos", "inbox"):
        for f in (FIXTURE / top).rglob("*"):
            if f.is_file():
                text = f.read_text(encoding="utf-8")
                assert "8080" not in text and "7421" not in text, f


def test_a_malformed_check_is_refused_by_the_case_validator() -> None:
    from scripts.golden_case_validator import _bootstrap_check_errors
    good = _check("OFF_BRAIN_FIRST_ENTRIES_LIB")
    assert _bootstrap_check_errors(good) == []
    for bad in ({**good, "documents": ["x.md"]}, {**good, "repo": None},
                {**good, "empty_sections": ["Nope"]}, {**good, "repo": "../escape"},
                {**good, "status_sources": ["repos/ledgerd/notes/sprint-14.md"]}, "x"):
        assert _bootstrap_check_errors(bad), bad


# ── the deterministic half of the verdict ───────────────────────────────────

def test_entries_bootstrap_apply_would_write_pass(ws) -> None:
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", _GOOD, ws)
    assert got["failures"] == [] and got["shape_refused"] == 0 and got["entries"] == len(_GOOD)
    assert _counts("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", None, ws, _answer(_oncall(ws)))["failures"] == []


def test_a_documents_path_given_the_private_alias_is_the_same_file(ws) -> None:
    # macOS: /var/... and /private/var/... are one directory
    entries = _oncall(ws)
    real = str(ws.resolve())
    alias = real[len("/private"):] if real.startswith("/private/") else real
    if alias == real:
        pytest.skip("no /private alias on this platform")
    entries[0]["sources"] = [f"file:{alias}/inbox/oncall/handbook.md"]
    assert _counts("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", None, ws, _answer(entries))["failures"] == []


def test_a_shape_error_never_hides_a_made_up_source(ws) -> None:
    # validate() checks shape first and sources last: this entry is refused for shape,
    # and its invented source must still fail the case.
    bad = {"kind": "cognition", "type": "decision", "section": "Architecture",
           "title": "x", "what": "y", "sources": ["docs/roadmap.md"]}   # no why / rejected
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [_GOOD[0], bad], ws)
    assert got["shape_refused"] == 1
    assert any("roadmap" in f for f in got["failures"]), got


def test_a_cognition_entry_in_an_empty_section_fails_whatever_its_type(ws) -> None:
    for etype in ("decision", "model"):   # model is also a shape refusal there
        e = {"kind": "cognition", "type": etype, "section": "Strategic Priorities",
             "title": "Old tokens must keep verifying", "what": "Compatibility.",
             "why": "w", "rejected_alternatives": "none", "sources": ["CONTRIBUTING.md"]}
        got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [_GOOD[0], e], ws)
        assert any("Strategic Priorities" in f for f in got["failures"]), (etype, got)


def test_a_gap_for_an_empty_section_is_the_right_answer(ws) -> None:
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", _GOOD, ws)   # _GOOD holds three such gaps
    assert got["failures"] == []


@pytest.mark.parametrize("case_id, source", [
    ("OFF_BRAIN_FIRST_ENTRIES_LIB", "STATUS.md"),
    ("OFF_BRAIN_FIRST_ENTRIES_LIB", "tokensign:STATUS.md#Status"),
    ("OFF_BRAIN_FIRST_ENTRIES_ADR", "notes/sprint-14.md:3"),
])
def test_citing_a_status_log_fails(ws, case_id, source) -> None:
    e = dict(_GOOD[0], sources=[source])
    got = _counts(case_id, [e], ws)
    assert any("status log" in f for f in got["failures"]), got


def test_citing_the_status_update_among_the_documents_fails(ws) -> None:
    e = dict(ONCALL_GOOD[0], sources=["DOC:weekly-2026-10-20.md"])
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", None, ws, _answer(_oncall(ws, [e])))
    assert any("status log" in f for f in got["failures"]), got


@pytest.mark.parametrize("source", ["user", "url:https://example.com/x", "docs/design.md",
                                    "file:/etc/hosts", "FIXTURE:Projects/Oncall/.artifacts/bootstrap/entry-contract.json",
                                    "FIXTURE:repos/tokensign/README.md"])
def test_a_brain_without_a_repo_cites_only_its_documents(ws, source) -> None:
    if source.startswith("FIXTURE:"):
        source = f"file:{ws / source[8:]}"
    e = dict(ONCALL_GOOD[0], sources=[source])
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", None, ws, _answer(_oncall(ws, [e])))
    assert got["failures"], source


@pytest.mark.parametrize("source", ["user", "url:https://example.com/x", "file:/etc/hosts",
                                    "docs/roadmap.md", "repos/tokensign/docs/design.md"])
def test_a_repo_brain_cites_only_files_of_its_repo(ws, source) -> None:
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [dict(_GOOD[0], sources=[source])], ws)
    assert got["failures"], source


def test_a_shape_refusal_alone_is_counted_not_failed(ws) -> None:
    e = dict(_GOOD[2], applicability=["not", "text"])   # a pitfall whose trigger is not text
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [_GOOD[0], e], ws)
    assert got["failures"] == [] and got["shape_refused"] == 1 and "applicability" in got["shape_reasons"][0]


@pytest.mark.parametrize("text", ["I could not find anything.", "```json\n{\"entries\": []}\n```",
                                  "```json\n[1, 2\n```"])
def test_no_entries_fails(ws, text) -> None:
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", None, ws, text)
    assert got["failures"] and "no JSON list" in got["failures"][0]


def test_a_list_the_judge_cannot_see_whole_fails(ws) -> None:
    text = "x" * (er._JUDGE_TEXT_MAX - 50) + _answer(_GOOD[:1])
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", None, ws, text)
    assert any("the judge reads only" in f for f in got["failures"])


def test_a_full_twelve_entry_answer_fits_the_judge_window(ws) -> None:
    sentence = "One ordinary sentence of about a hundred characters that says what the entry is about here. "
    entries = [dict(e, title=f"{e['title']} ({i})", what=sentence * 3)
               for i, e in enumerate((_GOOD * 2)[:12])]
    answer = _answer(entries)
    assert 4000 < len(answer) < er._JUDGE_TEXT_MAX
    assert _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", None, ws, answer)["failures"] == []


@pytest.mark.parametrize("text", [
    "```JSON\n" + json.dumps(_GOOD) + "\n```",
    "```json\n" + json.dumps({"entries": _GOOD}) + "```",
    "draft:\n```json\n[{\"kind\": \"x\"}]\n```\nfinal:\n```json\n" + json.dumps(_GOOD) + "\n```",
])
def test_the_last_json_block_is_read_however_it_is_fenced(ws, text) -> None:
    assert _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", None, ws, text)["failures"] == []


# ── how the deterministic half combines with the trajectory and the judge ──

def _run_case(case_id: str, final_text_of, judge: dict, steps: list[str]):
    """Run the real eval_trajectory_capture with the agent and the judge stubbed."""
    from core.golden_set_store import PRODUCT_ORIGIN
    case = {**_cases()[case_id], "_origin": PRODUCT_ORIGIN}

    def fake_run(prompt, allowed_tools=None, timeout=None, system=None, workspace=None):
        return steps, final_text_of(workspace), ""
    with mock.patch("scripts.scenario_runner.run_scenario_raw", fake_run), \
         mock.patch.object(er, "_judge_decision_direction", lambda c, t: judge):
        return er.eval_trajectory_capture(case)


_LIB_STEPS = ['Read {"file_path": "/w/Projects/Tokensign/.artifacts/bootstrap/entry-contract.json"}',
              'Read {"file_path": "/w/Projects/Tokensign/.artifacts/bootstrap/survey.json"}']
_BAD = [_GOOD[0], dict(_GOOD[2], sources=["STATUS.md"])]


@pytest.mark.parametrize("judge", [
    {"status": "error", "notes": "judge down"},
    {"status": "passed", "notes": "fine"},
    {"status": "failed", "notes": "wrong"},
])
def test_a_deterministic_failure_is_failed_whatever_the_judge_said(judge) -> None:
    r = _run_case("OFF_BRAIN_FIRST_ENTRIES_LIB", lambda w: _answer(_BAD), judge, _LIB_STEPS)
    assert r["status"] == "failed" and "status log" in r["notes"]
    assert r["bootstrap_counts"]["failures"]


def test_the_counts_are_kept_when_the_trajectory_fails() -> None:
    r = _run_case("OFF_BRAIN_FIRST_ENTRIES_LIB", lambda w: _answer(_GOOD),
                  {"status": "passed", "notes": "x"}, [])
    assert r["status"] == "failed" and r["bootstrap_counts"]["entries"] == len(_GOOD)


def test_a_clean_check_leaves_the_judge_to_decide() -> None:
    for judge, want in (({"status": "passed", "notes": "ok"}, "passed"),
                        ({"status": "error", "notes": "down"}, "error")):
        r = _run_case("OFF_BRAIN_FIRST_ENTRIES_LIB", lambda w: _answer(_GOOD), judge, _LIB_STEPS)
        assert r["status"] == want and r["bootstrap_counts"]["failures"] == []


def test_the_check_sees_the_workspace_the_agent_ran_in() -> None:
    steps = ['Read {"file_path": "/w/Projects/Oncall/.artifacts/bootstrap/entry-contract.json"}',
             'Read {"file_path": "/w/inbox/oncall/handbook.md"}',
             'Read {"file_path": "/w/inbox/oncall/postmortem-2026-03-14.md"}']
    r = _run_case("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", lambda w: _answer(_oncall(w)),
                  {"status": "passed", "notes": "ok"}, steps)
    assert r["bootstrap_counts"]["failures"] == [] and r["status"] == "passed", r.get("notes")


def test_a_users_own_case_cannot_use_the_check() -> None:
    case = dict(_cases()["OFF_BRAIN_FIRST_ENTRIES_LIB"])

    def fake_run(prompt, allowed_tools=None, timeout=None, system=None, workspace=None):
        return _LIB_STEPS, _answer(_GOOD), ""
    with mock.patch("scripts.scenario_runner.run_scenario_raw", fake_run), \
         mock.patch.object(er, "_judge_decision_direction", lambda c, t: {"status": "passed", "notes": ""}):
        r = er.eval_trajectory_capture(case)
    assert r["status"] == "failed" and "only on a product case" in r["notes"]


def test_the_run_record_keeps_the_counts_for_a_product_case() -> None:
    r = {"id": "X", "status": "failed", "bootstrap_counts": {"failures": ["f"]}}
    assert er._record_detail(r, product=True)["bootstrap_counts"] == {"failures": ["f"]}
    assert "bootstrap_counts" not in er._record_detail(r, product=False)


# ── review round: raw fields read leniently, the check decides first ────────

@pytest.mark.parametrize("kind", ["Cognition", None, " cognition "])
def test_a_misspelt_or_missing_kind_does_not_escape_the_empty_section_check(ws, kind) -> None:
    e = {"type": "decision", "section": "Strategic Priorities", "title": "Old tokens keep verifying",
         "what": "Compatibility.", "why": "w", "rejected_alternatives": "none",
         "sources": ["CONTRIBUTING.md"]}
    if kind is not None:
        e["kind"] = kind
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [_GOOD[0], e], ws)
    assert any("Strategic Priorities" in f for f in got["failures"]), (kind, got)


@pytest.mark.parametrize("sources, needle", [
    ("STATUS.md", "status log"), ("docs/roadmap.md", "roadmap"),
    ([{"path": "STATUS.md"}], "not text"),
])
def test_sources_that_are_not_a_list_of_texts_are_still_checked(ws, sources, needle) -> None:
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_LIB", [dict(_GOOD[2], sources=sources)], ws)
    assert any(needle in f for f in got["failures"]), got


def test_empty_sections_are_compared_by_canonical_name(ws) -> None:
    check = {**_check("OFF_BRAIN_FIRST_ENTRIES_LIB"), "empty_sections": ["strategic priorities"]}
    e = dict(_GOOD[0], type="decision", section="Strategic Priorities", why="w",
             rejected_alternatives="none")
    got = er.bootstrap_counts(check, _answer([e]), ws)
    assert any("Strategic Priorities" in f for f in got["failures"]), got
    from scripts.golden_case_validator import _bootstrap_check_errors
    assert _bootstrap_check_errors(check)   # a shipped case must spell it as the skeleton does


def test_a_source_bootstrap_apply_would_refuse_is_never_let_by(ws) -> None:
    # resolves to a listed document, but the path itself is not a file (POSIX: missing/..)
    e = dict(ONCALL_GOOD[0], sources=[f"file:{ws}/inbox/oncall/missing/../handbook.md"])
    got = _counts("OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", None, ws, _answer([e]))
    assert got["failures"], got


def test_the_judge_is_not_asked_once_the_check_failed() -> None:
    def judge(*a):
        raise AssertionError("the judge was called")
    case_id = "OFF_BRAIN_FIRST_ENTRIES_LIB"
    from core.golden_set_store import PRODUCT_ORIGIN
    case = {**_cases()[case_id], "_origin": PRODUCT_ORIGIN}

    def fake_run(prompt, allowed_tools=None, timeout=None, system=None, workspace=None):
        return _LIB_STEPS, _answer(_BAD), ""
    with mock.patch("scripts.scenario_runner.run_scenario_raw", fake_run), \
         mock.patch.object(er, "_judge_decision_direction", judge):
        r = er.eval_trajectory_capture(case)
    assert r["status"] == "failed" and not r.get("judged") and "decision is correct" not in r["notes"]


def test_a_crash_in_the_check_is_an_error_not_the_agents_failure() -> None:
    with mock.patch.object(er, "bootstrap_counts", side_effect=RuntimeError("boom")):
        r = _run_case("OFF_BRAIN_FIRST_ENTRIES_LIB", lambda w: _answer(_GOOD),
                      {"status": "passed", "notes": ""}, _LIB_STEPS)
    assert r["status"] == "error" and "crashed" in r["notes"]


def test_two_answer_keys_with_one_case_id_are_refused(tmp_path) -> None:
    k = tmp_path / "keys"
    shutil.copytree(KEYS, k)
    dup = yaml.safe_load((k / "Ledgerd.yaml").read_text())
    dup["brain"] = "Ledgerd2"
    (k / "Ledgerd2.yaml").write_text(yaml.safe_dump(dup))
    with pytest.raises(ValueError, match="already another answer key"):
        efb._keys(k)


def test_a_file_left_by_a_removed_answer_key_is_reported(tmp_path) -> None:
    w = tmp_path / "ws"
    shutil.copytree(FIXTURE, w)
    stale = w / "Projects/Gone/.artifacts/bootstrap/entry-contract.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    assert any("Projects/Gone" in d for d in efb.drift(w))


def test_write_keeps_what_follows_a_case_and_places_a_new_one_right_after(tmp_path) -> None:
    gs = tmp_path / "golden_set.yaml"
    original = (BACKEND / "templates/eval/golden_set.yaml").read_text()
    a = original.index("- id: OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE")
    tail = "# a comment the next case owns\n- id: ZZ_AFTER\n  title: a case after the generated ones\n"
    gs.write_text(original[:a] + tail)    # KNOWLEDGE removed, a non-generated case after ADR
    _files, cases = efb.build()
    efb._write_cases(cases, gs)
    text = gs.read_text()
    assert "# a comment the next case owns\n- id: ZZ_AFTER" in text
    ids = [c["id"] for c in yaml.safe_load(text)["cases"]]
    assert ids[-4:] == ["OFF_BRAIN_FIRST_ENTRIES_LIB", "OFF_BRAIN_FIRST_ENTRIES_ADR",
                        "OFF_BRAIN_FIRST_ENTRIES_KNOWLEDGE", "ZZ_AFTER"]


def test_each_case_gives_the_agent_the_skills_own_p2b_section() -> None:
    # The case measures the skill as shipped: its P2b section verbatim, so a change to
    # the skill changes the case (and the drift test asks for a regeneration).
    from scripts.scenario_runner import _validate_canary_command
    section = efb.skill_p2b()
    assert section.startswith("#### Writing a new brain's first entries (P2b)")
    assert "A PRODUCT section holds only what" in section
    for c in _cases().values():
        assert section in c["scenario"]["prompt"]
        assert _validate_canary_command is None or _validate_canary_command(c["scenario"]["prompt"]) is None
