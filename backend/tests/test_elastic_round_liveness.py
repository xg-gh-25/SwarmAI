"""How the Host learns an Elastic Workers round ended, and where a re-run starts
(core/hive_agentcore heartbeat, core/carriers/agentcore ``ended`` / ``state`` /
``writes_end_at`` / ``_returned_starts``, core/hive_return ``import_unrecorded``; design
docs/design/2026-11-20-swarm-out-multi-carrier.md §6.5, §6.6).

Each test answers one question:
- does the backend write ``heartbeat.json`` while busy, every HEARTBEAT_S, never while idle,
  and does a refused heartbeat never count as busy?
- does a heartbeat older than its cadence + the runtime's idle timeout + the clock margin end
  the round at once, its result read in the same refresh, and a missing result marked lost
  without waiting for the 8-hour lifetime?
- does a round with no heartbeat (an older image), or a fresh one, keep the old rule?
- is a round older than the lifetime whose result is in S3 read as closed, not carrier_expired?
- do the supervisor's words (expired; crashed, final or restarting) read as they should?
- may the execution role write the heartbeat?
- does a re-run import the newest returned bundle no turn record names, with prepare's checks,
  and start there; and stay as before when the turn record names it?
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

from core import hive_bundle, hive_files, hive_records, hive_return, swarm_out
from core import swarm_out_results as sor
from core.carriers import agentcore as car
from core.carriers import registry
from core.session_unit import SessionState
from tests.test_carriers_agentcore import BUCKET, DID, _close_in_s3, _git, _open, _to_s3, world  # noqa: F401
from tests.test_hive_agentcore_mode import AC_SID, HIVE_SID, Router, Unit, ac, pm  # noqa: F401

STALE_S = car.HEARTBEAT_S + car.IDLE_TIMEOUT_S + car.END_MARGIN_S


# ── the backend's heartbeat ──────────────────────────────────────────────────────

async def test_the_backend_writes_a_heartbeat_only_while_busy(ac, pm, monkeypatch):  # noqa: F811
    key = f"bridge/{AC_SID}/heartbeat.json"
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(ac.mod, "time", SimpleNamespace(time=lambda: clock["t"]))
    await ac.mod.sync_pass(router=Router(), pm=pm, workspace=ac.ws)
    assert key not in ac.s3.objects                                  # idle: none
    busy = Router(Unit(HIVE_SID, SessionState.STREAMING))
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key]) == {"at": 1_000_000.0, "every": ac.mod.HEARTBEAT_S}
    clock["t"] += 60
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key])["at"] == 1_000_000.0       # not due yet
    clock["t"] += ac.mod.HEARTBEAT_S
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key])["at"] == clock["t"]
    # a role that may not write it yet: logged, never busy, tried again at the next due pass
    put = ac.s3.put_object

    def refuse(**kw):
        if kw["Key"] == key:
            err = Exception("denied")
            err.response = {"Error": {"Code": "AccessDenied"}}
            raise err
        return put(**kw)
    monkeypatch.setattr(ac.s3, "put_object", refuse)
    clock["t"] += ac.mod.HEARTBEAT_S
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    assert (await ac.mod.busy(router=Router(), pm=pm, workspace=ac.ws)) == {"busy": False, "reasons": []}
    monkeypatch.setattr(ac.s3, "put_object", put)
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key])["at"] == clock["t"]
    # written before the pass's record export: a long upload never delays it
    from core import hive_records
    ac.s3.fail = True
    hive_records.append(ac.ws, HIVE_SID, "turn", data=b'{"rows": []}')
    assert ac.mod.flush()
    ac.s3.fail = False
    ac.s3.calls.clear()
    clock["t"] += ac.mod.HEARTBEAT_S
    await ac.mod.sync_pass(router=busy, pm=pm, workspace=ac.ws)
    keys = [c["Key"] for c in ac.s3.calls]
    assert keys[0] == key and any("/records/" in k for k in keys[1:])


# ── the Host: when a round ended ─────────────────────────────────────────────────

@pytest.fixture
def run(world, monkeypatch, tmp_path):                    # noqa: F811 — the imported fixture
    """A dispatched round on the fake runtime, ``age_s`` old, state.json running."""
    monkeypatch.setitem(registry._made, "agentcore", world.carrier)
    monkeypatch.setattr(hive_files, "_place_one", lambda ws, row, src, path: path)
    from core import project_registry
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path / "ws")
    rows: dict = {}
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda d: rows[d])

    async def make(age_s: float):
        h = await _open(world)
        h = dataclasses.replace(h, opened_at=time.time() - age_s)
        world.s3.put(f"bridge/{h.run_ref}/state.json", {"state": "running", "at": time.time() - age_s + 60})
        row = {"dispatch_id": DID, "hive_id": "ac1", "hive": "elastic", "session_id": "chat-0000000001",
               "rounds": [h.to_row()], "project_agent": {"returns": "commits"},
               "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s)),
               "dispatched_from": "sess-agent"}
        rows[DID] = row
        return row, h, world.tmp / "hive-ws"
    return make


def _beat(world, h, ago: float, every=None):
    world.s3.put(f"bridge/{h.run_ref}/heartbeat.json",
                 {"at": time.time() - ago, "every": car.HEARTBEAT_S if every is None else every})


async def _refresh(world, row, h) -> dict:
    """One refresh as swarm_out.refresh_runs and swarm_out_results.attach do it: the round's
    state, then the result check when it is due."""
    row["state"], row["state_reason"] = await world.carrier.state(h)
    kept = sor.load(DID)
    if not sor.check_due(row, "running", kept):
        return {"state": (row["state"], row["state_reason"]), "kept": kept}
    return {"state": (row["state"], row["state_reason"]), "kept": await sor.fetch(row)}


DONE = {"ended": "completed", "ended_at": "2026-11-24T01:00:00Z", "provisional": False,
        "work": {"repos": [], "files": []}}


async def test_a_stale_heartbeat_ends_the_round_and_its_result_is_read(run, world, monkeypatch):  # noqa: F811
    row, h, ws = await run(40 * 60)                    # 40 minutes old: far from its lifetime
    hive_records.append(ws, h.run_ref, "turn", data=json.dumps({"rows": [{"id": "r1"}], "first_turn": True}).encode())
    _to_s3(world, ws, h.run_ref)
    _beat(world, h, STALE_S - 30)
    got = await _refresh(world, row, h)
    assert got["state"] == ("running", "streaming")    # inside the window: still running
    _beat(world, h, STALE_S + 30)
    monkeypatch.setattr(sor, "summarize", lambda *a, **k: dict(DONE))
    got = await _refresh(world, row, h)
    assert got["state"] == ("stopped", "idle")
    assert got["kept"]["result"]["ended"] == "completed"           # read in this refresh


async def test_a_stale_heartbeat_alone_never_writes_a_live_round_off(run, world):  # noqa: F811
    """A heartbeat travels the same S3 path as the results: S3 writes failing for a while
    look like a dead round. It reads as stopped, but is not recorded and does not end the
    write window, so a missing result is not marked lost and a heartbeat that comes back
    opens the round again."""
    row, h, ws = await run(40 * 60)
    hive_records.append(ws, h.run_ref, "file", data=b"# r\n", path=f"Outputs/{DID}/r.md", listed=f"Outputs/{DID}/r.md")
    _to_s3(world, ws, h.run_ref)
    _beat(world, h, STALE_S + 30)
    got = await _refresh(world, row, h)
    assert got["state"] == ("stopped", "idle")
    assert got["kept"]["result"] is None and got["kept"].get("round_end") is None
    assert car.writes_end_at(h) == pytest.approx(h.opened_at + car.MAX_RUN_S + car.WRITE_GRACE_S)
    _beat(world, h, 10)                                # its S3 writes came back
    assert await world.carrier.state(h) == ("running", "streaming")


async def test_a_backend_not_restarted_marks_a_missing_result_lost_at_once(run, world):  # noqa: F811
    row, h, ws = await run(40 * 60)
    hive_records.append(ws, h.run_ref, "file", data=b"# r\n", path=f"Outputs/{DID}/r.md", listed=f"Outputs/{DID}/r.md")
    _to_s3(world, ws, h.run_ref)
    world.s3.put(f"bridge/{h.run_ref}/state.json", {"state": "crashed", "exit": -9, "restarting": False})
    got = await _refresh(world, row, h)
    assert got["state"] == ("stopped", "idle")
    assert got["kept"]["result"] is None
    assert got["kept"]["round_end"]["run_ref"] == h.run_ref      # not 8 h + 420 s later


async def test_an_end_by_age_is_recorded_once_nothing_can_be_written(run, world):  # noqa: F811
    _row, h, _ws = await run(car.MAX_RUN_S + 60)
    assert await world.carrier.ended(h) == "carrier_expired"
    assert car._marked_end(h.run_ref) is None           # a closed entry may still come
    old = dataclasses.replace(h, opened_at=time.time() - car.MAX_RUN_S - car.WRITE_GRACE_S - 60)
    assert await world.carrier.ended(old) == "carrier_expired"
    assert car._marked_end(h.run_ref) == "carrier_expired"
    n = len(world.s3.calls)
    assert await world.carrier.state(old) == ("stopped", "carrier_expired")
    assert len(world.s3.calls) == n                     # read here from now on, not from S3


async def test_no_heartbeat_keeps_the_old_rule_and_a_fresh_one_keeps_running(run, world):  # noqa: F811
    row, h, _ws = await run(3 * 3600)
    assert await world.carrier.state(h) == ("running", "streaming")   # an older image: no heartbeat
    assert car.writes_end_at(h) == pytest.approx(h.opened_at + car.MAX_RUN_S + car.WRITE_GRACE_S)
    _beat(world, h, 30)
    assert await world.carrier.state(h) == ("running", "streaming")
    # the worker's text: a heartbeat that does not read as one is no proof of an end
    for bad in (b'{"at": "yesterday"}', b'{"at": NaN}', b'{"at": true}', b"[1, 2]", b"not json"):
        world.s3.put(f"bridge/{h.run_ref}/heartbeat.json", bad)
        assert await world.carrier.state(h) == ("running", "streaming"), bad
    # a cadence out of range is read as the backend's own: a huge one never keeps a dead round open
    world.s3.put(f"bridge/{h.run_ref}/heartbeat.json", {"at": time.time() - STALE_S - 30, "every": 10 ** 9})
    assert await world.carrier.state(h) == ("stopped", "idle")


async def test_an_old_round_with_its_result_in_s3_reads_closed(run, world, monkeypatch):  # noqa: F811
    row, h, ws = await run(car.MAX_RUN_S + car.WRITE_GRACE_S + 60)
    hive_records.append(ws, h.run_ref, "turn", data=json.dumps({"rows": [{"id": "r1"}], "first_turn": True}).encode())
    hive_records.append(ws, h.run_ref, "closed", data=b"{}")
    _to_s3(world, ws, h.run_ref)
    assert await world.carrier.ended(h) == "closed"
    monkeypatch.setattr(sor, "summarize", lambda *a, **k: dict(DONE))
    got = await _refresh(world, row, h)
    assert got["state"] == ("stopped", "closed")
    assert got["kept"]["result"]["ended"] == "completed" and got["kept"].get("round_end") is None


async def test_an_old_round_with_nothing_in_s3_is_still_expired(run, world):  # noqa: F811
    row, h, _ws = await run(car.MAX_RUN_S + 60)
    assert await world.carrier.state(h) == ("stopped", "carrier_expired")
    # S3 not answering past the lifetime: expired too, never unknown for good

    def boom(**kw):
        raise RuntimeError("S3 down")
    h2 = dataclasses.replace(h, run_ref=car.session_id_for(DID, 1, 2))
    world.s3.get_object = boom
    assert await world.carrier.ended(h2) == "carrier_expired"


async def test_the_supervisors_words(run, world):  # noqa: F811
    row, h, _ws = await run(600)
    sid = h.run_ref
    world.s3.put(f"bridge/{sid}/state.json", {"state": "crashed", "exit": -9, "at": time.time(), "restarting": True})
    assert await world.carrier.state(h) == ("unknown", "starting")        # coming back
    world.s3.put(f"bridge/{sid}/state.json", {"state": "crashed", "exit": -9, "at": time.time() - STALE_S - 30,
                                               "restarting": True})
    assert await world.carrier.state(h) == ("stopped", "idle")            # it never came back
    # a stale sign of life is no proof nothing more comes: the write window stays the lifetime
    assert car.writes_end_at(h) == pytest.approx(h.opened_at + car.MAX_RUN_S + car.WRITE_GRACE_S)

    h2 = await _open(world, "so_ba9876543210")
    h2 = dataclasses.replace(h2, opened_at=time.time() - 600)
    world.s3.put(f"bridge/{h2.run_ref}/state.json", {"state": "crashed", "exit": 1, "restarting": False})
    assert await world.carrier.state(h2) == ("stopped", "idle")
    assert car.writes_end_at(h2) <= time.time()

    h3 = await _open(world, "so_aaaaaaaaaaaa")
    h3 = dataclasses.replace(h3, opened_at=time.time() - car.MAX_RUN_S + 1100)
    world.s3.put(f"bridge/{h3.run_ref}/state.json", {"state": "expired"})
    assert await world.carrier.state(h3) == ("stopped", "carrier_expired")
    assert car.writes_end_at(h3) <= time.time()                           # sealed: nothing more comes


def test_the_execution_role_may_write_the_heartbeat():
    p = car.execution_role_policy("123456789012", "us-east-1", BUCKET, None)
    puts = [r for s in p["Statement"] if s["Effect"] == "Allow" and "s3:PutObject" in s["Action"]
            for r in s["Resource"]]
    assert f"arn:aws:s3:::{BUCKET}/bridge/${{aws:AgentCoreRuntimeSourceSid}}/heartbeat.json" in puts


def test_the_idle_timeout_is_the_runtimes():
    from hive import agentcore_provisioner as prov
    assert car.IDLE_TIMEOUT_S == prov.IDLE_TIMEOUT_S == prov.runtime_spec("u", "r")[
        "lifecycleConfiguration"]["idleRuntimeSessionTimeout"]


# ── a re-run continues from an unrecorded bundle ─────────────────────────────────

URL = "https://github.com/example/demo"


def _repos(world):
    """The Host's checkout (main at its first commit) and a Hive bundle of one more commit."""
    host = world.tmp / "checkout"
    host.mkdir()
    _git(host, "init", "-q", "-b", "main")
    (host / "a.txt").write_text("1")
    _git(host, "add", ".")
    _git(host, "commit", "-qm", "one")
    one = _git(host, "rev-parse", "HEAD")
    hive = world.tmp / "hive-clone"
    subprocess.run(["git", "clone", "-q", str(host), str(hive)], check=True, capture_output=True)
    (hive / "a.txt").write_text("2")
    _git(hive, "commit", "-qam", "the round's commit, never in a turn record")
    two = _git(hive, "rev-parse", "HEAD")
    bundle = world.tmp / "b.bundle"
    _git(hive, "bundle", "create", "-q", str(bundle), "main", f"^{one}")
    return host, one, two, bundle


