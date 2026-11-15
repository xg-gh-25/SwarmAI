"""Drift between a seeded context file and the product template it was copied from.

Seeded context files (``CONTEXT_FILES`` entries with ``user_customized=True``) are
copied from ``backend/context/`` only when the live file is missing, and the live copy
is authoritative afterwards. For most of them the template is only a starting
skeleton: USER, STEERING and TOOLS are the person's, MEMORY, EVOLUTION, TIMELINE and
KNOWLEDGE are written by the agent and CDF, so they differ from their template by
design and a new skeleton is nothing to ask the person about.

A few seeded files are PRODUCT-OWNED: the template IS the content (today only
SELF.md, the self-portrait). They are declared by ``ContextFileSpec.template_owned``
(``tracked_files``); nothing else is checked, recorded or offered. Without a record
of what was copied, a later product change to such a template never reached an
existing install and nobody was told: an install kept injecting a months-old SELF.md
that named mechanisms the code no longer has.

This module keeps that record and turns a template change into ONE Need-You item per
file. It never writes a context file on its own. Where it runs: ``ensure_directory``
records the seed when it copies a product-owned template;
``initialization_manager.refresh_context_files`` runs ``check`` at every startup (on
the io pool, off the event loop); ``attention_authority._collect_template_drift``
shows the pending drifts as review-tier items (brain=None). An unedited copy's item
carries the template diff and the file is written only when the person confirms; an
edited copy's item offers a three-way view (template when copied, new template, live
file) to merge, and the file is never written:

- ``record_seed`` — ``ensure_directory`` calls it when it copies a template: the
  template's sha256 becomes the file's baseline, and the template text is kept in
  ``.template-seeds/<file>.seed`` so a later three-way view has the old version.
- ``check`` — run at startup. Per file: live equal to the template → in sync (the
  baseline moves to the template); template equal to the baseline → nothing changed;
  otherwise one pending drift, of kind ``untouched`` (live still equals the baseline),
  ``edited`` (live differs from the baseline) or ``unrecorded`` (no baseline was ever
  recorded — an install older than this module — and live differs from the template).
  A drift already pending for the same template hash and kind is left as it is, so
  the same drift never raises a second item; a newer template replaces it.
- ``apply`` — the person said yes to an ``untouched`` item: write the new template,
  only while the live file still hashes to the baseline (re-checked under the file's
  ``md_lock``; a hand edit is not locked, so one landing between that re-check and
  the atomic replace is not excluded). An edited or unrecorded file is never overwritten: the person merges,
  then ``resolve``. The person's yes is asked for in chat, as for every Need-You item
  (the channel has no act endpoint); it is not a code gate. What apply can write is
  bounded: the product's current template, over a file that is byte-equal to the
  template it was copied from.
- ``resolve`` — the person kept their copy or merged by hand: the current template
  becomes the baseline and the item goes.

State lives beside the context files in ``.template-seeds.json``::

    {"version": 1, "files": {"SELF.md": {"template_sha256": "<hex>" | null,
                                          "recorded_at": "<iso>",
                                          "pending": null | {"template_sha256": "<hex>",
                                                             "kind": "untouched|edited|unrecorded",
                                                             "raised_at": "<iso>"}}}}

``core.attention_authority._collect_template_drift`` reads it (read-only) and the
CLI below is the confirm path: ``python -m core.context_template_drift
{status,show,apply,resolve} [FILE ...]``.
"""

from __future__ import annotations

import argparse
import difflib
import re
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from utils.file_lock import md_lock

logger = logging.getLogger(__name__)

STATE_FILENAME = ".template-seeds.json"
SNAPSHOT_DIRNAME = ".template-seeds"
STATE_VERSION = 1

KIND_UNTOUCHED = "untouched"
KIND_EDITED = "edited"
KIND_UNRECORDED = "unrecorded"

# A template-change diff is shown in the Need-You message whole when it is at most
# this long; a longer one is named (its length + the command that prints it).
INLINE_DIFF_MAX = 4000

_SEEDED_PERM = 0o644
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def tracked_files() -> list[str]:
    """The product-owned seeded context files (``template_owned``), in CONTEXT_FILES
    order: the only files this module records, checks, shows or writes."""
    from core.context_directory_loader import CONTEXT_FILES  # local: the loader imports us lazily

    return [spec.filename for spec in CONTEXT_FILES
            if spec.user_customized and spec.template_owned]


def default_templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "context"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(context_dir: Path) -> Path:
    return Path(context_dir) / STATE_FILENAME


def _snapshot_path(context_dir: Path, filename: str) -> Path:
    return Path(context_dir) / SNAPSHOT_DIRNAME / f"{filename}.seed"


