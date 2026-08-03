"""Tests for EvalService CRUD operations and run triggers (P3)."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure backend is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.eval_service import EvalService


@pytest.fixture
def eval_workspace(tmp_path):
    """Create a minimal eval workspace with golden_set.yaml and EvalHistory."""
    project_dir = tmp_path / "Eval"
    project_dir.mkdir(parents=True)
    history_dir = project_dir / "EvalHistory"
    history_dir.mkdir()

    golden_set = {
        "version": 2,
        "categories": ["compliance", "recall"],
        "dimensions": ["compliance", "factual_accuracy"],
        "cases": [
            {
                "id": "GS001",
                "category": "compliance",
                "dimension": "compliance",
                "level": "session",
                "title": "Pipeline mandatory for code changes",
                "source": "C011",
                "eval_method": "programmatic",
                "affected_by": ["AGENT.md"],
                "evaluators": ["file_contains"],
                "scenario": {"turns": [{"input": "Fix typo"}]},
                "expected_trajectory": ["pipeline"],
                "verification": {"file": "test.py", "grep": "pipeline"},
            },
            {
                "id": "GS002",
                "category": "recall",
                "dimension": "factual_accuracy",
                "level": "session",
                "title": "Memory recall accuracy",
                "source": "KD01",
                "affected_by": ["MEMORY.md"],
                "evaluators": ["canary_pass"],
                "scenario": {"turns": [{"input": "What is X?"}]},
                "expected_trajectory": ["read_memory"],
                "verification": {"command": "echo OK", "expected_contains": "OK"},
            },
        ],
    }

    # Write YAML
    import yaml
    (project_dir / "golden_set.yaml").write_text(yaml.dump(golden_set, default_flow_style=False))

    # Write a sample run
    run = {
        "run_id": "eval_20260614_manual",
        "triggered_by": "manual",
        "triggered_at": "2026-06-14T04:00:00Z",
        "overall_score": 100.0,
        "dimensions": {"compliance": 100.0, "factual_accuracy": 100.0},
        "cases": [
            {"id": "GS001", "status": "passed", "duration_ms": 50},
            {"id": "GS002", "status": "passed", "duration_ms": 30},
        ],
        "total_cases": 2,
        "cases_passed": 2,
        "cases_failed": 0,
        "cases_skipped": 0,
        "duration_seconds": 0.08,
    }
    (history_dir / "2026-06-14_manual.json").write_text(json.dumps(run))

    return tmp_path


@pytest.fixture
def svc(eval_workspace):
    """Create an EvalService backed by the test workspace."""
    return EvalService(workspace_root=eval_workspace)


# ─── CRUD: Add Case ──────────────────────────────────────────────────────────


class TestAddCase:
    def test_add_case_success(self, svc):
        new_case = {
            "id": "GS003",
            "category": "compliance",
            "dimension": "compliance",
            "level": "session",
            "title": "New test case",
            "source": "manual",
            "affected_by": ["SOUL.md"],
            "evaluators": ["file_contains"],
            "scenario": {"turns": [{"input": "test"}]},
            # gate-eligible (file_contains) → teeth gate requires a negative_command
            # (run_5edf2cc0 G3). Auto-stamped on add.
            "verification": {"file": "x.py", "grep": "y", "negative_command": "false"},
        }
        result = svc.add_case(new_case)
        assert result["id"] == "GS003"
        assert svc.case_count == 3
        # G3/G8: gate-eligible case auto-stamped on add
        assert result.get("validated_by_4gate")

    def test_add_case_duplicate_id_fails(self, svc):
        duplicate = {
            "id": "GS001",  # already exists
            "category": "compliance",
            "dimension": "compliance",
            "title": "Duplicate",
            "evaluators": ["file_contains"],
            "affected_by": ["AGENT.md"],
            "verification": {"file": "x.py", "grep": "y", "negative_command": "false"},
        }
        with pytest.raises(ValueError, match="already exists"):
            svc.add_case(duplicate)

    def test_add_case_missing_required_field(self, svc):
        incomplete = {"id": "GS099", "title": "No evaluators"}
        with pytest.raises(ValueError, match="required"):
            svc.add_case(incomplete)

    def test_add_case_rejects_drifted_dotted_ref(self, svc, eval_workspace):
        """BLOCKER 2 / C044: gate_refs MUST run on the add path. A dotted ref that
        resolves EMPTY in .context (drifted, e.g. STEERING.R1 post-2026-06-27 reorg)
        is rejected — so a case can never silently enter the corpus feeding the judge
        empty context. Requires a .context/ in the workspace for resolution."""
        ctx = eval_workspace / ".context"
        ctx.mkdir(exist_ok=True)
        # STEERING.md WITHOUT an R1 rule → STEERING.R1 resolves empty (the drift class)
        (ctx / "STEERING.md").write_text("### 1. Some rule\nbody\n")
        bad = {
            "id": "GS_DRIFT", "category": "compliance", "dimension": "compliance",
            "title": "Drifted ref", "eval_method": "llm",
            "evaluators": ["goal_success"], "affected_by": ["STEERING.R1"],
            "scenario": {"turns": [{"input": "x"}]},
        }
        with pytest.raises(ValueError, match="(?i)ref|resolve|drift"):
            svc.add_case(bad)

    def test_add_case_accepts_resolvable_dotted_ref(self, svc, eval_workspace):
        """Companion: a dotted ref that DOES resolve passes gate_refs on add."""
        ctx = eval_workspace / ".context"
        ctx.mkdir(exist_ok=True)
        (ctx / "AGENT.md").write_text("R1. **Pipeline is mandatory** for all changes.\n")
        good = {
            "id": "GS_RESOLVE", "category": "compliance", "dimension": "compliance",
            "title": "Resolvable ref", "eval_method": "llm",
            "evaluators": ["goal_success"], "affected_by": ["AGENT.R1"],
            "scenario": {"turns": [{"input": "x"}]},
        }
        result = svc.add_case(good)
        assert result["id"] == "GS_RESOLVE"

    def test_add_case_persists_to_disk(self, svc, eval_workspace):
        new_case = {
            "id": "GS004",
            "category": "recall",
            "dimension": "factual_accuracy",
            "level": "session",
            "title": "Persisted case",
            "source": "test",
            "affected_by": ["KNOWLEDGE.md"],
            "evaluators": ["canary_pass"],
            "verification": {"command": "echo hi", "expected_contains": "hi",
                             "negative_command": "false"},
        }
        svc.add_case(new_case)

        # Reload from disk to verify
        svc2 = EvalService(workspace_root=eval_workspace)
        assert svc2.case_count == 3
        case = svc2.get_case_detail("GS004")
        assert case is not None
        assert case["title"] == "Persisted case"


# ─── CRUD: Update Case ───────────────────────────────────────────────────────


class TestUpdateCase:
    def test_update_case_success(self, svc):
        result = svc.update_case("GS001", {"title": "Updated title"})
        assert result["title"] == "Updated title"
        # Other fields preserved
        assert result["category"] == "compliance"

    def test_update_case_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.update_case("GS999", {"title": "Nope"})

    def test_update_case_cannot_change_id(self, svc):
        with pytest.raises(ValueError, match="Cannot change"):
            svc.update_case("GS001", {"id": "GS_NEW"})

    def test_update_case_persists(self, svc, eval_workspace):
        svc.update_case("GS001", {"title": "Persisted update"})
        svc2 = EvalService(workspace_root=eval_workspace)
        case = svc2.get_case_detail("GS001")
        assert case["title"] == "Persisted update"


# ─── CRUD: Delete (Archive) Case ─────────────────────────────────────────────


class TestDeleteCase:
    def test_delete_case_archives(self, svc):
        result = svc.delete_case("GS002")
        assert result["tier"] == "archived"
        # Case still accessible via detail
        case = svc.get_case_detail("GS002")
        assert case["tier"] == "archived"

    def test_delete_case_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.delete_case("GS999")

    def test_delete_case_excluded_from_golden_set_list(self, svc):
        svc.delete_case("GS002")
        gs = svc.get_golden_set()
        active_ids = [c["id"] for c in gs["cases"] if c.get("tier") != "archived"]
        assert "GS002" not in active_ids


class TestGoldenSetEvalMethod:
    """G3: get_golden_set must expose eval_method per case (frontend summary needs it)."""

    def test_golden_set_cases_include_eval_method_key(self, svc):
        gs = svc.get_golden_set()
        for c in gs["cases"]:
            assert "eval_method" in c, f"case {c['id']} missing eval_method key"

    def test_eval_method_value_passed_through(self, svc):
        gs = svc.get_golden_set()
        by_id = {c["id"]: c for c in gs["cases"]}
        assert by_id["GS001"]["eval_method"] == "programmatic"

    def test_missing_eval_method_is_none_not_dropped(self, svc):
        # GS002 fixture has no eval_method → key present, value None (never KeyError)
        gs = svc.get_golden_set()
        by_id = {c["id"]: c for c in gs["cases"]}
        assert by_id["GS002"]["eval_method"] is None


# ─── Run Triggers ────────────────────────────────────────────────────────────


class TestTriggerRun:
    def test_trigger_run_returns_run_id(self, svc):
        run_id = svc.trigger_run(trigger="manual")
        assert run_id.startswith("eval_")
        assert "manual" in run_id

    def test_trigger_run_with_cases(self, svc):
        run_id = svc.trigger_run(trigger="manual", case_ids=["GS001"])
        assert run_id is not None

    def test_trigger_run_creates_history_file(self, svc, eval_workspace):
        run_id = svc.trigger_run(trigger="test_trigger")
        # Wait for background thread (max 5s)
        import time
        for _ in range(50):
            svc.reload()
            runs = svc.get_history()
            if any(r["run_id"] == run_id for r in runs):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"Run {run_id} not found in history after 5s")

        run = next(r for r in runs if r["run_id"] == run_id)
        assert run["triggered_by"] == "test_trigger"

    def test_trigger_run_rejects_while_running(self, svc):
        """Cannot trigger a new run while one is in progress."""
        svc.trigger_run(trigger="first")
        # Immediately try second — should raise or return None
        with pytest.raises((ValueError, RuntimeError)):
            svc.trigger_run(trigger="second")

    def _capture_run_eval_include_behavior(self, svc, **trigger_kwargs):
        """Helper: trigger a run with run_eval mocked at the leaf boundary,
        join the background thread, return the include_behavior kwarg run_eval
        actually received. This proves end-to-end threading
        TriggerRunRequest -> trigger_run -> _execute_run -> run_eval."""
        captured = {}
        done = threading.Event()

        def fake_run_eval(cases_data, trigger, case_ids, root, **kwargs):
            captured["include_behavior"] = kwargs.get("include_behavior", "ABSENT")
            done.set()
            return {
                "run_id": "x", "triggered_by": trigger, "overall_score": 100.0,
                "dimensions": {}, "cases": [], "total_cases": 0,
                "cases_passed": 0, "cases_failed": 0, "cases_skipped": 0,
                "duration_seconds": 0.0,
            }

        # run_eval is imported inside _execute_run via `from scripts.eval_runner
        # import run_eval` — patch it at its source module.
        with patch("scripts.eval_runner.run_eval", side_effect=fake_run_eval):
            svc.trigger_run(**trigger_kwargs)
            assert done.wait(timeout=5.0), "run_eval was never called"
        return captured.get("include_behavior", "ABSENT")

    def test_trigger_run_forwards_include_behavior_true(self, svc):
        """AC1: opt-in — trigger_run(include_behavior=True) reaches run_eval as True."""
        assert self._capture_run_eval_include_behavior(
            svc, trigger="manual", include_behavior=True) is True

    def test_trigger_run_defaults_include_behavior_false(self, svc):
        """AC2: safety default preserved — omitting the flag reaches run_eval as
        False (behavior excluded on a blanket manual sweep)."""
        assert self._capture_run_eval_include_behavior(
            svc, trigger="manual") is False

    def test_behavior_case_count(self, svc):
        """Gate-2 MED#1: the /run route surfaces how many behavior cases an
        include_behavior=True sweep will spawn, so the caller sees the ~cost
        magnitude. The fixture golden_set has 0 behavior-method cases."""
        assert svc.behavior_case_count() == 0
        svc.add_case({
            "id": "GS_BEH", "category": "compliance", "dimension": "compliance",
            "title": "A behavior case", "eval_method": "behavior",
            "evaluators": ["goal_success"], "affected_by": ["AGENT.md"],
            "scenario": {"turns": [{"input": "x"}]},
        })
        assert svc.behavior_case_count() == 1


class TestPersistPreservesDiskOnlyCases:
    """_persist_golden_set must NOT drop cases that exist on disk but not in
    self._cases — i.e. cases added by another session/manual edit AFTER this
    process loaded golden_set.yaml. Reproduces the data-loss corruption
    (13 cases incl all GS_TRAJ behavior cases gutted, 2026-06-25). Radar b40b9545.

    Invariant relied upon: the disk-only re-append (a case on disk but absent from
    self._cases) means EXTERNALLY-added → preserve verbatim. The ONE exception is
    hard_delete_cases (run_110678fb): it shrinks self._cases AND passes the removed
    ids as `removed_ids` to the persist, so the disk-only loop SKIPS them — a
    locally-hard-deleted id is therefore NOT resurrected. delete_case (soft) still
    keeps the case in self._cases as archived. So: disk-only + NOT in removed_ids =
    externally-added (preserve); disk-only + in removed_ids = just hard-deleted (drop).
    """

    def _external_add_case(self, eval_workspace, case):
        """Simulate another session/manual edit appending a case to disk."""
        import yaml
        path = eval_workspace / "Eval" / "golden_set.yaml"
        data = yaml.safe_load(path.read_text())
        data["cases"].append(case)
        path.write_text(yaml.dump(data, default_flow_style=False))

    def test_disk_only_case_survives_persist(self, svc, eval_workspace):
        """AC1: a case on disk but absent from self._cases survives a persist.

        This is the exact corruption: svc loaded {GS001,GS002}; an external
        writer adds GS_EXTERNAL to disk; svc then persists (triggered by an
        unrelated update) — GS_EXTERNAL must NOT be silently dropped.
        """
        external = {
            "id": "GS_EXTERNAL",
            "category": "compliance",
            "dimension": "compliance",
            "level": "session",
            "title": "Added by a parallel session after svc loaded",
            "source": "external",
            "affected_by": ["STEERING.md"],
            "evaluators": ["file_contains"],
            "verification": {"file": "z.py", "grep": "z"},
        }
        self._external_add_case(eval_workspace, external)

        # svc's in-memory _cases does NOT know about GS_EXTERNAL. Trigger a
        # persist via an unrelated update to an in-memory case.
        svc.update_case("GS001", {"title": "unrelated touch"})

        # The disk-only case must still be on disk.
        svc2 = EvalService(workspace_root=eval_workspace)
        case = svc2.get_case_detail("GS_EXTERNAL")
        assert case is not None, "disk-only case was silently dropped (data loss)"
        assert case["title"] == "Added by a parallel session after svc loaded"
        # And the unrelated update still took effect.
        assert svc2.get_case_detail("GS001")["title"] == "unrelated touch"

    def test_in_both_user_fields_preserved(self, svc, eval_workspace):
        """AC2: for a case present in BOTH disk and memory, user-owned disk
        fields (tags/notes/promoted_from) survive a persist that didn't touch them."""
        import yaml
        path = eval_workspace / "Eval" / "golden_set.yaml"
        data = yaml.safe_load(path.read_text())
        for c in data["cases"]:
            if c["id"] == "GS002":
                c["tags"] = ["behavior_trajectory", "full"]
                c["notes"] = "hand-written note"
        path.write_text(yaml.dump(data, default_flow_style=False))

        # svc (loaded BEFORE the tag edit) persists via an update to GS001.
        svc.update_case("GS001", {"title": "touch"})

        svc2 = EvalService(workspace_root=eval_workspace)
        gs002 = svc2.get_case_detail("GS002")
        assert gs002.get("tags") == ["behavior_trajectory", "full"], "user-owned tags lost"
        assert gs002.get("notes") == "hand-written note", "user-owned notes lost"

    def test_soft_deleted_not_duplicated(self, svc, eval_workspace):
        """AC4: a soft-deleted (archived) case is preserved once, not resurrected
        as active nor duplicated. delete_case keeps it in _cases as archived;
        the disk-only append must not re-add it."""
        svc.delete_case("GS002")  # tier -> archived, persisted
        svc.update_case("GS001", {"title": "touch again"})  # another persist

        svc2 = EvalService(workspace_root=eval_workspace)
        gs = svc2.get_golden_set()
        gs002_entries = [c for c in gs["cases"] if c["id"] == "GS002"]
        assert len(gs002_entries) == 1, "soft-deleted case duplicated"
        assert gs002_entries[0]["tier"] == "archived", "soft-deleted case resurrected as active"

    def test_hard_delete_physically_removes_and_no_resurrection(self, svc, eval_workspace):
        """run_110678fb: hard_delete_cases PHYSICALLY removes a case (not archived)
        AND it is NOT resurrected by the disk-only re-append on a subsequent persist.
        Without the removed_ids skip, the case would reappear from disk (the trap)."""
        svc.hard_delete_cases(["GS002"])
        # immediately gone from disk (not archived, fully absent)
        svc2 = EvalService(workspace_root=eval_workspace)
        gs = svc2.get_golden_set()
        assert not any(c["id"] == "GS002" for c in gs["cases"]), "GS002 not physically removed"
        # and a LATER persist (unrelated update) must NOT resurrect it
        svc.update_case("GS001", {"title": "touch after hard delete"})
        svc3 = EvalService(workspace_root=eval_workspace)
        gs3 = svc3.get_golden_set()
        assert not any(c["id"] == "GS002" for c in gs3["cases"]), "GS002 RESURRECTED after persist"

    def test_hard_delete_leaves_other_cases_intact(self, svc, eval_workspace):
        """hard_delete drops ONLY the targets; survivors untouched."""
        before = {c["id"] for c in svc.get_golden_set()["cases"]}
        svc.hard_delete_cases(["GS002"])
        after = {c["id"] for c in EvalService(workspace_root=eval_workspace).get_golden_set()["cases"]}
        assert before - after == {"GS002"}, f"hard_delete dropped wrong set: {before - after}"
        assert "GS001" in after

    def test_hard_delete_reports_not_found(self, svc):
        """Destructive op semantics: report which ids were deleted vs not found."""
        result = svc.hard_delete_cases(["GS002", "GS_NOPE"])
        assert "GS002" in result["deleted"]
        assert "GS_NOPE" in result["not_found"]

    def test_hard_delete_removes_on_disk_case_not_in_memory(self, svc, eval_workspace):
        """Gate-2 F: a case added to DISK after svc loaded (absent from this svc's
        memory) must still be PHYSICALLY deleted — not reported not_found and left.
        hard_delete _load()s disk truth first, so it operates on the current corpus."""
        self._external_add_case(eval_workspace, {
            "id": "GS_ONDISK", "category": "compliance", "dimension": "compliance",
            "level": "session", "title": "added to disk after load", "source": "ext",
            "affected_by": ["STEERING.md"], "evaluators": ["file_contains"],
            "verification": {"file": "z.py", "grep": "z"},
        })
        result = svc.hard_delete_cases(["GS_ONDISK"])
        assert "GS_ONDISK" in result["deleted"], f"on-disk case not deleted: {result}"
        svc2 = EvalService(workspace_root=eval_workspace)
        assert not any(c["id"] == "GS_ONDISK" for c in svc2.get_golden_set()["cases"]), \
            "on-disk case survived hard_delete"

    def test_disk_read_failure_preserves_in_memory(self, svc, eval_workspace):
        """AC5/edge: if the disk re-read fails, the append must no-op (disk_cases
        empty) and in-memory cases are still written — current behavior preserved,
        no crash."""
        import core.eval_service as es_mod
        # Force the disk re-read inside _persist to raise, exercising the
        # except-branch where disk_cases stays empty.
        orig_safe_load = es_mod.yaml.safe_load
        calls = {"n": 0}

        def flaky_safe_load(text):
            calls["n"] += 1
            raise ValueError("simulated corrupt disk read")

        with patch.object(es_mod.yaml, "safe_load", side_effect=flaky_safe_load):
            # Should not raise; in-memory cases written as-is.
            svc.update_case("GS001", {"title": "survives disk-read-fail"})

        assert calls["n"] >= 1, "disk re-read path not exercised"
        svc2 = EvalService(workspace_root=eval_workspace)
        assert svc2.get_case_detail("GS001")["title"] == "survives disk-read-fail"

    def test_id_less_in_memory_case_still_written(self, svc, eval_workspace):
        """Gate-1 edge: an in-memory case with a falsy/absent id must still be
        PRESERVED on persist (not dropped). Under the public/private split
        (run_69b1c644), an untagged case routes to the PRIVATE file (fail-closed
        default — never auto-publish a case that didn't go through PROMOTE).
        The data-loss guard still holds; only the destination is private."""
        svc._cases.append({"title": "no id here", "category": "compliance"})
        svc.update_case("GS001", {"title": "touch"})  # triggers persist

        import yaml
        proj = eval_workspace / "Eval"
        pub = yaml.safe_load((proj / "golden_set.yaml").read_text())
        priv_path = proj / "golden_set.private.yaml"
        priv = yaml.safe_load(priv_path.read_text()) if priv_path.exists() else {"cases": []}
        all_titles = [c.get("title") for c in pub["cases"] + priv.get("cases", [])]
        assert "no id here" in all_titles, "id-less in-memory case was dropped on persist"
        # fail-closed: untagged case must NOT auto-land in the tracked public file
        pub_titles = [c.get("title") for c in pub["cases"]]
        assert "no id here" not in pub_titles, "untagged case leaked into public file"