async def _returned(world, monkeypatch, outcome_names_bundle: bool, bundle_bytes=None):
    from core import project_registry
    monkeypatch.setattr(hive_files, "_place_one", lambda ws, row, src, path: path)
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: world.tmp / "ws")
    host, one, two, bundle = _repos(world)
    monkeypatch.setattr(hive_return, "_checkout", lambda brain, url: host)
    h = await _open(world)
    ws = world.tmp / "hive-ws"
    key = hive_bundle.key("demo")
    seq_of_bundle = 2 if outcome_names_bundle else 3
    repos = [{"url": URL, "dir": "demo", "bundle": {"key": key, "seq": seq_of_bundle}}] if outcome_names_bundle else []
    if outcome_names_bundle:
        hive_records.append(ws, h.run_ref, "file", data=b"x", path=f"Outputs/{DID}/x.md", listed=f"Outputs/{DID}/x.md")
    tmp = hive_records.tmp_path(ws, h.run_ref, key)
    tmp.write_bytes(bundle_bytes if bundle_bytes is not None else bundle.read_bytes())
    turn = {"rows": [{"id": "r1", "role": "assistant", "content": [
        {"type": "turn_outcome", "ended": "completed", "repos": repos}]}], "first_turn": True}
    if outcome_names_bundle:
        hive_records.append(ws, h.run_ref, "bundle", src=tmp, key=key)
        hive_records.append(ws, h.run_ref, "turn", data=json.dumps(turn).encode())
    else:
        hive_records.append(ws, h.run_ref, "turn", data=json.dumps(turn).encode())
        hive_records.append(ws, h.run_ref, "file", data=b"x", path=f"Outputs/{DID}/x.md", listed=f"Outputs/{DID}/x.md")
        hive_records.append(ws, h.run_ref, "bundle", src=tmp, key=key)   # sealed after its turn's record
    _to_s3(world, ws, h.run_ref)
    row = {"dispatch_id": DID, "session_id": "chat-0000000001", "rounds": [h.to_row()],
           "repos": [{"url": URL, "ref": "main", "sha": one, "name": "demo", "dir": "demo"}],
           "project_agent": {"brain": "Demo", "delivery": "bundle"}}
    await world.carrier.outputs(h, DID, row)
    assert any(e["kind"] == "bundle" for e in hive_records.local_entries(DID))
    world.round_handle = h
    return host, one, two, key, row


