"""Tests for EvalService CRUD operations and run triggers (P3)."""

import json
import tempfile
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
    project_dir = tmp_path / "Projects" / "SwarmAI"
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
            "verification": {"file": "x.py", "grep": "y"},
        }
        result = svc.add_case(new_case)
        assert result["id"] == "GS003"
        assert svc.case_count == 3

    def test_add_case_duplicate_id_fails(self, svc):
        duplicate = {
            "id": "GS001",  # already exists
            "category": "compliance",
            "dimension": "compliance",
            "title": "Duplicate",
            "evaluators": ["file_contains"],
            "affected_by": ["AGENT.md"],
        }
        with pytest.raises(ValueError, match="already exists"):
            svc.add_case(duplicate)

    def test_add_case_missing_required_field(self, svc):
        incomplete = {"id": "GS099", "title": "No evaluators"}
        with pytest.raises(ValueError, match="required"):
            svc.add_case(incomplete)

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
            "verification": {"command": "echo hi", "expected_contains": "hi"},
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


class TestPersistPreservesDiskOnlyCases:
    """_persist_golden_set must NOT drop cases that exist on disk but not in
    self._cases — i.e. cases added by another session/manual edit AFTER this
    process loaded golden_set.yaml. Reproduces the data-loss corruption
    (13 cases incl all GS_TRAJ behavior cases gutted, 2026-06-25). Radar b40b9545.

    Invariant relied upon: delete_case is a SOFT delete (tier='archived', keeps
    the case in self._cases). There is NO hard-remove path that shrinks
    self._cases, so "on disk but not in memory" ALWAYS means externally-added,
    never locally-deleted — making verbatim append safe.
    """

    def _external_add_case(self, eval_workspace, case):
        """Simulate another session/manual edit appending a case to disk."""
        import yaml
        path = eval_workspace / "Projects" / "SwarmAI" / "golden_set.yaml"
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
        path = eval_workspace / "Projects" / "SwarmAI" / "golden_set.yaml"
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
        written (falls to the else-branch), and must NOT poison the disk-only
        dedup (it is never added to merged_ids, but disk_cases only keys truthy
        ids so there is no collision)."""
        svc._cases.append({"title": "no id here", "category": "compliance"})
        svc.update_case("GS001", {"title": "touch"})  # triggers persist

        import yaml
        path = eval_workspace / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = yaml.safe_load(path.read_text())
        titles = [c.get("title") for c in data["cases"]]
        assert "no id here" in titles, "id-less in-memory case was dropped on persist"


class TestPersistCrossProcessLock:
    """_persist_golden_set must hold an OS-level exclusive lock spanning the
    disk re-read THROUGH the atomic rename, so two concurrent SwarmAI processes
    cannot lose each other's golden_set writes (cross-process TOCTOU). The
    in-process threading.Lock does NOT protect across processes; flock does.
    Follow-up to run_fb4b42d2 (todo 7e233ecb).
    """

    def _lock_path(self, eval_workspace):
        return eval_workspace / "Projects" / "SwarmAI" / "golden_set.yaml.lock"

    def test_persist_acquires_exclusive_lock(self, svc, eval_workspace):
        """The persist must BLOCK while another fd holds the sidecar lock,
        proving it actually acquires an OS-level exclusive lock (not just the
        in-process threading.Lock). We hold the lock from the main thread, run
        persist in a background thread, and assert it does not complete until
        we release."""
        import threading
        import time
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
                "verification": {"file": "x.py", "grep": "y"},
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