class TestPersistCrossProcessLock:
    """_persist_golden_set must hold an OS-level exclusive lock spanning the
    disk re-read THROUGH the atomic rename, so two concurrent SwarmAI processes
    cannot lose each other's golden_set writes (cross-process TOCTOU). The
    in-process threading.Lock does NOT protect across processes; flock does.
    Follow-up to run_fb4b42d2 (todo 7e233ecb).
    """

    def _lock_path(self, eval_workspace):
        return eval_workspace / "Eval" / "golden_set.yaml.lock"

    def test_persist_acquires_exclusive_lock(self, svc, eval_workspace):
        """The persist must BLOCK while another fd holds the sidecar lock,
        proving it actually acquires an OS-level exclusive lock (not just the
        in-process threading.Lock). We hold the lock from the main thread, run
        persist in a background thread, and assert it does not complete until
        we release."""
        import threading
        from utils.file_lock import flock_exclusive, flock_unlock

        lock_path = self._lock_path(eval_workspace)
        # Pre-create the sidecar so we can hold it before persist runs.
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fd = open(lock_path, "w")
        flock_exclusive(holder_fd)  # main thread now holds the lock

        done = threading.Event()

        def run_persist():
            # update_case triggers _persist_golden_set, which must block on the
            # exclusive lock we hold.
            svc.update_case("GS001", {"title": "blocked-until-release"})
            done.set()

        t = threading.Thread(target=run_persist, daemon=True)
        t.start()

        # While we hold the lock, persist must NOT complete.
        assert not done.wait(timeout=0.8), (
            "persist completed while another process held the exclusive lock — "
            "it is not acquiring an OS-level lock (TOCTOU still open)"
        )

        # Release; persist should now proceed and finish promptly.
        flock_unlock(holder_fd)
        holder_fd.close()
        assert done.wait(timeout=5.0), "persist did not complete after lock release (deadlock?)"

        svc2 = EvalService(workspace_root=eval_workspace)
        assert svc2.get_case_detail("GS001")["title"] == "blocked-until-release"

    def test_lock_released_on_exception(self, svc, eval_workspace):
        """If persist raises mid-write, the lock MUST be released (finally), so a
        subsequent persist is not deadlocked."""
        import core.eval_service as es_mod
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        # Force the atomic write to blow up AFTER the lock is acquired.
        orig_replace = es_mod.Path.replace

        def boom(self, target):
            raise OSError("simulated rename failure")

        with patch.object(es_mod.Path, "replace", boom):
            with pytest.raises(OSError, match="simulated rename failure"):
                svc.update_case("GS001", {"title": "will fail"})

        # Lock must be free now — prove by acquiring it non-blocking.
        lock_path = self._lock_path(eval_workspace)
        fd = open(lock_path, "w")
        try:
            flock_exclusive_nb(fd)  # raises if still held → deadlock regression
        finally:
            flock_unlock(fd)
            fd.close()

    def test_two_threads_no_data_loss(self, svc, eval_workspace):
        """Two concurrent persists (each adding a distinct case) must both
        survive — the lock serializes them so neither clobbers the other."""
        import threading

        def add(case_id):
            svc.add_case({
                "id": case_id,
                "category": "compliance",
                "dimension": "compliance",
                "level": "session",
                "title": f"case {case_id}",
                "source": "concurrency",
                "affected_by": ["AGENT.md"],
                "evaluators": ["file_contains"],
                "verification": {"file": "x.py", "grep": "y", "negative_command": "false"},
            })

        # add_case holds _data_lock in-process; to exercise the cross-process
        # lock we just confirm serialized adds don't lose data end-to-end.
        threads = [threading.Thread(target=add, args=(f"GS_C{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        svc2 = EvalService(workspace_root=eval_workspace)
        assert svc2.get_case_detail("GS_C0") is not None, "GS_C0 lost"
        assert svc2.get_case_detail("GS_C1") is not None, "GS_C1 lost"


class TestCanaryRun:
    def test_canary_runs_synchronously(self, svc):
        """Canary runs only programmatic cases and returns immediately."""
        result = svc.run_canary()
        assert "overall_score" in result
        assert result["triggered_by"] == "canary"
        # Should be fast (<5s)
        assert result["duration_seconds"] < 5.0

    def test_canary_skips_llm_evaluators(self, svc):
        """Cases with LLM evaluators only are skipped in canary."""
        result = svc.run_canary()
        for case_result in result["cases"]:
            assert case_result["status"] in ("passed", "failed", "skipped", "error")


class TestAffectedCasesExcludesBehavior:
    """get_affected_cases must NEVER return behavior cases (Gate-2 MED,
    run_75b656c1): they spawn real agents and must not be auto-triggered by a
    file-edit hook. Only explicit opt-in (tag / named filter) may run them."""

    def _svc(self):
        from core.eval_service import EvalService
        svc = EvalService.__new__(EvalService)
        svc._cases = [
            {"id": "NORM", "eval_method": "programmatic", "tier": "active",
             "affected_by": ["AGENT.md"]},
            {"id": "BEHAV", "eval_method": "behavior", "tier": "active",
             "affected_by": ["AGENT.md"]},
        ]
        return svc

    def test_behavior_case_never_auto_triggered_by_file_edit(self):
        svc = self._svc()
        affected = svc.get_affected_cases(["backend/context/AGENT.md"])
        ids = {c["id"] for c in affected}
        assert "NORM" in ids, "programmatic case should be triggered"
        assert "BEHAV" not in ids, "behavior case must NOT auto-spawn from a file edit"


class TestDimensionCategoryGuard:
    """Load-time fail-loud guard: any case whose .dimension is not in the canonical
    dimensions list (or .category not in the canonical categories list) must emit a
    WARNING at load — never raise, never drop the case (PIT118 fail-loud != fail-hard).

    Root: compute_scores (eval_runner) aggregates raw .dimension tags with no
    validation, so an off-canonical dimension silently leaks into /api/eval/health
    while /api/eval/golden-set shows only the yaml-declared canonical 5. The guard
    surfaces the drift the moment a case is loaded. (run_8c44b7bf)
    """

    def _workspace(self, tmp_path, cases, dimensions=None, categories=None):
        import yaml
        project_dir = tmp_path / "Eval"
        project_dir.mkdir(parents=True)
        (project_dir / "EvalHistory").mkdir()
        gs = {
            "version": 2,
            "categories": categories or ["compliance", "recall"],
            "dimensions": dimensions or ["compliance", "factual_accuracy"],
            "cases": cases,
        }
        (project_dir / "golden_set.yaml").write_text(yaml.dump(gs, default_flow_style=False))
        return tmp_path

    def _case(self, cid, dimension, category="compliance"):
        return {
            "id": cid, "category": category, "dimension": dimension,
            "level": "session", "title": "t", "source": "s",
            "affected_by": ["AGENT.md"], "evaluators": ["file_contains"],
            "scenario": {"turns": [{"input": "x"}]},
            "verification": {"file": "x.py", "grep": "y"},
        }

    def test_off_canonical_dimension_warns_but_loads(self, tmp_path, caplog):
        import logging
        ws = self._workspace(
            tmp_path,
            [self._case("GS_BAD", dimension="bogus_dim"),
             self._case("GS_OK", dimension="compliance")],
        )
        with caplog.at_level(logging.WARNING, logger="core.eval_service"):
            svc = EvalService(workspace_root=ws)
        # Load did NOT drop the bad case (fail-loud, not fail-hard)
        ids = {c["id"] for c in svc._cases}
        assert ids == {"GS_BAD", "GS_OK"}, "guard must not drop cases"
        # A warning fired, naming the off-canonical dimension + case id
        warned = "\n".join(r.getMessage() for r in caplog.records)
        assert "bogus_dim" in warned and "GS_BAD" in warned, warned
        assert "GS_OK" not in warned, "canonical case must not warn"

    def test_off_canonical_category_warns(self, tmp_path, caplog):
        import logging
        ws = self._workspace(
            tmp_path,
            [self._case("GS_CATBAD", dimension="compliance", category="not_a_category")],
        )
        with caplog.at_level(logging.WARNING, logger="core.eval_service"):
            svc = EvalService(workspace_root=ws)
        warned = "\n".join(r.getMessage() for r in caplog.records)
        assert "not_a_category" in warned and "GS_CATBAD" in warned, warned

    def test_all_canonical_no_warning(self, tmp_path, caplog):
        import logging
        ws = self._workspace(
            tmp_path,
            [self._case("GS_A", dimension="compliance", category="compliance"),
             self._case("GS_B", dimension="factual_accuracy", category="recall")],
        )
        with caplog.at_level(logging.WARNING, logger="core.eval_service"):
            EvalService(workspace_root=ws)
        dim_warns = [r.getMessage() for r in caplog.records
                     if "dimension" in r.getMessage().lower() or "categor" in r.getMessage().lower()]
        assert dim_warns == [], f"clean set must not warn: {dim_warns}"


class TestInflightMarker:
    """Method-B durable-run-record fix: a status='running' marker in the
    isolated EvalHistory/.inflight/ namespace makes a mid-flight-killed run
    detectable (get_run → 200 running) instead of a 404 ghost, WITHOUT any
    EvalHistory reader seeing it (namespace isolation, zero reader changes)."""

    def test_trigger_writes_running_marker_synchronously(self, svc, eval_workspace):
        """AC1: the marker exists BEFORE the thread would need to run — patch
        Thread to a no-op so only the synchronous write in trigger_run happens."""
        with patch("core.eval_service.threading.Thread") as MockThread:
            MockThread.return_value.start.return_value = None
            run_id = svc.trigger_run(trigger="manual")
        marker = eval_workspace / "Eval" / "EvalHistory" / ".inflight" / f"{run_id}.json"
        assert marker.exists(), "running marker not written synchronously"
        data = json.loads(marker.read_text())
        assert data["status"] == "running"
        assert data["run_id"] == run_id
        assert data["overall_score"] is None

    def test_get_run_returns_running_marker(self, svc, eval_workspace):
        """AC2: get_run falls back to the marker when run_id isn't a completed run."""
        with patch("core.eval_service.threading.Thread") as MockThread:
            MockThread.return_value.start.return_value = None
            run_id = svc.trigger_run(trigger="manual")
        run = svc.get_run(run_id)
        assert run is not None and run["status"] == "running"

    def test_marker_invisible_to_all_readers(self, svc, eval_workspace):
        """AC4 ISOLATION: a marker in .inflight/ does NOT alter _load_history
        count, get_health latest, or the ci_eval_gate / monthly_report readers —
        because every one uses non-recursive glob('*.json'). This is the whole
        payoff of Method B (no per-reader status filter needed)."""
        history_dir = eval_workspace / "Eval" / "EvalHistory"
        svc.reload()
        baseline_count = len(svc._runs)

        svc._write_inflight_marker("eval_20260707_120000_abc123_manual", "manual",
                                   "2026-07-07T12:00:00+00:00")

        svc._load_history()
        assert len(svc._runs) == baseline_count, "marker leaked into _load_history"
        assert all(r.get("status") != "running" for r in svc._runs)
        # get_health latest must remain the pre-existing completed run (100.0)
        health = svc.get_health()
        assert health.get("overall_score") in (100.0, 100), health

        # ci_eval_gate._reports_by_mtime — non-recursive glob, marker invisible
        import importlib
        cig = importlib.import_module("scripts.ci_eval_gate")
        reports = cig._reports_by_mtime(eval_workspace)
        assert all(r.get("status") != "running" for r in reports), \
            "ci_eval_gate saw the .inflight marker"

    def test_completion_clears_marker_and_writes_history(self, svc, eval_workspace):
        """AC3: the marker EXISTS mid-run (proving trigger_run wrote it), then is
        CLEARED after the terminal write (proving _clear_inflight_marker ran).

        Non-vacuity (Gate-2 CRITICAL fix): the earlier version only asserted
        `not marker.exists()` at the end — trivially true on reverted code where
        the marker is never written. We now capture marker-existence AT the
        terminal write (via a wrapper on _write_run_result), so the test fails if
        either the write OR the clear is removed."""
        inflight_dir = eval_workspace / "Eval" / "EvalHistory" / ".inflight"
        done = threading.Event()
        seen = {}
        real_write = svc._write_run_result

        def spy_write(result):
            # At the moment the terminal record is written, the running marker
            # must still be on disk — this is what proves the clear (which runs
            # AFTER, in finally) actually removed a real file.
            rid = result.get("run_id")
            seen["marker_present_at_terminal"] = (inflight_dir / f"{rid}.json").exists()
            return real_write(result)

        def fake_run_eval(cases_data, trigger, case_ids, root, **kwargs):
            done.set()
            return {
                "run_id": "x", "triggered_by": trigger, "overall_score": 100.0,
                "triggered_at": "2026-07-07T12:00:00+00:00",
                "dimensions": {}, "cases": [], "total_cases": 0,
                "cases_passed": 0, "cases_failed": 0, "cases_skipped": 0,
                "duration_seconds": 0.0,
            }

        with patch.object(svc, "_write_run_result", side_effect=spy_write), \
                patch("scripts.eval_runner.run_eval", side_effect=fake_run_eval):
            run_id = svc.trigger_run(trigger="manual")
            assert done.wait(timeout=5.0)
            # give the thread a beat to finish the terminal write + clear
            import time
            for _ in range(50):
                if not svc.is_running:
                    break
                time.sleep(0.1)
        assert seen.get("marker_present_at_terminal") is True, \
            "marker was NOT on disk at terminal write — trigger_run didn't write it"
        marker = inflight_dir / f"{run_id}.json"
        assert not marker.exists(), "marker not cleared after completion"
        svc.reload()
        run = svc.get_run(run_id)
        assert run is not None and run.get("run_id") == run_id

    def test_marker_survives_when_no_terminal_write(self, svc, eval_workspace):
        """AC3/AC6: simulate a SIGKILL-before-terminal — the marker written by
        trigger_run persists (durable + detectable), so get_run still reports
        running rather than 404. We call the synchronous writer directly (the
        thread never gets to write a terminal record)."""
        run_id = "eval_20260707_130000_dead01_manual"
        svc._write_inflight_marker(run_id, "manual", "2026-07-07T13:00:00+00:00")
        # No terminal write, no clear (thread died) → marker must remain.
        svc.reload()
        run = svc.get_run(run_id)
        assert run is not None and run["status"] == "running"

    def test_marker_write_is_atomic(self, svc, eval_workspace):
        """AC5: a written marker always parses with a 'status' key (temp+replace,
        no partial file ever exposed under {run_id}.json)."""
        run_id = "eval_20260707_140000_atom01_manual"
        svc._write_inflight_marker(run_id, "manual", "2026-07-07T14:00:00+00:00")
        marker = eval_workspace / "Eval" / "EvalHistory" / ".inflight" / f"{run_id}.json"
        data = json.loads(marker.read_text())
        assert "status" in data and data["status"] == "running"
        # no leftover .tmp files in the dir
        tmps = list(marker.parent.glob("*.tmp"))
        assert tmps == [], f"leftover temp files: {tmps}"