async def test_a_rerun_imports_an_unrecorded_bundle(world, monkeypatch):  # noqa: F811
    host, one, two, key, row = await _returned(world, monkeypatch, outcome_names_bundle=False)
    starts = car._returned_starts(DID, row)
    assert starts == [{"url": URL, "ref": "main", "sha": two, "checkout": str(host), "name": "demo",
                       "from": f"refs/hive/{DID}/{key}", "base": one}]    # the run's own start
    assert _git(host, "rev-parse", f"refs/hive/{DID}/{key}") == two
    assert _git(host, "rev-parse", "main") == one                     # no branch here moved


async def test_a_rerun_after_main_was_rewritten_never_starts_from_the_rewritten_history(world, monkeypatch):  # noqa: F811
    """main was rewritten after the dispatch (here from its first commit on, so the old
    and new histories share nothing): the unrecorded bundle is replayed onto the new
    equivalent, and the next round starts from the replayed tip, never from a commit
    main no longer has (it would be shipped to the Hive again)."""
    host, one, two, key, row = await _returned(world, monkeypatch, outcome_names_bundle=False)
    tree = _git(host, "rev-parse", f"{one}^{{tree}}")
    new_one = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", tree, "-m", "one"],
                             cwd=host, check=True, capture_output=True,
                             text=True, env={**__import__("os").environ, "GIT_AUTHOR_DATE": "@1 +0000",
                                             "GIT_COMMITTER_DATE": "@1 +0000"}).stdout.strip()
    _git(host, "checkout", "-q", "-B", "main", new_one)
    assert new_one != one
    starts = car._returned_starts(DID, row)
    sha = starts[0]["sha"]
    assert sha not in (one, two) and _git(host, "rev-parse", f"{sha}^") == new_one
    assert subprocess.run(["git", "merge-base", "--is-ancestor", one, sha], cwd=host).returncode != 0
    assert _git(host, "rev-parse", f"refs/hive/{DID}/{key}") == sha
    assert _git(host, "rev-parse", f"refs/hive/{DID}/{key}-hive") == two     # never shipped
    assert _git(host, "show", f"{sha}:a.txt") == "2"
    assert starts[0]["base"] == new_one              # the equivalent its commits were replayed onto