def _atomic_write(dest: Path, data: bytes, perm: int = _SEEDED_PERM) -> None:
    """temp file in the same directory → fsync → os.replace → chmod."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        os.chmod(dest, perm)
    except OSError:
        pass  # best-effort on non-Unix


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "files": {}}


def _sha_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _SHA_RE.fullmatch(value) else None


def _normalize(data: Any) -> dict[str, Any]:
    """Keep only well-formed entries: a file record is a dict whose baseline is a
    sha256 or None and whose pending is None or a dict with a sha256 and a known kind.
    Anything else is dropped (that file then reads as unrecorded), so a hand-edited or
    damaged state can never crash the seed path or the check."""
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("not a template-seeds state object")
    files: dict[str, Any] = {}
    for name, rec in data["files"].items():
        if not isinstance(name, str) or not isinstance(rec, dict):
            continue
        clean = {"template_sha256": _sha_or_none(rec.get("template_sha256")),
                 "recorded_at": rec.get("recorded_at") if isinstance(rec.get("recorded_at"), str) else None,
                 "pending": None}
        pending = rec.get("pending")
        if (isinstance(pending, dict) and _sha_or_none(pending.get("template_sha256"))
                and pending.get("kind") in (KIND_UNTOUCHED, KIND_EDITED, KIND_UNRECORDED)):
            clean["pending"] = {
                "template_sha256": pending["template_sha256"],
                "kind": pending["kind"],
                "raised_at": pending.get("raised_at") if isinstance(pending.get("raised_at"), str) else None,
            }
        files[name] = clean
    return {"version": STATE_VERSION, "files": files}


def _read_state(context_dir: Path, *, isolate_corrupt: bool) -> dict[str, Any]:
    """The state (well-formed entries only), or an empty one. An unparseable file is
    moved aside (never deleted) when *isolate_corrupt*; a reader that must not write
    just sees an empty state."""
    path = _state_path(context_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _empty_state()
    try:
        return _normalize(json.loads(raw.decode("utf-8")))
    except ValueError as exc:  # also UnicodeDecodeError and JSONDecodeError
        if isolate_corrupt:
            aside = path.with_name(f"{path.name}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
            try:
                os.replace(path, aside)
                logger.warning("template drift: unreadable state moved to %s (%s)", aside, exc)
            except OSError as move_exc:
                logger.warning("template drift: unreadable state %s could not be moved: %s",
                               path, move_exc)
        return _empty_state()


def _write_state(context_dir: Path, state: dict[str, Any]) -> None:
    data = json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
    _atomic_write(_state_path(context_dir), data + b"\n")


def _set_baseline(context_dir: Path, rec: dict[str, Any], filename: str,
                  template_bytes: bytes) -> None:
    _atomic_write(_snapshot_path(context_dir, filename), template_bytes)
    rec["template_sha256"] = _sha256(template_bytes)
    rec["recorded_at"] = _now()
    rec["pending"] = None


def record_seed(context_dir: Path, filename: str, template_bytes: bytes) -> None:
    """A template was just copied to *filename*: it is the file's baseline now."""
    context_dir = Path(context_dir)
    with md_lock(_state_path(context_dir)):
        state = _read_state(context_dir, isolate_corrupt=True)
        rec = state["files"].setdefault(filename, {})
        _set_baseline(context_dir, rec, filename, template_bytes)
        _write_state(context_dir, state)


def _evaluate(rec: Optional[dict[str, Any]], template_sha: str,
              live_sha: str) -> tuple[str, Optional[str]]:
    """What the check does for one file: ``("baseline", None)`` (live equals the
    template: record it), ``("clear", None)`` (no drift), or ``("pending", kind)``."""
    baseline = (rec or {}).get("template_sha256")
    if live_sha == template_sha:
        return "baseline", None
    if baseline == template_sha:
        return "clear", None
    if baseline is None:
        return "pending", KIND_UNRECORDED
    return "pending", (KIND_UNTOUCHED if live_sha == baseline else KIND_EDITED)


