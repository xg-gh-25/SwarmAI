"""Governance audit marker — "the default model changed, re-audit the governed files".

Instructions written for an older model become dead weight or friction on a newer
one (Anthropic's single-agent-loop migration guidance: delete by default). The one
periodic trigger for a prompt audit is a change of the DEFAULT model
(docs/design/2026-10-26-evolution-loop-admission.md §3.5). Per-tab / per-channel
model overrides are ignored: comparing them would flap between sessions.

State: ``<workspace>/.context/.governance_audit.json``
    {"baseline_model": "<model at first start>", "audited_model": "<model last audited>"}

- ``record_baseline`` — daemon startup; writes ``baseline_model`` only when the file
  does not exist or cannot be read (a first install gets no hint; nothing has been audited, so it is
  never recorded as ``audited_model``).
- ``audit_hint`` — pure read, for the session briefing. Never writes.
- ``record_audit`` — the s_context-hygiene audit, after the user approved its edits:
  ``python -m core.governance_audit mark``.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE = Path(".context") / ".governance_audit.json"


def _path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir) / _STATE


def _read(workspace_dir: str | Path) -> dict | None:
    p = _path(workspace_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(workspace_dir: str | Path, data: dict) -> None:
    p = _path(workspace_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", dir=p.parent, suffix=".tmp", delete=False, encoding="utf-8")
    try:
        json.dump(data, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, p)
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def record_baseline(workspace_dir: str | Path, default_model: str | None) -> bool:
    """Write ``baseline_model`` when no readable state exists yet. Returns True if written."""
    if not default_model:
        return False
    if _path(workspace_dir).exists():
        if _read(workspace_dir) is not None:
            return False
        # An unreadable file would silence the hint forever: start over.
        logger.warning("governance audit state unreadable; rewriting the baseline")
    _write(workspace_dir, {"baseline_model": default_model})
    return True


def audit_hint(workspace_dir: str | Path, default_model: str | None) -> str | None:
    """One briefing line when the default model differs from the last audited one."""
    state = _read(workspace_dir)
    if not state or not default_model:
        return None
    last = state.get("audited_model") or state.get("baseline_model")
    if not last or last == default_model:
        return None
    return (
        f"- Default model changed to {default_model}; the governance files were last "
        f"audited for {last}. Run a prompt audit (s_context-hygiene) for instructions "
        f"written for the older model."
    )


def record_audit(workspace_dir: str | Path, model: str) -> None:
    """Record that the governed files were audited (and the edits approved) for ``model``."""
    state = _read(workspace_dir) or {}
    state["audited_model"] = model
    _write(workspace_dir, state)


def _main(argv: list[str]) -> int:
    if argv[:1] != ["mark"]:
        print("usage: python -m core.governance_audit mark [--model M] [--workspace DIR]", file=sys.stderr)
        return 2
    args = dict(zip(argv[1::2], argv[2::2]))
    if "--workspace" in args:
        workspace = Path(args["--workspace"])
    else:
        from core.project_registry import get_swarmws
        workspace = get_swarmws()
    model = args.get("--model")
    if not model:
        from core.app_config_manager import AppConfigManager
        mgr = AppConfigManager.instance()
        mgr.load()
        model = mgr.get("default_model")
    if not model:
        print("no default_model configured", file=sys.stderr)
        return 1
    record_audit(workspace, model)
    print(json.dumps({"ok": True, "audited_model": model, "path": str(_path(workspace))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
