"""Tests permanent snapshot retention and authorized disposal.

Methodology: temporary git branch and ledger, real AuthorizationOracle consumption.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from core.run_protocol import auth
from core.run_protocol.retention import RetentionError, discard_snapshot, snapshot_id


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _rig(tmp_path, monkeypatch):
    main = tmp_path / "main"; main.mkdir()
    _git(main, "init", "-q"); _git(main, "config", "user.email", "a@b.c"); _git(main, "config", "user.name", "T")
    (main / "a").write_text("a"); _git(main, "add", "."); _git(main, "commit", "-qm", "base")
    _git(main, "branch", "run/run_one")
    root = tmp_path / "ledger"; monkeypatch.setenv("SWARM_WORKTREE_LEDGER", str(root))
    ledger = root / "run_one" / "repo"; ledger.mkdir(parents=True)
    (ledger / "meta.json").write_text(json.dumps({"snapshot": "a" * 40}))
    return main, ledger


def _consume(ledger, snapshot=None):
    oracle = auth.AuthorizationOracle()
    request = auth.make_request(request_id="q", session_id="s", run_id="run_one",
                                action="discard_snapshot",
                                bindings={"snapshot_id": snapshot or snapshot_id(ledger)})
    oracle.issue(request, auth_event_id="a" * 64, decided_at=0, expires_at=100)
    return oracle.consume(request_id="q", session_id="s", run_id="run_one",
                          action="discard_snapshot",
                          bindings={"snapshot_id": snapshot or snapshot_id(ledger)}, now=1).consumption


def test_only_matching_consumption_deletes_branch_and_ledger(tmp_path, monkeypatch):
    main, ledger = _rig(tmp_path, monkeypatch)
    consumption = _consume(ledger)
    discard_snapshot(consumption=consumption, run_id="run_one", repo_name="repo",
                     main=main, ledger=ledger)
    assert not ledger.exists()
    assert _git(main, "branch", "--list", "run/run_one") == ""


def test_wrong_snapshot_binding_is_rejected_without_deletion(tmp_path, monkeypatch):
    main, ledger = _rig(tmp_path, monkeypatch)
    consumption = _consume(ledger, snapshot="wrong")
    with pytest.raises(RetentionError, match="another snapshot"):
        discard_snapshot(consumption=consumption, run_id="run_one", repo_name="repo",
                         main=main, ledger=ledger)
    assert ledger.exists() and _git(main, "branch", "--list", "run/run_one")
