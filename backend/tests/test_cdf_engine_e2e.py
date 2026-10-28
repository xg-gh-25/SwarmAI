"""CDF stage-B end to end: enqueue -> owner worker -> journaled writer -> Flow queries.

Methodology: a temp SWARM_DATA_DIR and a temp workspace (a real DDD copied from the
frozen holdout corpus snapshot when readable, else the DDD skeleton), real enqueue,
real owner lock, real worker.run_once, real writer, with a typed in-process judge
instead of Bedrock. Key invariants:
- every candidate reaches a terminal state; written units carry ``cand:<unit_id>``;
- every file outside the decided destinations is byte-identical before and after;
- a second run of the same candidates is a duplicate (no second write);
- the Flow read API sees the same states without writing the DB.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

SNAPSHOT = Path("/Users/gawan/.kiro/crew/scratch/runtime-a31c1283/cdf_holdout/corpus_snapshot/Projects/SwarmAI")
QUALITY = {"atomic": True, "valuable": True, "self_contained": True, "durable": True,
           "evidenced": True, "internally_consistent": True, "concise": True}
ANSWERS = {
    "Always reply to Gawan in Chinese.": {
        "shape": "UserCross", "content_form": "rule", "type": "guideline", "user_kind": "preference",
        "applicability": "when replying", "claims": ["reply in Chinese"], "quality": QUALITY, "reroute_to": None},
    "Run CDF tests in chunks with a perl alarm cap because pytest hangs past the Bash timeout.": {
        "shape": "ProjectSingle", "project": "SwarmAI", "topic": "lesson", "content_form": "rule",
        "type": "guideline", "applicability": "when running CDF tests",
        "claims": ["chunk tests with perl alarm"], "quality": QUALITY, "reroute_to": None},
    "Maybe we should try a vector DB someday.": {
        "shape": "ProjectSingle", "project": "SwarmAI", "topic": "decision", "content_form": "rule",
        "type": "guideline", "applicability": "someday", "claims": ["try a vector DB"],
        "quality": dict(QUALITY, evidenced=False), "reroute_to": None},
}


class Judge:
    """Typed judge keyed by candidate text; card 3 sees no hit, card 3b unused."""

    def __init__(self) -> None:
        from scripts.ingestion_replay import RecordJudge
        self._record = RecordJudge

    def _for(self, text: str):
        for key, answer in ANSWERS.items():
            if key in text:
                return self._record({"card1": answer, "card3": [], "card3b": None})
        raise AssertionError(f"unexpected text {text!r}")

    def card1(self, text, projects, context=""):
        return self._for(text).card1(text, projects, context)

    def card3(self, text, hits):
        """Every retrieved hit is judged unrelated, so the unit is new (action add)."""
        reply = json.dumps([{"anchor": h["anchor"], "overlap": "unrelated", "same_topic": False,
                             "claim_verdicts": [], "scope_relation": "disjoint"} for h in hits])
        return self._record({"card3": [reply]}).card3(text, hits)

    def card3b(self, old, candidate):  # never reached: nothing is replaced
        raise AssertionError("card3b must not be called for unrelated hits")


def _digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".lock"}  # md_lock sidecars


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data, ws = tmp_path / "data", tmp_path / "ws"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    (ws / ".context").mkdir(parents=True)
    for name in ("MEMORY.md", "EVOLUTION.md", "STEERING.md", "USER.md", "TOOLS.md"):
        (ws / ".context" / name).write_text(f"# {name[:-3]}\n", encoding="utf-8")
    for folder in ("Notes", "Library", "Learned", "Inbox"):
        (ws / "Knowledge" / folder).mkdir(parents=True)
    try:
        has_snapshot = SNAPSHOT.is_dir()
    except OSError:  # another session's scratch: not readable from this sandbox
        has_snapshot = False
    if has_snapshot:
        shutil.copytree(SNAPSHOT, ws / "Projects" / "SwarmAI")
        for path in [ws / "Projects" / "SwarmAI", *(ws / "Projects" / "SwarmAI").rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)  # the frozen snapshot is read-only
    else:
        und = ws / "Projects" / "SwarmAI" / "2-understanding"
        und.mkdir(parents=True)
        skeleton = Path(__file__).resolve().parents[1] / "templates" / "ddd-skeleton"
        for doc in ("PROJECT.md", "PRODUCT.md", "TECH.md", "IMPROVEMENT.md"):
            # a new brain starts from the one skeleton (new-brain design §5.1)
            shutil.copyfile(skeleton / doc, und / doc)
    from core.session_unit import is_any_chat_streaming  # noqa: F401  (public wrapper exists)
    monkeypatch.setattr("core.session_unit.is_any_chat_streaming", lambda: False)
    monkeypatch.setattr("core.ingestion.worker.ingestion_admission",
                        lambda lane, **kw: type("A", (), {"run": True})())
    monkeypatch.setattr("core.ingestion.inbox.wake", lambda *a, **k: None, raising=False)
    return data, ws


def _enqueue_all():
    from core.ingestion import api
    env_user = {"source_event_ids": ["e1"], "evidence": "user_explicit", "tainted": False}
    env_inf = {"source_event_ids": ["e2"], "evidence": "inferred", "tainted": False}
    out = [
        api.enqueue("remember", "msg-1", "0", {"kind": "remember", "text": "Always reply to Gawan in Chinese."},
                    envelope=env_user),
        api.enqueue("distillation", "sess-1", "lesson:perl", {"kind": "distillation", "lessons": [
            {"text": "Run CDF tests in chunks with a perl alarm cap because pytest hangs past the Bash timeout.",
             "type_hint": "guideline"}]}, envelope=dict(env_inf, evidence="run_lesson"),
                    project_snapshot={"source_repo": "swarmai", "repo_projects": {"swarmai": ["SwarmAI"]}}),
        api.enqueue("distillation", "sess-1", "lesson:vector", {"kind": "distillation", "lessons": [
            {"text": "Maybe we should try a vector DB someday.", "type_hint": "guideline"}]}, envelope=env_inf),
    ]
    return out


def test_cdf_end_to_end(env):
    data, ws = env
    from core.ingestion import api, queries
    from core.ingestion.owner import acquire
    from core.ingestion.worker import IngestionWorker
    from core.ingestion.writer import recover

    results = _enqueue_all()
    assert [r.status for r in results] == ["queued"] * 3, results
    before = _digest(ws)

    owner = acquire()
    assert owner is not None and acquire() is None  # single owner
    try:
        conn = api.connect_rw()
        owner.adopt(conn)
        recover(conn)
        worker = IngestionWorker(owner, ws, judge_factory=Judge)
        for _ in range(20):
            if not worker.run_once(conn):
                break
        units = {r["unit_id"]: dict(r) for r in conn.execute("SELECT * FROM ingestion_units")}
        cands = {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM ingestion_inbox")}
        conn.close()
    finally:
        owner.release()

    assert set(cands.values()) == {"done"}, (cands, [(u["unit_id"], u["state"], u["reason"], u["error_class"]) for u in units.values()])
    states = sorted(u["state"] for u in units.values())
    assert states.count("written") == 2 and states.count("rejected") == 1, [(u["unit_id"], u["state"], u["reason"]) for u in units.values()]
    rejected = next(u for u in units.values() if u["state"] == "rejected")
    assert rejected["reason"] == "speculative"

    after = _digest(ws)
    changed = {p for p in after if before.get(p) != after[p]}
    ddd = [p for p in changed if p.startswith("Projects/SwarmAI/2-understanding/")]
    assert ".context/STEERING.md" in changed and len(ddd) == 1 and len(changed) == 2, changed
    for unit in units.values():
        if unit["state"] == "written":
            dest = json.loads(unit["destination"])
            hit = [p for p in changed if f"cand:{unit['unit_id']}" in (ws / p).read_text(encoding="utf-8")]
            assert hit, (unit["unit_id"], dest)
    for p in before:
        if p not in changed:
            assert before[p] == after[p], p
    assert "Always reply to Gawan in Chinese." in (ws / ".context/STEERING.md").read_text(encoding="utf-8")

    # idempotent re-submit: nothing new
    assert [r.status for r in _enqueue_all()] == ["duplicate"] * 3

    # Flow read side sees the same thing and does not write the DB
    db = api.ingestion_db_path()
    h0 = hashlib.sha256(db.read_bytes()).hexdigest()
    overview = queries.flow_overview("24h")
    assert json.dumps(overview, default=str).count("written") >= 1
    page = queries.list_items({}, 1)
    assert len(page["items"] if isinstance(page, dict) else page) == 3
    assert hashlib.sha256(db.read_bytes()).hexdigest() == h0