def check(context_dir: Path, templates_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Compare every product-owned seeded file (``tracked_files``) with its template and
    record at most one pending drift per file. Entries for any other file (left by an
    earlier version that tracked every seeded file) are dropped from the state. Writes
    only the state file (and a baseline snapshot), never a context file. Returns the
    drifts pending after the check."""
    context_dir = Path(context_dir)
    templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
    with md_lock(_state_path(context_dir)):
        state = _read_state(context_dir, isolate_corrupt=True)
        files = state["files"]
        covered = tracked_files()
        stale = [name for name in files if name not in covered]
        for name in stale:
            del files[name]  # never shown, applied or reused as a baseline
        changed = bool(stale)
        for filename in covered:
            src = templates_dir / filename
            dest = context_dir / filename
            if not src.is_file() or not dest.is_file():
                continue  # nothing seeded (yet), or no template for it
            try:
                template_bytes = src.read_bytes()
                live_sha = _sha256(dest.read_bytes())
            except OSError as exc:
                logger.warning("template drift: cannot read %s: %s", filename, exc)
                continue
            template_sha = _sha256(template_bytes)
            rec = files.get(filename)
            action, kind = _evaluate(rec, template_sha, live_sha)
            try:
                if action == "baseline":
                    if rec is None or rec.get("template_sha256") != template_sha or rec.get("pending"):
                        rec = files.setdefault(filename, {})
                        _set_baseline(context_dir, rec, filename, template_bytes)
                        changed = True
                elif action == "clear":
                    if rec.get("pending"):
                        rec["pending"] = None
                        changed = True
                else:
                    pending = (rec or {}).get("pending") or {}
                    if pending.get("template_sha256") == template_sha and pending.get("kind") == kind:
                        continue  # the same drift is already raised
                    rec = files.setdefault(filename, {"template_sha256": None})
                    rec.setdefault("template_sha256", None)
                    same_drift = pending.get("template_sha256") == template_sha
                    rec["pending"] = {
                        "template_sha256": template_sha,
                        "kind": kind,
                        "raised_at": pending.get("raised_at") if same_drift else _now(),
                    }
                    changed = True
            except OSError as exc:
                logger.warning("template drift: cannot record %s: %s", filename, exc)
        if changed:
            _write_state(context_dir, state)
    return pending_drifts(context_dir)


def pending_drifts(context_dir: Path) -> list[dict[str, Any]]:
    """Read-only: one row per file with a pending drift, in CONTEXT_FILES order."""
    files = _read_state(Path(context_dir), isolate_corrupt=False)["files"]
    rows = []
    for filename in tracked_files():
        rec = files.get(filename)
        pending = rec.get("pending") if isinstance(rec, dict) else None
        if not isinstance(pending, dict) or pending.get("kind") not in (
                KIND_UNTOUCHED, KIND_EDITED, KIND_UNRECORDED):
            continue
        rows.append({
            "file": filename,
            "kind": pending["kind"],
            "template_sha256": str(pending.get("template_sha256") or ""),
            "baseline_sha256": rec.get("template_sha256"),
            "raised_at": pending.get("raised_at"),
        })
    return rows


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _diff(a: str, b: str, a_label: str, b_label: str) -> str:
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), a_label, b_label))


def _baseline_text(context_dir: Path, filename: str) -> Optional[str]:
    """The kept text of the template *filename* was copied from, only when it still
    hashes to the recorded baseline: the snapshot sits in a writable workspace, so a
    text that does not match is not product text and is not used."""
    rec = _read_state(Path(context_dir), isolate_corrupt=False)["files"].get(filename) or {}
    baseline = rec.get("template_sha256")
    try:
        data = _snapshot_path(Path(context_dir), filename).read_bytes()
    except OSError:
        return None
    if baseline is None or _sha256(data) != baseline:
        return None
    return data.decode("utf-8", errors="replace")


def template_change_diff(context_dir: Path, filename: str,
                         templates_dir: Optional[Path] = None) -> Optional[str]:
    """The diff from the baseline template to the current one (product text only),
    or None when no verified baseline text is kept (an unrecorded file)."""
    templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
    old = _baseline_text(context_dir, filename)
    new = _read_text(templates_dir / filename)
    if old is None or new is None:
        return None
    return _diff(old, new, f"template {filename} (when copied)", f"template {filename} (now)")


def view(context_dir: Path, filename: str, templates_dir: Optional[Path] = None) -> str:
    """The whole view of one pending drift, for the person (via the agent)."""
    context_dir = Path(context_dir)
    templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
    row = next((r for r in pending_drifts(context_dir) if r["file"] == filename), None)
    if row is None:
        return f"{filename}: nothing pending."
    live_path = context_dir / filename
    new_path = templates_dir / filename
    old_path = _snapshot_path(context_dir, filename)
    live = _read_text(live_path) or ""
    new = _read_text(new_path) or ""
    old = _baseline_text(context_dir, filename)
    out = [f"{filename}: {row['kind']}",
           f"live file:              {live_path}",
           f"new template:           {new_path}"]
    if old is not None:
        out.append(f"template when copied:   {old_path}")
    if row["kind"] == KIND_UNTOUCHED and old is not None:
        out += ["", "== What apply changes in the live file (it still equals the template it was copied from) ==",
                _diff(old, new, f"{filename} (live = template when copied)", f"{filename} (new template)")]
    elif row["kind"] == KIND_EDITED and old is not None:
        out += ["", "== 1. What the product changed (template when copied -> new template) ==",
                _diff(old, new, "template when copied", "new template"),
                "", "== 2. The local edits (template when copied -> live file) ==",
                _diff(old, live, "template when copied", "live file"),
                "", "== 3. Live file -> new template ==",
                _diff(live, new, "live file", "new template")]
    else:
        out += ["", "== Live file -> new template (the template it was copied from is not known) ==",
                _diff(live, new, "live file", "new template")]
    return "\n".join(out)


def apply(context_dir: Path, filename: str,
          templates_dir: Optional[Path] = None) -> tuple[bool, str]:
    """Write the new template over an UNTOUCHED live file, on the person's yes. Only a
    product-owned file (``tracked_files``) is ever written."""
    if filename not in tracked_files():
        return False, f"{filename}: not a product-owned context file; nothing changed."
    context_dir = Path(context_dir)
    templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
    with md_lock(_state_path(context_dir)):
        state = _read_state(context_dir, isolate_corrupt=True)
        rec = state["files"].get(filename)
        pending = rec.get("pending") if isinstance(rec, dict) else None
        if not pending:
            return False, f"{filename}: nothing pending."
        if pending.get("kind") != KIND_UNTOUCHED:
            return False, (f"{filename}: the live file is not the template it was copied from "
                           f"({pending.get('kind')}); it is never overwritten. Merge what the person "
                           f"wants by hand, then run resolve {filename}.")
        dest = context_dir / filename
        try:
            template_bytes = (templates_dir / filename).read_bytes()
        except OSError as exc:
            return False, f"{filename}: cannot read the template: {exc}"
        if _sha256(template_bytes) != pending.get("template_sha256"):
            return False, (f"{filename}: the template changed again since the item was raised; "
                           "restart SwarmAI so the check records the newer one.")
        with md_lock(dest):
            try:
                live_sha = _sha256(dest.read_bytes())
            except OSError as exc:
                return False, f"{filename}: cannot read the live file: {exc}"
            if live_sha != rec.get("template_sha256"):
                pending["kind"] = KIND_EDITED
                _write_state(context_dir, state)
                return False, (f"{filename}: the live file was edited since the item was raised; "
                               f"nothing was written. Merge by hand, then run resolve {filename}.")
            _atomic_write(dest, template_bytes, _SEEDED_PERM)
        _set_baseline(context_dir, rec, filename, template_bytes)
        _write_state(context_dir, state)
    return True, f"{filename}: updated to the new template."


def resolve(context_dir: Path, filename: str,
            templates_dir: Optional[Path] = None) -> tuple[bool, str]:
    """The person kept their copy or merged by hand: the current template becomes the
    baseline and the item goes. Writes no context file. Only a product-owned file."""
    if filename not in tracked_files():
        return False, f"{filename}: not a product-owned context file; nothing changed."
    context_dir = Path(context_dir)
    templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
    with md_lock(_state_path(context_dir)):
        state = _read_state(context_dir, isolate_corrupt=True)
        rec = state["files"].get(filename)
        if not isinstance(rec, dict) or not rec.get("pending"):
            return False, f"{filename}: nothing pending."
        try:
            template_bytes = (templates_dir / filename).read_bytes()
        except OSError as exc:
            return False, f"{filename}: cannot read the template: {exc}"
        _set_baseline(context_dir, rec, filename, template_bytes)
        _write_state(context_dir, state)
    return True, f"{filename}: resolved; the live file was left as it is."


def _default_context_dir() -> Path:
    from jobs.paths import SWARMWS

    return Path(SWARMWS) / ".context"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.context_template_drift",
        description="Product-owned seeded context files (template_owned, today SELF.md) "
                    "whose template changed since they were copied.")
    parser.add_argument("command", choices=("status", "show", "apply", "resolve"))
    parser.add_argument("files", nargs="*", help="context file names, e.g. SELF.md")
    parser.add_argument("--context-dir", type=Path, default=None)
    parser.add_argument("--templates-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    context_dir = args.context_dir or _default_context_dir()
    if args.command == "status":
        rows = pending_drifts(context_dir)
        if not rows:
            print("No template drift pending.")
        for r in rows:
            print(f"{r['file']}: {r['kind']} (template {r['template_sha256'][:12]})")
        return 0
    if not args.files:
        parser.error(f"{args.command} needs at least one file name")
    known = set(tracked_files())
    code = 0
    for filename in args.files:
        if filename not in known:
            print(f"{filename}: not a product-owned context file; covered: {', '.join(sorted(known))}")
            code = 2
            continue
        if args.command == "show":
            print(view(context_dir, filename, args.templates_dir))
            continue
        ok, message = (apply if args.command == "apply" else resolve)(
            context_dir, filename, args.templates_dir)
        print(message)
        if not ok:
            code = 2
    return code


if __name__ == "__main__":
    sys.exit(main())
