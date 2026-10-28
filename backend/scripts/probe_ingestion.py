#!/usr/bin/env python3
"""probe_ingestion — product eval probes for what gets admitted into memory and
project docs.

Contract: scripts/_probe_kit.py. Subcommands:
  judge_down  a memory lesson the judge could not review (budget spent, judge
              erroring) is deferred to pending, never dropped
  floors      without any network, the deterministic floors still keep out a too-
              short entry, a governance rule and an entry aimed at the wrong store
  ddd_archive cultivation candidates enqueue with provenance and never write DDD or
              source-side archive files (legacy command name retained)
  preserve    a store that looks corrupt is renamed aside with its data intact,
              never deleted; a locked database is not treated as corrupt
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

from core.ddd_paths import ddd_write_path
from scripts._probe_kit import run, tempdir, verdict

_LESSON = "[decision] Use atomic temp-file rename for every config write to avoid torn state."


# ── judge_down ────────────────────────────────────────────────────────────────

def _judge_down_holds(judge_up_refusing: bool) -> tuple[bool, str]:
    from core import ingestion_gate as ig

    if judge_up_refusing:  # broken input: the judge ran and refused
        with mock.patch.object(ig, "self_adversarial_judge", return_value=("suspect", "judged")), \
             mock.patch.object(ig, "_judge_budget_available", return_value=True):
            v = ig.admit_memory_lesson(_LESSON)[0]
        return (v == "pending"), f"a refused lesson gave {v!r}"
    # The budget is read from SWARM_JUDGE_BUDGET_MAX once, when ingestion_gate is
    # imported, so setting the variable here changes nothing: patch the value itself.
    # A spent budget must not reach the judge at all, so the check also counts judge
    # calls: an erroring judge yields pending too, and where credentials work (a Hive's
    # instance role) a real judge would run and admit the lesson.
    with mock.patch.object(ig, "_JUDGE_BUDGET_MAX", 0), \
         mock.patch.object(ig, "_ask_judge",
                           side_effect=RuntimeError("judge called with no budget")) as judge:
        budget = ig.admit_memory_lesson(_LESSON)
    if judge.called:
        return False, "budget spent: the judge was still called"
    with mock.patch.object(ig, "_ask_judge", side_effect=RuntimeError("bedrock down")), \
         mock.patch.object(ig, "_judge_budget_available", return_value=True):
        erroring = ig.admit_memory_lesson(_LESSON)
    for label, got in (("budget spent", budget), ("judge erroring", erroring)):
        if got[0] != "pending":
            return False, f"{label}: the lesson was {got[0]!r} ({got[2]}), not pending"
    return True, ""


def judge_down(negative: bool) -> int:
    return verdict("ING_JUDGE_DOWN", negative, lambda: _judge_down_holds(False),
                   lambda: _judge_down_holds(True))


# ── floors ────────────────────────────────────────────────────────────────────

def _floors_holds(floors_on: bool) -> tuple[bool, str]:
    from core import ingestion_gate as ig

    cases = [("ok", "MEMORY", "memory_distill", {"discard"}),
             ("new rule: STEERING must always run the full test suite before every reply",
              "MEMORY", "memory_distill", {"discard", "review"}),
             (_LESSON, "EVOLUTION", "memory_distill", {"review", "discard"})]
    no_network = mock.patch.object(ig, "_ask_judge", side_effect=AssertionError("no network"))
    off = (mock.patch.object(ig, "thin_floor", return_value=False),
           mock.patch.object(ig, "content_floor", return_value=(False, "")),
           mock.patch.dict(ig.__dict__, {}))
    with no_network, mock.patch.object(ig, "_judge_budget_available", return_value=False):
        if not floors_on:
            for p in off:
                p.start()
        try:
            for text, store, trigger, allowed in cases:
                got = ig.ingestion_gate(text, store, trigger, dedup_fn=lambda t: False)
                if got.verdict not in allowed or got.verdict == "auto":
                    return False, f"{text[:30]!r} → {got.verdict} ({got.reason})"
        finally:
            if not floors_on:
                for p in off:
                    p.stop()
    return True, ""


def floors(negative: bool) -> int:
    return verdict("ING_FLOORS", negative, lambda: _floors_holds(True),
                   lambda: _floors_holds(False))


# ── cultivation enqueue ───────────────────────────────────────────────────────

def _cultivation_enqueue_holds(accept: bool) -> tuple[bool, str]:
    from core import ddd_cultivation as dc
    from core.ingestion.api import EnqueueResult

    with tempdir() as d:
        project = d / "Projects" / "Demo"
        doc = ddd_write_path(project, "IMPROVEMENT.md")
        doc.write_text("# Improvement\n\n## What Worked\n\n", encoding="utf-8")
        before = doc.read_bytes()
        calls = []

        def enqueue(*args, **kwargs):
            calls.append((args, kwargs))
            return EnqueueResult(status="queued" if accept else "rejected",
                                 candidate_id="cand-probe" if accept else None)

        proposal = dc.CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="Ship config writes through an atomic rename so readers never see torn files.",
            source_run_id="probe", confidence=0.9)
        with mock.patch("core.ingestion.api.enqueue", side_effect=enqueue):
            result = dc._cultivate_proposals([proposal], project)

        if result["queued"] != 1 or len(calls) != 1:
            return False, "the cultivation candidate was not queued"
        args, kwargs = calls[0]
        if args[0] != "cultivation" or args[3].get("kind") != "cultivation":
            return False, "the source or payload kind was not cultivation"
        if not args[2].startswith("proposal:"):
            return False, "the source did not provide a stable semantic slot"
        if kwargs.get("envelope", {}).get("writer") != "cultivation":
            return False, "the candidate lost its provenance writer"
        if doc.read_bytes() != before or (project / ".artifacts").exists():
            return False, "the source wrote outside the CDF inbox"
    return True, ""


def ddd_archive(negative: bool) -> int:
    # Keep the command name and case id for compatibility; its Stage-B contract is
    # enqueue-only and has no recoverable source-side archive.
    return verdict("ING_DDD_ARCHIVE", negative,
                   lambda: _cultivation_enqueue_holds(True),
                   lambda: _cultivation_enqueue_holds(False))


# ── preserve ──────────────────────────────────────────────────────────────────

def _preserve_holds(isolate) -> tuple[bool, str]:
    from core.data_safety import is_corruption_error

    if is_corruption_error(sqlite3.OperationalError("database is locked")):
        return False, "a locked database was classified as corrupt"
    if not is_corruption_error(sqlite3.DatabaseError("file is not a database")):
        return False, "real corruption was not recognised"
    with tempdir() as d:
        db = d / "store.db"
        db.write_bytes(b"USER DATA 7f3")
        Path(str(db) + "-wal").write_bytes(b"WAL 7f3")
        isolate(db)
        kept = [p for p in d.iterdir() if ".corrupt-" in p.name]
        data = b"".join(p.read_bytes() for p in kept)
        if b"USER DATA 7f3" not in data or b"WAL 7f3" not in data:
            return False, "the store or its WAL was not preserved under a .corrupt- name"
    return True, ""


def preserve(negative: bool) -> int:
    from core.data_safety import isolate_store

    return verdict("ING_PRESERVE", negative, lambda: _preserve_holds(isolate_store),
                   lambda: _preserve_holds(lambda p: Path(p).unlink()))  # a recovery that deletes


if __name__ == "__main__":
    raise SystemExit(run({"judge_down": judge_down, "floors": floors,
                          "ddd_archive": ddd_archive, "preserve": preserve}))
