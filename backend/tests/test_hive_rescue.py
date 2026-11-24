"""The rescue an AgentCore supervisor runs after its backend died (core/hive_rescue; design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.5).

Each test answers one question:
- does it first upload the record entries written but not in S3, then close the open turn
  the death left (``turn_outcome`` daemon_stopped through core/hive_boot, the EC2 boot
  cleanup) and seal it with the declared repository's commits as a bundle, then upload
  those entries too, reporting nothing pending?
- does it run only in an AgentCore Hive's environment?
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from core import hive_records as hr
from tests.test_hive_agentcore_mode import AC_SID, BUCKET, FakeS3
from tests.test_hive_boot_close_turns import BEFORE, DECLARED, FACTS, _a, _Msgs, _u

SID = "sess_rescue_open_000"
OUT = "Outputs/so_0123456789ab/notes.md"


class _Sessions:
    async def get(self, sid):
        return {"id": sid, "created_at": "2026-11-01T09:00:00", "metadata": {"task_repos": DECLARED}}


@pytest.fixture
def hive(tmp_path, monkeypatch):
    """An AgentCore Hive whose backend died mid-turn: one order bound to a session whose
    last turn wrote rows and was never sealed, its S3 a fake client."""
    from core import hive_agentcore, hive_boot, hive_bundle, hive_workspace, pipeline_human, task_repos
    import core.project_registry as pr
    import database
    for k, v in {"SWARMAI_MODE": "hive", "SWARMAI_CARRIER": "agentcore", "SWARMAI_AC_SID": AC_SID,
                 "SWARMAI_AC_BUCKET": BUCKET, "AWS_REGION": "us-east-1",
                 "SWARM_DATA_DIR": str(tmp_path / "data")}.items():
        monkeypatch.setenv(k, v)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(pr, "get_swarmws", lambda: ws)
    monkeypatch.setattr(hive_agentcore, "_workspace", lambda: ws)
    monkeypatch.setattr(hive_agentcore, "_usage_fingerprint", lambda: (0, 0))
    hive_agentcore._reset_for_tests()
    s3 = FakeS3()
    hive_agentcore._client_obj = s3
    msgs = _Msgs({SID: [_u("1", BEFORE), _a("1", BEFORE)]})
    monkeypatch.setattr(database, "db", SimpleNamespace(messages=msgs, sessions=_Sessions()))
    monkeypatch.setattr(hive_workspace, "_orders", lambda: ["so_0123456789ab"])
    monkeypatch.setattr(hive_workspace, "load", lambda w, wid: {"work_id": wid, "session_id": SID})
    monkeypatch.setattr(task_repos, "repo_facts", lambda w, repos, since: [dict(f) for f in FACTS])

    def add_bundles(w, sid, facts):                 # the branch head's commits, as make() leaves them
        for f in facts:
            tmp = hr.tmp_path(w, sid, hive_bundle.key(f["dir"]))
            tmp.write_bytes(b"# v2 git bundle\n" + b"PACK" * 100)
            f["bundle"] = {"key": hive_bundle.key(f["dir"]), "tmp": str(tmp)}
        return facts
    monkeypatch.setattr(hive_bundle, "add_bundles", add_bundles)
    monkeypatch.setattr(pipeline_human, "pending_decisions", lambda: [])
    monkeypatch.setattr(hive_boot, "STARTED_AT", datetime(2026, 11, 1, 12, 0, 0))
    reviews: list = []

    async def request_review(router, session_id, pm=None):
        reviews.append(session_id)
        return True
    from core import hive_review
    monkeypatch.setattr(hive_review, "request_review", request_review)
    yield SimpleNamespace(ws=ws, s3=s3, msgs=msgs, mod=hive_agentcore, reviews=reviews)
    hive_agentcore._reset_for_tests()


def _uploaded_seqs(s3) -> list[int]:
    return [int(c["Key"].rsplit("/", 1)[1].split(".")[0]) for c in s3.calls if "/records/manifest/" in c["Key"]]


def test_rescue_uploads_pending_then_seals_the_open_turn_with_its_bundle(hive):
    from core import hive_rescue
    # an entry sealed while S3 failed: written, never uploaded
    hive.s3.fail = True
    hr.append(hive.ws, SID, "file", data=b"# notes\n", path=OUT, listed=OUT)
    assert hive.mod.flush()
    hive.s3.fail = False
    assert hive.mod.export_pending(hive.ws) and _uploaded_seqs(hive.s3) == []

    from core import hive_boot
    close = hive_boot.close_open_turns
    in_s3_at_seal: list = []

    async def watched(router):
        # what a seal that hangs (and is killed at the supervisor's guard) would leave
        in_s3_at_seal.append(_uploaded_seqs(hive.s3))
        return await close(router)
    hive_boot.close_open_turns = watched
    try:
        got = asyncio.run(hive_rescue.rescue(hive.ws))
    finally:
        hive_boot.close_open_turns = close

    assert in_s3_at_seal == [[1]]                      # the pending entry was in S3 first
    assert got["ok"] is True and got["sealed"] == 1 and got["pending"] is False
    assert got["uploaded"] >= 1
    # the open turn is closed the way an EC2 boot closes it, its commits in the outcome
    outcome = [b for r in hive.msgs.rows[SID] for b in r.get("content") or []
               if isinstance(b, dict) and b.get("type") == "turn_outcome"]
    assert len(outcome) == 1 and outcome[0]["ended"] == "daemon_stopped"
    bundle = outcome[0]["repos"][0]["bundle"]
    assert type(bundle["seq"]) is int and "error" not in bundle
    kinds = [(e["seq"], e["kind"]) for e in hr.entries(hive.ws, SID)["entries"]]
    assert kinds[0] == (1, "file") and (bundle["seq"], "bundle") in kinds and kinds[-1][1] == "turn"
    # the pending entry went first, then every entry the seal added; nothing is left
    seqs = _uploaded_seqs(hive.s3)
    assert seqs[0] == 1 and sorted(seqs) == [s for s, _ in kinds]
    assert not hive.mod.export_pending(hive.ws)
    assert f"bridge/{AC_SID}/records/{hr._name(bundle['seq'], 'bundle', bundle['key'])}" in hive.s3.objects
    # a second rescue changes nothing
    again = asyncio.run(hive_rescue.rescue(hive.ws))
    assert again["sealed"] == 0 and again["pending"] is False
    assert len(hr.entries(hive.ws, SID)["entries"]) == len(kinds)


def test_rescue_runs_only_in_an_agentcore_hive(hive, monkeypatch):
    from core import hive_rescue
    monkeypatch.setenv("SWARMAI_CARRIER", "ec2")
    got = asyncio.run(hive_rescue.rescue(hive.ws))
    assert got == {"ok": False, "why": "not an AgentCore Hive"}
    assert not any(b.get("type") == "turn_outcome" for r in hive.msgs.rows[SID] for b in r.get("content") or []
                   if isinstance(b, dict))


def test_main_prints_one_json_line(hive, monkeypatch, capsys):
    from core import hive_rescue

    async def fake(workspace=None):
        return {"ok": True, "uploaded": 0, "sealed": 0, "pending": False}
    monkeypatch.setattr(hive_rescue, "rescue", fake)

    async def no_db():
        return False
    monkeypatch.setattr(hive_rescue, "_open_database", no_db)
    assert hive_rescue.main() == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is True


def test_no_database_is_created_where_there_was_none(hive, monkeypatch, tmp_path):
    """A backend that died before its first boot made its store: the rescue opens none and
    creates no file (the restarted backend would take an empty one for a returning user's)."""
    import database
    from core import hive_rescue
    db = tmp_path / "data" / "data.db"
    made: list = []

    async def initialize_database(skip_schema=False):
        made.append(skip_schema)
    monkeypatch.setattr(database, "get_database", lambda: SimpleNamespace(db_path=db))
    monkeypatch.setattr(database, "initialize_database", initialize_database)
    assert asyncio.run(hive_rescue._open_database()) is False
    assert made == [] and not db.exists()
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")                               # a returning store: opened as the lifespan does
    assert asyncio.run(hive_rescue._open_database()) is True and made == [True]
