"""Where a v1 run's protected contract and integration branches live, and reading them.

Moved out of the retired test runner (``runner.py``, S6-1b): the engine, the gates and
the worktree code read a run's contract; none of them ran tests.

Public symbols:
- ``ContractError`` -- the contract is unreadable or is not this run's v1 contract
- ``contract_path`` / ``read_contract`` / ``is_v1_run``
- ``integration_branches_path``
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ContractError(RuntimeError):
    """A run's contract cannot be located or read safely."""


def contract_path(data_dir: str | Path, run_id: str) -> Path:
    if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id or ""):
        raise ContractError("unsafe run id")
    return Path(data_dir) / "state" / "pipeline_agent_audit" / "runs" / run_id / "contract.json"


def read_contract(data_dir: str | Path, run_id: str) -> dict[str, Any] | None:
    path = contract_path(data_dir, run_id)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"v1 contract is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("run_id") != run_id \
            or value.get("protocol_version") != 1:
        raise ContractError("v1 contract identity/version mismatch")
    return value


def is_v1_run(data_dir: str | Path, run_id: str) -> bool:
    return read_contract(data_dir, run_id) is not None


def integration_branches_path(data_dir: str | Path, run_id: str) -> Path:
    """Where the engine records each repo's integration branch at worktree creation
    (the audit area: agents cannot write it)."""
    return contract_path(data_dir, run_id).parent / "integration_branches.json"