async def test_a_later_rounds_unrecorded_bundle_built_on_the_earlier_rounds_tip_is_imported(world, monkeypatch):  # noqa: F811
    """Round 1's commits are at the run's ref; the round after it started there (its order's
    base the run's start) and was cut off before its turn record: its newest bundle holds
    both rounds' commits on the run's start. It is imported, and the next round counts
    from the run's start."""
    host, one, two, key, row = await _returned(world, monkeypatch, outcome_names_bundle=False)
    car._returned_starts(DID, row)                                       # round 1's tip at the ref
    hive = world.tmp / "hive-clone"
    (hive / "a.txt").write_text("3")
    _git(hive, "commit", "-qam", "the next round's commit, never in a turn record")
    three = _git(hive, "rev-parse", "HEAD")
    bundle = world.tmp / "b2.bundle"
    _git(hive, "bundle", "create", "-q", str(bundle), "main", f"^{one}")    # from the run's start (core/task_repos)
    ws, h = world.tmp / "hive-ws", world.round_handle
    tmp = hive_records.tmp_path(ws, h.run_ref, key)
    tmp.write_bytes(bundle.read_bytes())
    hive_records.append(ws, h.run_ref, "bundle", src=tmp, key=key)
    _to_s3(world, ws, h.run_ref)
    await world.carrier.outputs(h, DID, row)
    starts = car._returned_starts(DID, row)
    assert (starts[0]["sha"], starts[0]["base"]) == (three, one)
    assert _git(host, "rev-parse", f"refs/hive/{DID}/{key}") == three
    assert _git(host, "rev-parse", "main") == one                     # no branch here moved


async def test_a_recorded_bundle_leaves_the_start_as_before(world, monkeypatch):  # noqa: F811
    host, one, _two, key, row = await _returned(world, monkeypatch, outcome_names_bundle=True)
    starts = car._returned_starts(DID, row)
    assert starts == [{"url": URL, "ref": "main", "sha": one, "name": "demo"}]   # not prepared: the dispatch's start
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/hive/{DID}/{key}"], cwd=host,
                          capture_output=True).returncode != 0


async def test_an_unrecorded_bundle_that_fails_its_checks_stops_the_rerun(world, monkeypatch):  # noqa: F811
    host, _one, _two, key, row = await _returned(world, monkeypatch, outcome_names_bundle=False,
                                                 bundle_bytes=b"# v2 git bundle\nnot a bundle\n")
    with pytest.raises(car.AgentCoreError) as e:
        car._returned_starts(DID, row)
    assert "demo" in str(e.value) and "nothing was sent" in str(e.value)
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/hive/{DID}/{key}"], cwd=host,
                          capture_output=True).returncode != 0


async def test_a_round_waiting_on_a_pipeline_decision_keeps_its_heartbeat(ac, pm, monkeypatch):  # noqa: F811
    """A pipeline decision that waits is a wait like a question (core/hive_review): the
    round stays busy, so its heartbeat keeps beating and the Host never reads the wait as a
    silent end (heartbeat_lost) while nothing streams."""
    from core import hive_review
    key = f"bridge/{AC_SID}/heartbeat.json"
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(ac.mod, "time", SimpleNamespace(time=lambda: clock["t"]))
    monkeypatch.setattr(hive_review, "decision_waits", lambda sid: sid == HIVE_SID)
    idle_unit = Router(Unit(HIVE_SID))
    await ac.mod.sync_pass(router=idle_unit, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key])["at"] == clock["t"]
    monkeypatch.setattr(hive_review, "decision_waits", lambda sid: False)
    clock["t"] += ac.mod.HEARTBEAT_S
    await ac.mod.sync_pass(router=idle_unit, pm=pm, workspace=ac.ws)
    assert json.loads(ac.s3.objects[key])["at"] == clock["t"] - ac.mod.HEARTBEAT_S   # idle: no beat
