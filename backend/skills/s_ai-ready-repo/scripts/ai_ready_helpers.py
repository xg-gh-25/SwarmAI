"""AI-Ready-Repo Engine — Helper Script for Deterministic Operations.

Handles operations where LLM would hallucinate or be unreliable:
- Git history parsing (commit hashes, dates, file changes)
- File tree building (accurate filesystem state)
- Tech stack detection (from config files)
- code-intel.json v2 schema validation
- AGENTS.md template rendering

All functions are pure/stateless. No LLM calls. No network.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Input Validation ───

def _validate_repo_path(repo_path: Path) -> Path:
    """Validate repo path: must exist, be a directory, and be git-tracked — EITHER
    a repo root (has its own .git) OR a subdirectory inside a git work-tree (a
    monorepo package member, whose .git lives at the repo root — run_a9fe5ad3).

    A monorepo member has no .git of its own but git ls-files / log scoped to it
    work against the parent repo, so the analysis functions are fully functional.
    The old .git-must-exist check wrongly rejected every detected monorepo member.

    Resolves symlinks to prevent traversal attacks.
    Raises ValueError if validation fails.
    """
    repo_path = Path(repo_path).resolve()

    if not repo_path.exists():
        raise ValueError(f"Path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise ValueError(f"Path is not a directory: {repo_path}")
    if (repo_path / ".git").exists():
        return repo_path
    # Not a repo root — accept iff inside a git work-tree (monorepo member).
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            return repo_path
    except (subprocess.TimeoutExpired, OSError):
        pass  # fall through to the strict rejection below
    raise ValueError(f"Not a git repository (no .git): {repo_path}")


def _safe_file_read(file_path: Path, repo_root: Path, max_size: int = 10 * 1024 * 1024) -> str | None:
    """Read a file safely: resolve symlinks, enforce containment within repo_root.

    Returns file content or None if unsafe/unreadable.
    """
    resolved = file_path.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        # Path traversal attempt — file resolves outside repo
        logger.warning(f"Path traversal blocked: {file_path} resolves to {resolved}")
        return None

    if not resolved.is_file():
        return None

    try:
        if resolved.stat().st_size > max_size:
            logger.warning(f"File too large ({resolved.stat().st_size} bytes), skipping: {resolved}")
            return None
        return resolved.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as e:
        logger.warning(f"Cannot read {resolved}: {e}")
        return None


# ─── code-intel.json v2 Schema Validation ───

# Schema aligned to the REAL producer, core/code_intel/json_exporter.py (the
# ground-truth v2 emitter that runs on every reindex). run_5647c72c fixed a
# validator↔exporter divergence: the validator had been written against a
# hand-built FIXTURE schema (module.path/responsibility, top-level `edges`,
# entry_point.path) that the exporter never emitted — so the real SwarmAI
# code-intel.json failed its own validator (43 errors) and v3 generation could
# not run on real data (O009: validator never tested against real output).
# The exporter emits: top-level `dependencies` (NOT `edges`); modules as
# {name, symbol_count, function_count, class_count, file_count, files}; and
# entry_points as {name, file_path, type}.
_REQUIRED_TOP_LEVEL = {"$schema", "version", "repo", "modules", "entry_points"}
_REQUIRED_REPO = {"name", "languages", "total_symbols", "total_edges"}
# The exporter's _build_modules (json_exporter.py:121-132) emits ALL of these
# UNCONDITIONALLY (no branches) — so the exact producer contract is all 6, not a
# loose {name, symbol_count} floor (Gate-2 LOW, run_5647c72c: don't under-specify
# a schema the sole producer always fully populates).
_REQUIRED_MODULE = {"name", "symbol_count", "function_count", "class_count",
                    "file_count", "files"}
_OPTIONAL_TOP_LEVEL = {"routes", "hot_zones", "risk_areas", "dead_code",
                       "dependencies", "edges", "generated_at"}


def validate_code_intel_json(doc: dict, repo_root=None) -> list[str]:
    """Validate a code-intel.json document against v2 schema.

    Returns list of error strings. Empty list = valid.
    Does NOT use jsonschema library — pure Python for zero-dep operation.

    ``repo_root`` (optional): threaded to check_mermaid_node_anchoring so a mermaid
    node naming a real-on-disk-but-unindexed file is accepted (run_3026ef31).
    """
    errors: list[str] = []

    # Top-level required fields
    for field in _REQUIRED_TOP_LEVEL:
        if field not in doc:
            errors.append(f"Missing required top-level field: '{field}'")

    # Version check — v2.0 and v3.0 both accepted (v3 = v2 + domain layer, Run 1)
    _version = doc.get("version")
    if _version and _version not in ("2.0", "3.0"):
        errors.append(f"Invalid version: expected '2.0' or '3.0', got '{_version}'")

    # Repo structure
    repo = doc.get("repo")
    if isinstance(repo, dict):
        for field in _REQUIRED_REPO:
            if field not in repo:
                errors.append(f"Missing required repo field: '{field}'")
    elif "repo" in doc:
        errors.append("'repo' must be a dict")

    # Type checks for list fields (must be lists, not strings/dicts/None)
    for field in ("modules", "edges", "entry_points", "routes", "hot_zones", "risk_areas", "dead_code"):
        if field in doc and not isinstance(doc[field], list):
            errors.append(f"'{field}' must be a list, got {type(doc[field]).__name__}")

    # Modules validation
    modules = doc.get("modules")
    if isinstance(modules, list):
        for i, mod in enumerate(modules):
            if not isinstance(mod, dict):
                errors.append(f"modules[{i}] must be a dict")
                continue
            for field in _REQUIRED_MODULE:
                if field not in mod:
                    errors.append(f"modules[{i}] missing required field: '{field}'")

    # Edges validation (basic structure check)
    edges = doc.get("edges")
    if isinstance(edges, list):
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f"edges[{i}] must be a dict")
            elif "from" not in edge or "to" not in edge:
                errors.append(f"edges[{i}] must have 'from' and 'to' fields")

    # Edge count consistency: repo.total_edges vs actual edges[]
    if isinstance(repo, dict) and isinstance(edges, list):
        claimed = repo.get("total_edges", 0)
        actual = len(edges)
        if claimed > 0 and actual > 0 and claimed > actual * 10:
            errors.append(
                f"Edge count inconsistency: repo.total_edges={claimed} but edges[] has {actual} entries. "
                f"Either include more edges or set total_edges to match the delivered count."
            )

    # Entry points validation. The real exporter (_build_entry_points) emits
    # {name, file_path, type}; older/agent-authored docs may use {path, …}.
    # Accept EITHER a `file_path` or a `path` locator (run_5647c72c: requiring
    # only `path` rejected every real exporter output).
    entry_points = doc.get("entry_points")
    if isinstance(entry_points, list):
        for i, ep in enumerate(entry_points):
            if not isinstance(ep, dict):
                errors.append(f"entry_points[{i}] must be a dict")
            elif "file_path" not in ep and "path" not in ep:
                errors.append(f"entry_points[{i}] must have a 'file_path' (or 'path') field")

    # ─── v3 domain-layer validation (Run 1) ───
    # v2 docs (no version 3.0, no non-empty domains/flows/steps) skip this
    # entirely → backward-compatible. Fires when the doc declares v3 OR carries
    # actual domain-layer content. All THREE v3 checks run together — structural
    # AND the two anti-hallucination guards (referential integrity + LLM-assertion
    # anchoring). Wiring the guards in here is load-bearing: they are the entire
    # anti-spurious value (§1.5); if the main validator doesn't call them, a
    # hallucinated/dangling assertion sails through (Gate-2 CRITICAL, run_aad6d4f2).
    _has_v3_content = any(
        isinstance(doc.get(k), list) and doc.get(k) for k in ("domains", "flows", "steps")
    )
    if _version == "3.0" or _has_v3_content:
        errors.extend(_validate_v3_domain_layer(doc))
        errors.extend(check_domain_referential_integrity(doc))
        errors.extend(check_llm_assertion_guards(doc))
        errors.extend(check_mermaid_node_anchoring(doc, repo_root=repo_root))
        errors.extend(check_business_rule_anchor_files(doc, repo_root=repo_root))
        errors.extend(check_anchor_accounting(doc))
        errors.extend(validate_coverage_ledger(doc))

    return errors


def _validate_v3_domain_layer(doc: dict) -> list[str]:
    """Structural checks for the v3 domain layer: domains[]/flows[]/steps[].

    Only STRUCTURE + required-field presence here. Referential integrity
    (dangling refs) and LLM-assertion guards are separate pure functions
    (check_domain_referential_integrity / check_llm_assertion_guards) so each
    can be called + tested independently.
    """
    errors: list[str] = []
    for key in ("domains", "flows", "steps"):
        if key in doc and not isinstance(doc[key], list):
            errors.append(f"'{key}' must be a list, got {type(doc[key]).__name__}")

    for i, d in enumerate(doc.get("domains", []) if isinstance(doc.get("domains"), list) else []):
        if not isinstance(d, dict):
            errors.append(f"domains[{i}] must be a dict"); continue
        for f in ("id", "name"):
            if f not in d:
                errors.append(f"domains[{i}] missing required field: '{f}'")

    for i, fl in enumerate(doc.get("flows", []) if isinstance(doc.get("flows"), list) else []):
        if not isinstance(fl, dict):
            errors.append(f"flows[{i}] must be a dict"); continue
        for f in ("id", "domain_id"):
            if f not in fl:
                errors.append(f"flows[{i}] missing required field: '{f}'")

    for i, st in enumerate(doc.get("steps", []) if isinstance(doc.get("steps"), list) else []):
        if not isinstance(st, dict):
            errors.append(f"steps[{i}] must be a dict"); continue
        for f in ("id", "flow_id"):
            if f not in st:
                errors.append(f"steps[{i}] missing required field: '{f}'")

    return errors


def check_domain_referential_integrity(doc: dict) -> list[str]:
    """Every domain-layer reference must resolve to a real node (anti-dangling).

    - flow.entry_ref → an id in routes[] (§1.1 anti-hallucination anchor)
    - flow.domain_id → an id in domains[]
    - step.flow_id   → an id in flows[]
    - domain.cross_domain[].target → an id in domains[]
    Pure function (no IO) → unit-testable + mutation-verifiable.
    """
    errors: list[str] = []
    # Drop None/blank ids from the resolvable sets — a ref must match a REAL id,
    # never a `None` a route without an id contributed (Gate-2 hole: None∈{None}).
    route_ids = {r["id"] for r in doc.get("routes", []) if isinstance(r, dict) and _nonblank(r.get("id"))}
    domain_ids = {d["id"] for d in doc.get("domains", []) if isinstance(d, dict) and _nonblank(d.get("id"))}
    flow_ids = {f["id"] for f in doc.get("flows", []) if isinstance(f, dict) and _nonblank(f.get("id"))}

    def _ref_error(node_id, field, ref, kind, resolvable) -> None:
        # Present-but-blank ref is an error (a flow must belong to a real domain);
        # absent ref (None/missing) is allowed (optional).
        if ref is None:
            return
        if not _nonblank(ref) or ref not in resolvable:
            errors.append(f"{field} '{ref}' on '{node_id}' does not resolve to any {kind}")

    for d in doc.get("domains", []):
        if not isinstance(d, dict):
            continue
        for xd in d.get("cross_domain", []) or []:
            if isinstance(xd, dict) and "target" in xd:
                _ref_error(d.get("id"), "cross_domain.target", xd.get("target"), "domain", domain_ids)

    for fl in doc.get("flows", []):
        if not isinstance(fl, dict):
            continue
        if "entry_ref" in fl:
            _ref_error(fl.get("id"), "flow.entry_ref", fl.get("entry_ref"), "route.id", route_ids)
        if "domain_id" in fl:
            _ref_error(fl.get("id"), "flow.domain_id", fl.get("domain_id"), "domain", domain_ids)

    for st in doc.get("steps", []):
        if not isinstance(st, dict):
            continue
        if "flow_id" in st:
            _ref_error(st.get("id"), "step.flow_id", st.get("flow_id"), "flow", flow_ids)

    return errors


# LLM-assertion fields carrying rule/precondition/exception claims (§1.5).
# step-level `contract`/`io` also carry assertions → covered via _iter_assertion_lists.
_LLM_ASSERTION_KEYS = ("business_rules", "preconditions", "rules", "exceptions")


def _nonblank(v) -> bool:
    """True iff v is a non-blank string (strips whitespace-only)."""
    return isinstance(v, str) and bool(v.strip())


def _iter_assertion_lists(node: dict):
    """Yield (key, list) for every assertion-bearing list on a domain-layer node,
    including nested `contract`/`io` sub-objects (§1.5 step-level coverage)."""
    for akey in _LLM_ASSERTION_KEYS:
        val = node.get(akey)
        if isinstance(val, list):
            yield akey, val
    # step.contract / step.io may themselves hold assertion lists
    for sub in ("contract", "io"):
        subobj = node.get(sub)
        if isinstance(subobj, dict):
            for akey in _LLM_ASSERTION_KEYS:
                val = subobj.get(akey)
                if isinstance(val, list):
                    yield f"{sub}.{akey}", val


def check_llm_assertion_guards(doc: dict) -> list[str]:
    """§1.5 anti-spurious / anti-false-negative guard on LLM-generated assertions.

    ⚠️ SCOPE OF THE GUARANTEE (Run C honesty): this guard enforces that a claim is
    ANCHORED, NOT that its prose is TRUE. `verified:true` here means "the LLM asserted
    this rule AND supplied a non-blank `anchor` string" — it does NOT mean the anchored
    code was read and confirmed to match the prose (that would need an LLM prose-judge,
    the deliberate anti-scope). A `verified:true` rule with a real anchor pointing at
    code that contradicts it still passes this guard. Consumers must treat verified:true
    as "LLM-asserted + anchor-present, human-unadjudicated" (see _fmt_assertion_row,
    which renders it as '[llm-claim] ... (anchor: ...)', never as bare fact).

    Each assertion object anywhere in the domain layer:
    - MUST be a dict carrying an explicit boolean `verified` — a plain-string rule
      or a dict with no `verified` is an UN-adjudicated claim, flagged (else an LLM
      dodges the guard by omitting `verified` — Gate-2 HIGH, run_aad6d4f2).
    - `verified` MUST be a real bool (not "true"/"false"/1 — the `is True` identity
      check silently mis-branched string values, Gate-2 CRITICAL).
    - verified:true  → non-blank `anchor` (code file:line PRESENT — not resolved/read);
      else spurious (paper 0.67).
    - verified:false → non-blank `absence_evidence` (grep=0 proof); §1.5#4: a
      "rule doesn't exist" negative is unreliable unless proven absent (the exact
      false-negative that bit Run 0's fixed-column grep).
    Pure function → unit-testable + mutation-verifiable. (Deliberately NOT extended to
    read anchored files: a line-resolvability check is theater — anchors are
    signature-first by design (INSTRUCTIONS.md: "signature is the stable anchor, not
    line number"), so line-range checks false-reject intended drift, and "line exists"
    says nothing about prose truth — Run C M3-skeptic verdict.)
    """
    errors: list[str] = []

    def _check_assertion(a, where: str) -> None:
        if not isinstance(a, dict):
            errors.append(f"{where}: assertion must be a dict with a boolean 'verified' "
                          f"(plain-string/unadjudicated claim, §1.5)")
            return
        v = a.get("verified")
        if not isinstance(v, bool):
            errors.append(f"{where}: 'verified' must be a bool, got {type(v).__name__} "
                          f"(unadjudicated or type-confused claim, §1.5)")
            return
        if v is True:
            if not _nonblank(a.get("anchor")):
                errors.append(f"{where}: verified:true assertion has no anchor (spurious risk, §1.5)")
        else:
            if not _nonblank(a.get("absence_evidence")):
                errors.append(f"{where}: verified:false assertion has no absence_evidence "
                              f"(§1.5#4 anti-false-negative)")

    for scope_key in ("domains", "flows", "steps"):
        for node in doc.get(scope_key, []) or []:
            if not isinstance(node, dict):
                continue
            nid = node.get("id", "?")
            for akey, lst in _iter_assertion_lists(node):
                for a in lst:
                    _check_assertion(a, f"{nid}.{akey}")

    return errors


# A "code-like" mermaid token = one that names a source artifact (carries a
# source-file extension or a path separator). Prose labels (User, Server, "sends
# message") have neither → never treated as an anchor claim (anti-false-positive).
_CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]*(?:\.py|\.ts|\.tsx|\.js|\.rs)\b")


def _collect_doc_file_anchors(doc: dict) -> set[str]:
    """Every real source-file path the doc knows about — the resolvable set a
    mermaid node may reference. Union of modules[].files, entry_points[].file_path,
    routes[].file_path, and steps[].file_path. Stored both as full path and
    basename so a diagram may name either `backend/routers/chat.py` or `chat.py`."""
    anchors: set[str] = set()

    def _add(p) -> None:
        if _nonblank(p):
            anchors.add(p)
            anchors.add(p.rsplit("/", 1)[-1])  # basename too

    for mod in doc.get("modules", []) or []:
        if isinstance(mod, dict):
            for f in mod.get("files", []) or []:
                _add(f)
    for ep in doc.get("entry_points", []) or []:
        if isinstance(ep, dict):
            _add(ep.get("file_path") or ep.get("path"))
    for r in doc.get("routes", []) or []:
        if isinstance(r, dict):
            _add(r.get("file_path"))
    for st in doc.get("steps", []) or []:
        if isinstance(st, dict):
            _add(st.get("file_path"))
    return anchors


def check_mermaid_node_anchoring(doc: dict, repo_root=None) -> list[str]:
    """Gate-1 must-fix (run_3026ef31): the diagram.mermaid field has NO other
    validator, so a hallucinated node label ("backend/ghost_service.py") ships
    silently. This closes the hole fail-closed like the §1.5 guards.

    For every ``diagram.mermaid`` on any domain/flow, extract every CODE-LIKE
    token (one carrying a source-file extension — a path/filename), and assert it
    resolves to real code. A token is accepted iff it is EITHER (a) a file in the
    doc's own anchor set (_collect_doc_file_anchors) OR (b) — when ``repo_root`` is
    provided — a file that actually EXISTS on disk under repo_root. A token that
    resolves to neither is a hallucinated node → error.

    Why (b): the anti-hallucination goal is "the node maps to REAL code", and a
    file that exists on disk IS real code. The v2 code-intel graph indexes only a
    SUBSET of the repo (run_3026ef31: session_healing.py / json_exporter.py exist
    on disk but aren't in the graph) — without the disk check, the gate would
    false-reject a truthful node just because the graph is incomplete. repo_root is
    NOT an escape hatch: a token absent from BOTH the doc AND disk still fails, AND
    the disk check enforces containment (an absolute/`../`-traversal token that
    resolves OUTSIDE repo_root is rejected, not accepted — Gate-2 F1).

    Prose participant labels (no source-file extension) are ignored, so honest
    sequenceDiagram actor names never false-positive.

    Pure w.r.t. the doc; the optional disk check is the only IO (repo_root=None →
    pure, backward-compatible + unit-testable + mutation-verifiable).
    """
    errors: list[str] = []
    anchors = _collect_doc_file_anchors(doc)
    _root = Path(repo_root) if repo_root is not None else None

    _root_resolved = _root.resolve() if _root is not None else None

    def _resolves(tok: str, base: str) -> bool:
        if tok in anchors or base in anchors:
            return True
        # Disk fallback: the file genuinely exists WITHIN the repo (graph
        # incomplete). Gate-2 F1 (HIGH): containment is load-bearing — an
        # absolute token (`/tmp/x.py`) makes `_root / tok` DISCARD _root
        # (pathlib absolute-rhs rule), and `../` traversal escapes upward, so
        # is_file() alone would accept ANY real file on the machine, defeating
        # the anti-hallucination gate. Resolve + assert the candidate stays
        # under repo_root (same discipline as _safe_file_read).
        if _root_resolved is not None:
            try:
                cand = (_root_resolved / tok).resolve()
                cand.relative_to(_root_resolved)  # ValueError if outside repo
                if cand.is_file():
                    return True
            except (OSError, ValueError):
                pass
        return False

    def _check_diagram(node_kind: str, node_id: str, node: dict) -> None:
        diagram = node.get("diagram")
        if not isinstance(diagram, dict):
            return
        mermaid = diagram.get("mermaid")
        if not _nonblank(mermaid):
            return
        for tok in _CODE_TOKEN_RE.findall(mermaid):
            base = tok.rsplit("/", 1)[-1]
            if not _resolves(tok, base):
                errors.append(
                    f"{node_kind} '{node_id}' mermaid references '{tok}' which is not "
                    f"a real file in code-intel.json or on disk (hallucinated node, Gate-1 anti-hallucination)")

    for d in doc.get("domains", []) or []:
        if isinstance(d, dict):
            _check_diagram("domain", d.get("id", "?"), d)
    for fl in doc.get("flows", []) or []:
        if isinstance(fl, dict):
            _check_diagram("flow", fl.get("id", "?"), fl)
    return errors


def check_business_rule_anchor_files(doc: dict, repo_root=None) -> list[str]:
    """run_9a9e314c DoD5 — the non-theater fabrication backstop for verified:true
    business_rules (+ preconditions/rules). check_llm_assertion_guards only asserts
    the `anchor` string is NON-BLANK; a fabricated anchor to a NON-EXISTENT FILE
    (e.g. `backend/core/ghost.py:42`) sails through CLEAN. This guard checks the
    anchor's FILE part is real.

    SCOPE (deliberate, mirrors the mermaid-resolver's honesty):
      * FILE-EXISTS, NOT line-resolve. Line-resolution is theater (signature-first
        anchoring — line drift false-rejects intended drift; Run-C M3 verdict). We
        catch WHOLESALE FABRICATION (the anchored file doesn't exist), not prose truth.
      * A file resolves if it's in the doc's known anchors (modules/routes/entry/steps)
        OR exists on disk UNDER repo_root. repo_root=None → pure/doc-only (no disk IO),
        and CANNOT prove a doc-absent file fabricated → not flagged (backward-compat).
      * Containment is load-bearing (same Gate-2 F1 lesson as the mermaid guard): an
        absolute anchor (`/tmp/x.py`) or `../` traversal that escapes repo_root is
        REJECTED even if the target exists — else is_file() accepts any file on the
        machine, defeating the anti-hallucination purpose.

    Pure w.r.t. the doc; the optional disk check is the only IO (repo_root=None → pure,
    unit-testable + mutation-verifiable).
    """
    errors: list[str] = []
    anchors = _collect_doc_file_anchors(doc)
    _root_resolved = Path(repo_root).resolve() if repo_root is not None else None

    def _file_of(anchor: str) -> str:
        # anchor is "file:line" or "file:start-end" or bare "file". Strip a trailing
        # ":<digits>" or ":<digits>-<digits>" line-spec; keep the file path (which may
        # itself contain no colon on posix). rsplit once from the right on ':'.
        if ":" in anchor:
            head, tail = anchor.rsplit(":", 1)
            # only treat tail as a line-spec if it's a line reference — digits with
            # optional range/list separators (`216`, `216-232`, `216,232`, `L216`).
            # Else the colon was part of the path (rare) — keep whole.
            probe = tail.lstrip("Ll")
            if probe and all(c.isdigit() or c in "-," for c in probe):
                return head
        return anchor

    def _resolves(f: str) -> bool:
        base = f.rsplit("/", 1)[-1]
        if f in anchors or base in anchors:
            return True
        if _root_resolved is not None:
            try:
                cand = (_root_resolved / f).resolve()
                cand.relative_to(_root_resolved)  # ValueError if outside repo
                if cand.is_file():
                    return True
            except (OSError, ValueError):
                pass
        return False

    def _check_node(node: dict, nid: str) -> None:
        for _akey, lst in _iter_assertion_lists(node):
            for a in lst:
                if not isinstance(a, dict) or a.get("verified") is not True:
                    continue
                anchor = a.get("anchor")
                if not _nonblank(anchor):
                    continue  # non-blank check is check_llm_assertion_guards' job
                f = _file_of(anchor.strip())
                # only judge anchors that NAME a code file (carry an extension or path
                # sep) — a bare symbol-name anchor isn't a file claim (anti-false-flag).
                if not _CODE_TOKEN_RE.search(f) and "/" not in f:
                    continue
                # in pure mode (no repo_root) we can't prove a doc-absent file fake,
                # so only flag when we have disk OR the file is genuinely un-resolvable
                # against the doc AND we have a root to check against.
                if _root_resolved is None:
                    if f not in anchors and f.rsplit("/", 1)[-1] not in anchors:
                        continue  # can't prove absence without disk
                if not _resolves(f):
                    errors.append(
                        f"{nid}.{_akey}: verified:true anchor '{anchor}' names file '{f}' "
                        f"which is not in code-intel.json or on disk under repo_root "
                        f"(fabricated/hallucinated anchor, DoD5 anti-fabrication)")

    for scope_key in ("domains", "flows", "steps"):
        for node in doc.get(scope_key, []) or []:
            if isinstance(node, dict):
                _check_node(node, node.get("id", "?"))
    return errors


# ── Run 1 (run_94e5a5aa): anchor-accounting = the COVERAGE-GUARANTEE mechanism ──
#
# The crux this closes: v3 generation was anti-hallucination-hard (a flow.entry_ref
# must resolve) but coverage-BLIND — the LLM could classify 10 of 208 anchors and
# the system silently accepted 4.8% coverage while reporting "valid". On a bank
# legacy codebase that is a fatal delivery: "done" while only 4.8% is understood.
#
# The fix is NOT a route-% threshold (Gate-0 reframe: a % gate rewards padding
# trivial routes to hit a number — P6 metric-gaming — and `routes` is the wrong
# denominator for a batch/stored-proc/message-handler system). It is an ACCOUNTING
# invariant: EVERY anchor must be ACCOUNTED — either classified (a flow entry_ref)
# OR explicitly parked in `unclassified: [{id, reason}]` with a SUBSTANTIVE reason.
# Silent omission is a fail-closed error; honest "no business flow, because X" is
# allowed. This forbids the silent hole WITHOUT forcing fake flows.

# A junk "reason" that must NOT rubber-stamp an omission (Gate-1 F5 — same family as
# the mermaid absolute-path escape: a self-authored gate leaving its own hole). A
# real reason explains WHY an anchor has no business flow; "." / "n/a" / "todo" do not.
_JUNK_REASONS = {".", "-", "--", "n/a", "na", "none", "x", "?", "tbd", "todo", "fixme"}
_MIN_REASON_LEN = 12  # a substantive reason is a short phrase, not a placeholder


def _is_substantive_reason(reason) -> bool:
    """A reason genuinely accounts for an un-classified anchor iff it is a real
    EXPLANATION, not a placeholder or a length-padding stamp.

    Gate-2 F1 (HIGH): `len>=12` alone was gameable — "xxxxxxxxxxxx" / 12 dots passed,
    which just moved the reason="." rubber-stamp down one level (the CLASS-A
    "self-authored gate leaves its own hole" pattern). So substance = ALL of:
    - non-blank, not in the junk-token set, and len>=_MIN_REASON_LEN, AND
    - a real PHRASE: >=2 whitespace-separated words (a single long token isn't an
      explanation), AND
    - low-information rejection: >=5 distinct characters (bars "xxxxxxxxxxxx",
      "............", "____________" — a single repeated char is not an explanation).
    """
    if not _nonblank(reason):
        return False
    r = reason.strip().lower()
    if r in _JUNK_REASONS or len(r) < _MIN_REASON_LEN:
        return False
    if len(r.split()) < 2:            # must be a phrase, not one padded token
        return False
    if len(set(r.replace(" ", ""))) < 5:  # too few distinct chars = filler
        return False
    return True


# ── Run AB Cycle 2: the UNIFIED coverage ledger (Gate-1 Check-5) ──
#
# "One ledger, not two." Two hole SOURCES exist at different granularities:
#   - route-level: doc['unclassified'] = [{id, reason}] — a route anchor with no
#     business flow (the id MUST be a real route anchor; enforced by
#     check_anchor_accounting). This is the WORKING, back-compat route bucket.
#   - file/repo-level: doc['coverage_ledger'] = [{ref, kind, reason}] — a file the
#     deterministic PARSER could not turn into nodes (unknown extension, unreadable,
#     parse failure) or a repo-level fact (empty/oversized). Produced upstream by
#     parser.parse_repo_with_coverage (Cycle 1).
# They cannot be physically ONE array because check_anchor_accounting requires an
# unclassified `id` to be a real ROUTE anchor — a file path would be rejected as
# fabricated. So unification is by CONTRACT, not by cramming: ONE {ref, kind, reason}
# entry shape, ONE _is_substantive_reason gate for the `reason` of every hole
# regardless of source, and ONE reader iter_coverage_ledger(doc) that yields the
# complete hole set in that shape. That is the honest "single ledger" a consumer sees.

_COVERAGE_HOLE_KINDS = {"route", "file", "repo", "query", "gitignored"}


def validate_coverage_ledger(doc: dict) -> list[str]:
    """Fail-closed validator for the file/repo-level coverage_ledger (Run AB).

    Mirrors check_anchor_accounting's discipline for the parser-produced holes:
    - each entry is a dict carrying {ref, kind, reason}
    - `kind` is present and one of _COVERAGE_HOLE_KINDS (no silent unknown kind)
    - `reason` is SUBSTANTIVE (same _is_substantive_reason gate as unclassified —
      a hole cannot be rubber-stamped with junk/"n/a"/blank)
    - a route-kind entry's `ref` MUST be a real route anchor (mirrors the
      unclassified anti-fabrication check :596 — a file path masquerading as a
      route hole is rejected)

    An absent/empty coverage_ledger is valid (a fully-covered repo has no file holes).
    """
    errors: list[str] = []
    ledger = doc.get("coverage_ledger")
    if ledger is None:
        return errors
    if not isinstance(ledger, list):
        return ["coverage_ledger must be a list of {ref, kind, reason} entries"]

    # route-kind refs are validated against the real anchor menu (best-effort: if the
    # doc has no backfilled ids, extract raises — that is check_anchor_accounting's
    # job to report, so here we just skip the anchor cross-check rather than double-raise).
    try:
        route_ids = _route_anchor_ids(doc)
    except ValueError:
        route_ids = set()

    for i, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            errors.append(f"coverage_ledger[{i}] must be a dict {{ref, kind, reason}}")
            continue
        ref = entry.get("ref")
        kind = entry.get("kind")
        reason = entry.get("reason")
        if not _nonblank(ref):
            errors.append(f"coverage_ledger[{i}] missing 'ref'")
        if kind not in _COVERAGE_HOLE_KINDS:
            errors.append(f"coverage_ledger[{i}] has invalid 'kind'={kind!r} "
                          f"(must be one of {sorted(_COVERAGE_HOLE_KINDS)})")
        if not _is_substantive_reason(reason):
            errors.append(f"coverage_ledger[{i}] (ref={ref!r}) has no substantive "
                          f"'reason' — a hole cannot be rubber-stamped with a "
                          f"blank/junk reason (same gate as unclassified)")
        if kind == "route" and _nonblank(ref) and route_ids and ref not in route_ids:
            errors.append(f"coverage_ledger[{i}] route-kind ref {ref!r} is not a real "
                          f"route anchor (fabricated — a file path cannot be a route hole)")
    return errors


def iter_coverage_ledger(doc: dict):
    """Yield the COMPLETE hole set in the unified {ref, kind, reason} shape (Run AB).

    This is the single honest "what is NOT understood" reader that unifies the two
    hole sources a consumer would otherwise have to know about separately:
      - route holes from doc['unclassified'] ({id, reason} → {ref:id, kind:'route', reason})
      - file/repo/query holes from doc['coverage_ledger'] (already {ref, kind, reason})

    Only well-formed, substantively-reasoned holes are yielded (a junk-reason entry is
    a validation error surfaced by validate_coverage_ledger / check_anchor_accounting,
    not something to silently re-emit here). Order: route holes first, then ledger.
    """
    for u in (doc.get("unclassified") or []):
        if isinstance(u, dict) and _nonblank(u.get("id")) and _is_substantive_reason(u.get("reason")):
            yield {"ref": u["id"], "kind": "route", "reason": u["reason"]}
    for h in (doc.get("coverage_ledger") or []):
        if (isinstance(h, dict) and _nonblank(h.get("ref"))
                and h.get("kind") in _COVERAGE_HOLE_KINDS
                and _is_substantive_reason(h.get("reason"))):
            yield {"ref": h["ref"], "kind": h["kind"], "reason": h["reason"]}


def _route_anchor_ids(doc: dict) -> set[str]:
    """The accounting denominator = the id set of ROUTE-kind anchors (Gate-1 2a:
    extract_entry_anchors also yields entry_point-kind anchors, but referential
    integrity only resolves flow.entry_ref against routes[], so an entry_point
    anchor can never be *classified* — scoping the denominator to route-kind keeps
    classified/unclassified drawn from the same set. When a real non-HTTP system
    (jobs/handlers) arrives, extend BOTH the denominator and the classify path
    together, not one alone)."""
    anchors = extract_entry_anchors(doc)  # may raise ValueError (routes present, no ids)
    return {a["id"] for a in anchors if a.get("kind") == "route" and _nonblank(a.get("id"))}


def compute_anchor_accounting(doc: dict) -> dict:
    """Pure: how much of the codebase's (route-kind) anchor menu is ACCOUNTED for.

    Returns:
      total             — count of route-kind anchors (the denominator)
      classified        — anchors referenced by a flow.entry_ref
      unclassified_count— anchors parked in doc['unclassified'] with a substantive reason
      missing_ids       — anchors that are NEITHER (the silent-omission set; sorted)
      accounted_ratio   — (classified + unclassified) / total  ← the GATED invariant (must be 1.0)
      classified_ratio  — classified / total                   ← honest quality signal, NEVER gated
                          (gating it would reward padding flows — P6)

    If routes are present but un-backfilled (no ids), returns total=0 with an
    `unbackfilled=True` flag rather than raising — this is a REPORT (fail-closed
    enforcement lives in check_anchor_accounting, which errors on that case).
    """
    try:
        all_ids = _route_anchor_ids(doc)
    except ValueError:
        return {"total": 0, "classified": 0, "unclassified_count": 0,
                "missing_ids": [], "accounted_ratio": 0.0, "classified_ratio": 0.0,
                "unbackfilled": True}
    total = len(all_ids)
    classified = {f.get("entry_ref") for f in (doc.get("flows") or [])
                  if isinstance(f, dict) and _nonblank(f.get("entry_ref"))} & all_ids
    unclassified = {u["id"] for u in (doc.get("unclassified") or [])
                    if isinstance(u, dict) and u.get("id") in all_ids
                    and _is_substantive_reason(u.get("reason"))}
    accounted = classified | unclassified
    missing = all_ids - accounted
    return {
        "total": total,
        "classified": len(classified),
        "unclassified_count": len(unclassified),
        "missing_ids": sorted(missing),
        "accounted_ratio": round(len(accounted) / total, 4) if total else 1.0,
        "classified_ratio": round(len(classified) / total, 4) if total else 0.0,
    }


def check_anchor_accounting(doc: dict) -> list[str]:
    """Fail-closed COVERAGE guard: every route-kind anchor must be accounted for.

    Errors (any → validate_code_intel_json fails → finalize_v3 raises):
    - a missing anchor (neither in a flow nor in a reasoned unclassified bucket) —
      the silent-omission defect this whole mechanism exists to kill.
    - an `unclassified` entry whose id is not a real anchor (fabricated — mirrors the
      mermaid fake-node guard) OR whose reason is blank/junk (Gate-1 F5 rubber-stamp).
    - an anchor listed in BOTH a flow AND unclassified (Gate-1 2b: double-accounting
      masks a real omission).
    - routes present but NONE carry ids (Gate-2 F2: extract_entry_anchors raises
      loud — the guard must NOT swallow that into a clean pass, else a whole-codebase
      omission ships silently because the id-backfill was skipped).
    """
    errors: list[str] = []
    try:
        all_ids = _route_anchor_ids(doc)
    except ValueError as e:
        # routes present but un-backfilled (no ids) — surface, do NOT pass vacuously.
        return [f"anchor-accounting cannot run: {e} — run backfill_route_ids(doc) "
                f"first so every route carries an id (Gate-2 F2 anti-vacuous-pass)"]

    # Gate-2 F1 (run AB adversarial, CRITICAL): a route present WITHOUT an id is
    # silently excluded from all_ids by extract_entry_anchors — so a moved/renamed
    # route that lost its id-reattach match would VANISH from the accounting
    # denominator and accounted_ratio would read 1.0 over a set that no longer
    # contains it (a real route invisible = the banking false-100% red line). The
    # ALL-id-less case raises above; this catches the PARTIAL case (some ids present,
    # some routes id-less) which does NOT raise. Every route MUST carry an id.
    idless = [f"{r.get('method')} {r.get('path')} ({r.get('file_path')})"
              for r in (doc.get("routes") or [])
              if isinstance(r, dict) and not _nonblank(r.get("id"))]
    for loc in idless:
        errors.append(f"route {loc} has NO id — it is silently excluded from the "
                      f"coverage denominator (a moved/renamed route that lost its "
                      f"anchor id). Run backfill_route_ids(doc) so every route is "
                      f"accounted (Gate-2 F1: id-less route = invisible coverage hole).")

    if not all_ids:
        return errors  # genuinely no route entries (v2 doc) — not this guard's job

    flow_refs = {f.get("entry_ref") for f in (doc.get("flows") or [])
                 if isinstance(f, dict) and _nonblank(f.get("entry_ref"))} & all_ids

    unclassified_ids: set[str] = set()
    for u in (doc.get("unclassified") or []):
        if not isinstance(u, dict):
            errors.append("unclassified entry must be a dict {id, reason}")
            continue
        uid = u.get("id")
        if uid not in all_ids:
            errors.append(f"unclassified id '{uid}' is not a real anchor in the menu "
                          f"(fabricated — must be a route id, §1.1 anti-hallucination)")
            continue
        if not _is_substantive_reason(u.get("reason")):
            errors.append(f"unclassified anchor '{uid}' has no substantive reason "
                          f"(blank/junk reason cannot rubber-stamp an omission — Gate-1 F5)")
            continue
        if uid in flow_refs:
            errors.append(f"anchor '{uid}' is BOTH classified (a flow) AND unclassified "
                          f"— double-accounting masks a real omission (Gate-1 2b)")
            continue
        unclassified_ids.add(uid)

    missing = all_ids - flow_refs - unclassified_ids
    for mid in sorted(missing):
        errors.append(f"anchor '{mid}' is UNACCOUNTED — not in any flow.entry_ref and "
                      f"not in unclassified[] with a reason (silent-omission = coverage hole; "
                      f"classify it into a flow OR park it in unclassified:[{{id,reason}}])")
    return errors


def compute_subsystem_coverage(doc: dict, seed: list[dict]) -> dict:
    """§12.2 BREADTH anchor: how much of the repo's load-bearing SUBSYSTEM menu has
    a domain — the completeness axis that `compute_anchor_accounting` (route axis)
    does NOT measure. The route axis answers "is each HTTP route in a flow"; this
    answers "does each load-bearing subsystem have a domain+spec at all".

    Why a SEED list, not graph-clustering (design §12.1, probed live): backend/core
    is 116 flat .py files — dir-anchor (22 top-level dirs, core=149 files), filename-
    prefix (62% singletons), and import connected-components (1×97-file blob + 49
    singletons) ALL fail to partition it. The seed is the existing, maintained
    architecture map (KNOWLEDGE.md "Codebase Navigation" + TECH.md "Key Subsystems"):
    evidence that pre-dates this run, each entry pointing at real files. This is the
    Spec-Studio "enumerate the org's packages" move, adapted — our flat core's
    "packages" live in the arch doc, not the directory tree.

    Args:
      doc:  a code-intel.json (reads domains[] + flows[] + steps[] + routes[]).
      seed: [{"name": str, "tier": "spine"|"extension"|"out-of-scope",
              "globs": [fnmatch patterns over repo-relative file paths]}].
            tier="out-of-scope" = a support layer we deliberately DON'T spec
            (utils/middleware/schemas) — recorded honestly, never silently dropped
            (§12.2 honest-lossy, Spec-Studio pattern).

    A subsystem is COVERED iff any file matching its globs appears as evidence of a
    domain — i.e. in a domain's flow's step.file_path, OR a domain's flow's
    entry_ref route.file_path. (Domains carry no file list directly; their flows'
    steps/routes ARE the file evidence — verified against real code-intel.json.)

    Returns a report (pure, no I/O, no mutation):
      total / covered / gaps / out_of_scope — counts
      subsystems: [{name, tier, status, domain_id?, evidence_files[]}]
                  status ∈ covered | gap | out-of-scope
      gap_queue:  sorted names of spine/extension subsystems with NO domain
                  (the non-silent gap list — pkgs-gaps-status style, §12.2 AC2)
    """
    import fnmatch as _fnmatch

    domains = doc.get("domains") or []
    flows = doc.get("flows") or []
    steps = doc.get("steps") or []
    routes = {r.get("id"): r for r in (doc.get("routes") or []) if isinstance(r, dict)}

    # Build: file_path -> domain_id, from each domain's flows' evidence (steps + entry route).
    file_to_domain: dict[str, str] = {}
    flows_by_domain: dict[str, list] = {}
    for fl in flows:
        if isinstance(fl, dict):
            flows_by_domain.setdefault(fl.get("domain_id"), []).append(fl)
    for dom in domains:
        if not isinstance(dom, dict):
            continue
        did = dom.get("id")
        for fl in flows_by_domain.get(did, []):
            fid = fl.get("id")
            # entry route file
            r = routes.get(fl.get("entry_ref"))
            if r and _nonblank(r.get("file_path")):
                file_to_domain.setdefault(r["file_path"], did)
            # step files
            for st in steps:
                if isinstance(st, dict) and st.get("flow_id") == fid \
                        and _nonblank(st.get("file_path")):
                    file_to_domain.setdefault(st["file_path"], did)

    subsystems = []
    for s in (seed or []):
        if not isinstance(s, dict) or not s.get("name"):
            continue
        globs = s.get("globs") or []
        tier = s.get("tier") or "extension"
        # which domain (if any) has evidence in a file matching this subsystem's globs
        matched_domain = None
        evidence = []
        for fp, did in file_to_domain.items():
            if any(_fnmatch.fnmatch(fp, g) for g in globs):
                matched_domain = matched_domain or did
                evidence.append(fp)
        if tier == "out-of-scope":
            status = "out-of-scope"          # honest-lossy: recorded, not specced
        elif matched_domain:
            status = "covered"
        else:
            status = "gap"                   # load-bearing but no domain — non-silent
        entry = {"name": s["name"], "tier": tier, "status": status}
        if matched_domain:
            entry["domain_id"] = matched_domain
            entry["evidence_files"] = sorted(set(evidence))
        subsystems.append(entry)

    covered = sum(1 for s in subsystems if s["status"] == "covered")
    gaps = sum(1 for s in subsystems if s["status"] == "gap")
    oos = sum(1 for s in subsystems if s["status"] == "out-of-scope")
    gap_queue = sorted(s["name"] for s in subsystems if s["status"] == "gap")
    return {
        "total": len(subsystems),
        "covered": covered,
        "gaps": gaps,
        "out_of_scope": oos,
        "subsystems": subsystems,
        "gap_queue": gap_queue,
    }


def blind_spot_scan(doc: dict) -> dict:
    """AC4 — Spec Studio-style REVERSE coverage check (code→doc direction).

    For each risky code span the AST layer already knows about (``risk_areas`` +
    ``hot_zones`` — deterministic facts, NOT an LLM negative assertion), ask: does the
    domain layer document the file that owns it? A file is "documented" iff it is
    touched by a ``steps[].file_path`` OR named by a ``business_rules[].anchor``. If
    neither → it's a blind spot (code has a risky behavior the spec never mentions).

    Design constraints (§11.2 / §12.4, both load-bearing):
      * REPORT-ONLY — this returns a report; it is NEVER a fail-closed gate. The
        fail-closed ``behavior_coverage`` gate was DEFERRED as C042 (gating an LLM
        negative assertion over sparse step data). Callers must not BLOCK on it.
      * DETERMINISTIC — keys off ``risk_areas``/``hot_zones`` (real fan-in/risk facts),
        so it never asserts "X does not exist" from an LLM (the r6 unreliability lesson).
      * HONEST / non-silent — every risky span is either ``documented`` or listed in
        ``blind_spots`` with its ``reason`` preserved; nothing is dropped.

    Returns: ``{total_risky, documented, blind, clean, blind_spots:[{name,file_path,
    reason,risk_score}]}``. ``clean`` is True iff zero blind spots (a valid, honest
    outcome — zero findings is not failure).
    """
    # 1. Build the set of files the domain layer documents (steps + rule anchors).
    documented_files: set[str] = set()
    for st in doc.get("steps") or []:
        fp = st.get("file_path")
        if fp:
            documented_files.add(fp)
    for dm in doc.get("domains") or []:
        for br in dm.get("business_rules") or []:
            anchor = br.get("anchor")
            if anchor:
                documented_files.add(anchor)

    # 2. Collect risky spans (dedup by (name, file_path); risk_areas carry the reason,
    #    hot_zones contribute high-fan-in files not already flagged).
    risky: dict[tuple, dict] = {}
    for ra in doc.get("risk_areas") or []:
        fp = ra.get("file_path")
        if not fp:
            continue
        key = (ra.get("name"), fp)
        risky[key] = {
            "name": ra.get("name"),
            "file_path": fp,
            "reason": ra.get("reason") or "",
            "risk_score": ra.get("risk_score"),
        }
    for hz in doc.get("hot_zones") or []:
        fp = hz.get("file_path")
        if not fp:
            continue
        key = (hz.get("name"), fp)
        if key not in risky:
            risky[key] = {
                "name": hz.get("name"),
                "file_path": fp,
                "reason": f"High fan-in: {hz.get('callers')} callers",
                "risk_score": None,
            }

    # 3. Split documented vs blind (a span is documented iff its file is documented).
    blind_spots = [
        span for span in risky.values()
        if span["file_path"] not in documented_files
    ]
    total = len(risky)
    blind = len(blind_spots)
    return {
        "total_risky": total,
        "documented": total - blind,
        "blind": blind,
        "clean": blind == 0,
        "blind_spots": sorted(blind_spots, key=lambda b: b["file_path"]),
    }


def derive_route_id(method: str, path: str, file_path: str) -> str:
    """§1.4 collision-resistant route id = route:{slug}-{hash(method+path+file)}.

    - slug is a readable label; the hash carries uniqueness (Gate-2 fix,
      run_aad6d4f2): the OLD form slugged `method+path` (collapsing `/a/b`,
      `/a-b`, `/users` vs `/users/` to one slug) and hashed only file_path
      (16-bit → ~40% collision at 200+ routes). Now the hash is over the EXACT
      `method|path|file_path` triple at 32 bits, so distinct routes get distinct
      ids even when their slugs collapse or the same file defines several.
    - NO line number → survives code drift (rejected {file}:{line} broke on edits).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", f"{method} {path}".lower()).strip("-")
    key = f"{method}|{path}|{file_path}"
    h = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]  # 32 bits, non-crypto id
    return f"route:{slug}-{h}"


# ─── Incremental merge (Run 2, run_36266b66) ───

def merge_code_intel(baseline: dict, new_nodes: list, new_edges: list) -> dict:
    """Merge freshly-analyzed nodes/edges into a baseline GRAPH (§2, UA keep-last).

    ⚠️ OPERATES ON A NODE/EDGE GRAPH, NOT ON THE EXPORTED code-intel.json.
    (run_5647c72c) This is the UA batch-graph merge — it reads/writes
    ``baseline["nodes"]`` + ``baseline["edges"]``. The PRODUCED code-intel.json
    (core/code_intel/json_exporter.py) has NO top-level `nodes`/`edges` — it uses
    `modules`/`routes`/`dependencies`. Passing an exported code-intel.json here is
    SAFE for existing keys (baseline is deep-copied, so modules/routes survive
    untouched) but the merge is a NO-OP on them — it only reconciles the graph
    `nodes`/`edges` layer. Do NOT use this to incrementally merge exported
    code-intel.json module/route data; that layer is regenerated by the exporter,
    not merged here. (0 production callers today — this guard prevents future misuse.)

    Incremental model (UA "old graph = batch -1"): the BASELINE is processed
    FIRST, then new batches — so a node re-analyzed this round OVERWRITES its
    baseline copy (keep-last by id). Unchanged baseline nodes survive untouched.

    - Nodes: dedup by `id`, keep-last (baseline first → new wins). No id → kept
      positionally (can't dedup an id-less node; rare, structural error caught
      elsewhere).
    - Edges: dedup by the full key (from, to, type, direction) — `direction` is
      part of the key so a `forward` edge never silently overwrites a
      `bidirectional` one (Run 0 lesson). Then DROP any edge whose `from`/`to`
      endpoint is not in the merged node-id set (dangling → drop).

    Pure function (deep-copies retained objects — never mutates/aliases the
    caller's baseline) → unit-testable + mutation-verifiable + idempotent
    (merge(merge(x)) == merge(x)) for ALL inputs, including id-less nodes.

    id-less nodes: deduped by a structural content-key (not blind positional
    append) so re-feeding the same id-less node is idempotent. An edge whose
    endpoint is an id-less node is NOT dropped (id-less nodes join the
    reachability set via a synthetic key) — keeping node+edge consistent.
    Edge dedup prefers the FIELD-RICHER edge on a key collision (never loses
    weight/note to a stripped re-emit).
    """
    import copy as _copy

    def _struct_key(n: dict) -> str:
        return json.dumps(n, sort_keys=True, ensure_ascii=False, default=str)

    nodes_by_id: dict = {}
    idless_by_struct: dict = {}  # dedup id-less nodes structurally (idempotent)
    for node in list(baseline.get("nodes", [])) + list(new_nodes or []):
        nd = _copy.deepcopy(node) if isinstance(node, dict) else node
        if isinstance(nd, dict) and nd.get("id") is not None:
            nodes_by_id[nd["id"]] = nd  # keep-last: later (new) wins
        elif isinstance(nd, dict):
            idless_by_struct[_struct_key(nd)] = nd
        # non-dict nodes are structural garbage → dropped (can't be referenced)
    merged_nodes = list(nodes_by_id.values()) + list(idless_by_struct.values())
    # Reachability set for dangling-edge detection: real ids + id-less struct keys.
    node_ids = set(nodes_by_id.keys())
    idless_keys = set(idless_by_struct.keys())

    def _endpoint_present(ep) -> bool:
        # An endpoint resolves if it's a known id OR the struct-key of an id-less node.
        return ep in node_ids or ep in idless_keys

    def _richness(e: dict) -> int:
        return len(e)  # more fields = richer; prefer it on collision

    edges_by_key: dict = {}
    for edge in list(baseline.get("edges", [])) + list(new_edges or []):
        if not isinstance(edge, dict):
            continue
        e = _copy.deepcopy(edge)
        key = (e.get("from"), e.get("to"), e.get("type"), e.get("direction"))
        prev = edges_by_key.get(key)
        # keep-last, but never let a stripped re-emit clobber a richer edge
        if prev is None or _richness(e) >= _richness(prev):
            edges_by_key[key] = e
    merged_edges = [
        e for e in edges_by_key.values()
        if _endpoint_present(e.get("from")) and _endpoint_present(e.get("to"))
    ]

    out = _copy.deepcopy(baseline)  # no aliasing of caller's nested objects
    out["nodes"] = merged_nodes
    out["edges"] = merged_edges
    return out


def reconcile_human_blocks(
    old_blocks: list, new_domain_blocks: list
) -> tuple[list, list]:
    """§8.8 [human] re-key contract: human-authored spec blocks survive a domain
    rename (id change) as long as their CONTENT is unchanged, else quarantine.

    A [human] block is anchored by a stable `hash` (content-hash), NOT by
    `domain_id` — so when incremental merge renames/splits a domain (new id,
    same content), the block re-attaches to the new domain that carries the same
    hash. A block whose hash matches NO new domain is moved to `orphaned` (for
    manual re-attach) — NEVER dropped (human business rules are assets).

    Args:
        old_blocks: [{domain_id, content, hash}, ...] — extracted from prior .spec.md
        new_domain_blocks: [{domain_id, hash}, ...] — the post-merge domains
    Returns:
        (kept, orphaned) — kept blocks carry the NEW domain_id; every input block
        appears in exactly one of the two lists (conservation: none vanish).
    """
    # Build hash → domain_id, tracking AMBIGUITY: if two new domains share a
    # content-hash, we cannot know which one a human block belongs to → that
    # hash is ambiguous and matching blocks are quarantined (not silently bound
    # to a last-wins arbitrary domain). Gate-2 finding, run_36266b66.
    hash_counts: dict = {}
    hash_to_new_domain: dict = {}
    for nd in new_domain_blocks or []:
        if isinstance(nd, dict) and nd.get("hash") is not None:
            h = nd["hash"]
            hash_counts[h] = hash_counts.get(h, 0) + 1
            hash_to_new_domain[h] = nd.get("domain_id")
    ambiguous = {h for h, c in hash_counts.items() if c > 1}

    kept: list = []
    orphaned: list = []
    for blk in old_blocks or []:
        if not isinstance(blk, dict):
            orphaned.append(blk)
            continue
        h = blk.get("hash")
        if h is not None and h in hash_to_new_domain and h not in ambiguous:
            rekeyed = dict(blk)
            rekeyed["domain_id"] = hash_to_new_domain[h]  # re-attach to new id
            kept.append(rekeyed)
        else:
            orphaned.append(blk)  # never dropped (unmatched OR ambiguous)
    return kept, orphaned


# ─── Run 1.5 (run_1417a3a1): domain-layer GENERATION scaffold ───
# The deterministic half of code-intel v3 domain generation (§1.1/§1.4/§1.5):
# backfill join keys → project the anti-hallucination anchor menu → assemble +
# fail-closed validate. LLM classification (routes → business domains) stays
# agent-driven in INSTRUCTIONS.md; these functions are the guardrails around it.

def backfill_route_ids(doc: dict) -> dict:
    """Add a stable §1.4 `id` to every routes[]/entry_points[] entry lacking one.

    The join key flows anchor to (flow.entry_ref → route.id). v2 routes have no
    id; this backfills `derive_route_id(method, path, file_path)`. Pure: returns a
    deep-copied doc, never mutates the caller's.

    - IDEMPOTENT: an entry that already has a non-blank `id` is preserved
      untouched (so re-running after a partial generation is safe).
    - COLLISION-DETECTED (§1.4): if two entries derive/carry the SAME id, raises
      ValueError — a silent keep-last would drop a real route from the anchor menu.
    - entry_points may lack method/path (a CLI/cron entry) → id derives from
      whatever of {method,path,file_path} exist; a fully-empty entry is skipped
      (can't anchor a flow to nothing) rather than given a garbage id.
    """
    import copy as _copy
    out = _copy.deepcopy(doc)
    seen: dict[str, str] = {}  # id → "routes[i]"/"entry_points[i]" for collision msg

    def _ensure(entries, label):
        if not isinstance(entries, list):
            return
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            existing = e.get("id")
            if _nonblank(existing):
                rid = existing
                origin = "carried"  # author-supplied id
            else:
                method = str(e.get("method") or "")
                path = str(e.get("path") or "")
                fpath = str(e.get("file_path") or "")
                if not (method or path or fpath):
                    continue  # nothing to anchor on — skip, don't fabricate an id
                rid = derive_route_id(method, path, fpath)
                e["id"] = rid
                origin = "derived"  # from method|path|file_path triple
            where = f"{label}[{i}]"
            if rid in seen:
                prev_where, prev_origin = seen[rid]
                # Distinguish the two collision classes (Gate-2 MED): a duplicate
                # method|path|file triple (both derived) vs a carried id clashing
                # with a derived/other id, vs a rare 32-bit sha1 birthday collision.
                if prev_origin == "derived" and origin == "derived":
                    cause = ("the method|path|file_path triple is duplicated (likely a "
                             "real handler + a mock/test registration), OR a rare 32-bit "
                             "hash collision between two distinct triples")
                else:
                    cause = (f"a {origin} id clashes with a {prev_origin} id — an "
                             f"author-supplied 'id' duplicates another entry's id")
                raise ValueError(
                    f"route id collision: '{rid}' on both {prev_where} ({prev_origin}) "
                    f"and {where} ({origin}) — §1.4 requires unique anchor ids. Cause: {cause}."
                )
            seen[rid] = (where, origin)

    _ensure(out.get("routes"), "routes")
    _ensure(out.get("entry_points"), "entry_points")
    return out


def extract_entry_anchors(doc: dict) -> list[dict]:
    """Project routes[]+entry_points[] into the compact ANCHOR MENU that
    constrains LLM flow creation (§1.1/§1.5 anti-hallucination).

    Returns [{id, method, path, file_path, line_number, kind}] — the ONLY set of
    ids a generated flow.entry_ref is allowed to reference. The LLM classifies
    real anchors into business flows; it cannot invent an entry point, because a
    flow whose entry_ref is not in this menu fails check_domain_referential_integrity.

    Requires ids to be present (run backfill_route_ids first) — an entry without a
    non-blank id is skipped (it can't be an anchor).

    LOUD-on-empty (Gate-2 MED): if the doc HAS routes/entry_points but NONE carry an
    id, the caller almost certainly forgot backfill_route_ids — a silent [] would
    give the LLM an empty menu (nothing to anchor to) and only surface as a
    downstream finalize_v3 rejection, far from the cause. Raise instead.
    """
    anchors: list[dict] = []
    total_entries = 0
    for kind, key in (("route", "routes"), ("entry_point", "entry_points")):
        entries = doc.get(key)
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            total_entries += 1
            if not _nonblank(e.get("id")):
                continue
            anchors.append({
                "id": e["id"],
                "method": e.get("method"),
                "path": e.get("path"),
                "file_path": e.get("file_path"),
                "line_number": e.get("line_number"),
                "kind": kind,
            })
    if total_entries and not anchors:
        raise ValueError(
            f"extract_entry_anchors: {total_entries} route/entry_point(s) present but "
            f"NONE carry an id — run backfill_route_ids(doc) first (§1.4). An empty "
            f"anchor menu would leave the LLM nothing to anchor flows to."
        )
    return anchors


def finalize_v3(doc: dict, domains: list, flows: list, steps: list, repo_root=None) -> dict:
    """Assemble a generated domain layer into a v3 doc — FAIL-CLOSED gate.

    Attaches domains/flows/steps, bumps version to '3.0', then runs the existing
    Run-1 validators (structural + referential integrity + LLM-assertion guards).
    Raises ValueError with ALL errors if any guard fails — a generation that
    produces a dangling entry_ref or an unanchored 'verified:true' claim (§1.5
    spurious) is REJECTED, never persisted. Pure: deep-copies, never mutates input.

    ``repo_root`` (optional): when given, the mermaid-node-anchoring guard also
    accepts a node naming a file that EXISTS on disk under repo_root (the v2 graph
    indexes only a subset of the repo — a truthful node must not be rejected merely
    because the graph is incomplete; run_3026ef31). NOT an escape hatch: a node
    absent from both the doc and disk still fails.

    The caller (agent workflow) is expected to have run backfill_route_ids first so
    flow.entry_ref values resolve; finalize_v3 is the last line of defense that
    proves the assembled doc is internally consistent before it becomes truth.
    """
    import copy as _copy
    # Type-guard the layer args (Gate-2 HIGH): a non-list (dict/int) would either
    # silently mangle (list(dict)→keys) or raise a bare TypeError instead of the
    # documented ValueError. Fail-closed with a clear message. None → empty list.
    for _name, _val in (("domains", domains), ("flows", flows), ("steps", steps)):
        if _val is not None and not isinstance(_val, list):
            raise ValueError(
                f"finalize_v3: '{_name}' must be a list or None, got "
                f"{type(_val).__name__} (fail-closed §1.5)"
            )
    out = _copy.deepcopy(doc)
    # deep-copy the layer args too (not just list()): the spec_hash stamp below
    # mutates each domain dict, and the docstring promises "never mutates input" —
    # a shallow list() would share the caller's dict objects and inject spec_hash
    # into them (Gate-2 HIGH, run_97a6b1db). deepcopy keeps finalize_v3 pure.
    out["domains"] = _copy.deepcopy(list(domains or []))
    out["flows"] = _copy.deepcopy(list(flows or []))
    out["steps"] = _copy.deepcopy(list(steps or []))
    out["version"] = "3.0"
    # ── stamp each domain's spec_hash at ASSEMBLY (run_97a6b1db) ──
    # finalize_v3 is the sanctioned AGENT domain-authoring chokepoint (domain +
    # flows + steps all in hand). Historically only the core json_exporter (reindex
    # path) stamped spec_hash, so a doc authored HERE shipped staleness-BLIND —
    # freshness.detect_spec_details_staleness treats an unstamped domain as
    # unjudgeable and silently exempts it. Stamp here too, using THIS module's
    # single-source _spec_content_hash (no cross-boundary import — json_exporter
    # reaches in for the same fn), so authoring-path and reindex-path agree and the
    # stamp matches the marker a regenerate_spec_preserving_human .spec.md carries.
    # Fail-open per-domain: one bad domain must never sink the assembly.
    _o_flows = out["flows"]
    _o_steps = out["steps"]
    for _dom in out["domains"]:
        if isinstance(_dom, dict):
            try:
                _dom["spec_hash"] = _spec_content_hash(_dom, _o_flows, _o_steps)
            except Exception:  # noqa: BLE001 — fail-open (unstamped = unjudgeable, never wrong)
                pass
    errors = validate_code_intel_json(out, repo_root=repo_root)
    if errors:
        raise ValueError(
            "finalize_v3 rejected the generated domain layer (fail-closed §1.5):\n  - "
            + "\n  - ".join(errors)
        )
    return out


# ─── Run 3 (run_6602eeab): spec-details eval dims + deterministic skeleton ───

def _iter_domain_assertions(domain: dict, flows: list, steps: list):
    """Yield every LLM-assertion dict (business_rules/issues/gaps + step
    rules/preconditions/exceptions) belonging to `domain`. Shared by the eval
    scorers so completeness/precision/explicit count the SAME element set."""
    for a in domain.get("business_rules") or []:
        yield a
    for a in domain.get("issues") or []:
        yield a
    for a in domain.get("gaps") or []:
        yield a
    did = domain.get("id")
    dom_flows = [f for f in flows if f.get("domain_id") == did]
    for fl in dom_flows:
        fid = fl.get("id")
        for st in steps:
            if st.get("flow_id") != fid:
                continue
            for k in ("rules", "preconditions", "exceptions"):
                for a in st.get(k) or []:
                    yield a


def eval_spec_details(doc: dict) -> dict:
    """§9 eval dims for a code-intel v3 domain layer — the quantitative quality
    gate (Siala & Lano 2025), NOT "looks right".

    - flow_validity (was misnamed 'completeness' — Run 1 fix): fraction of flows that
      resolve to a real route entry_ref (an unanchored flow = a missing/hallucinated
      element). This is a per-flow VALIDITY measure — NOT codebase coverage. The old
      name lied: a doc with 10 valid flows covering 10 of 208 routes scored
      "completeness"=1.0 while 95% of the codebase was unclassified. Real coverage is
      the two accounting ratios below.
    - accounted_ratio: (classified + reasoned-unclassified) / total anchors — the
      fail-closed coverage invariant (must be 1.0; see check_anchor_accounting).
    - classified_ratio: classified / total anchors — the HONEST quality signal (how
      much is a real business flow, not just parked as unclassified). Never gated.
    - precision (consistency): fraction of assertions that are ANCHORED-AND-ASSERTED
      (a real bool True + non-blank anchor). ⚠️ Run C honesty: "verified" here means
      LLM-asserted + anchor-string-present, NOT prose-confirmed-against-code — this
      metric measures how many claims carry a checkable pointer, not how many are
      true. FP here = spurious (paper: LLM 0.67) — an un-anchored / verified:false
      assertion counts against precision.
    - explicit: fraction of steps with explicit==True (paper: can-forward-engineer).
    - f1 = 2·recall·precision/(recall+precision).

    Pure: counts over the doc structure, no I/O. Returns 0.0 for an empty axis
    (no divide-by-zero) and records the denominators so a caller can tell
    "1.0 because perfect" from "1.0 because vacuous (n=0)".
    """
    domains = doc.get("domains") or []
    flows = doc.get("flows") or []
    steps = doc.get("steps") or []
    route_ids = {r.get("id") for r in (doc.get("routes") or []) if r.get("id")}

    # flow_validity: flows anchored to a real route (per-flow validity, NOT coverage)
    anchored = sum(1 for f in flows
                   if f.get("entry_type") != "http" or f.get("entry_ref") in route_ids)
    n_flows = len(flows)
    flow_validity = anchored / n_flows if n_flows else 0.0
    # real coverage (the metric the old 'completeness' hid)
    _acc = compute_anchor_accounting(doc)

    # precision + explicit over all assertions / steps
    total_assert = 0
    verified_assert = 0
    for dom in domains:
        for a in _iter_domain_assertions(dom, flows, steps):
            total_assert += 1
            if isinstance(a, dict) and a.get("verified") is True \
                    and str(a.get("anchor") or "").strip():
                verified_assert += 1
    precision = verified_assert / total_assert if total_assert else 0.0

    n_steps = len(steps)
    explicit_steps = sum(1 for s in steps if s.get("explicit") is True)
    explicit = explicit_steps / n_steps if n_steps else 0.0

    # f1 pairs flow_validity with precision (unchanged semantics; renamed input)
    f1 = (2 * flow_validity * precision / (flow_validity + precision)
          if (flow_validity + precision) else 0.0)

    return {
        "flow_validity": round(flow_validity, 4),
        "accounted_ratio": _acc["accounted_ratio"],
        "classified_ratio": _acc["classified_ratio"],
        "precision": round(precision, 4),
        "explicit": round(explicit, 4),
        "f1": round(f1, 4),
        "denominators": {"flows": n_flows, "assertions": total_assert, "steps": n_steps,
                         "anchors": _acc["total"]},
    }


def _md_cell(v) -> str:
    """Escape a value for a markdown TABLE cell: a literal `|` would create a
    phantom column and corrupt the 2-col table; a newline would split the row.
    (Gate-2 MED, run_235ffe64 — real step.io.output '{status:created} | 400'
    carries a pipe.)"""
    return str(v).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _fmt_assertion_row(a) -> str:
    """One assertion (rule/precondition/exception) as inline text, HONESTLY labeled
    so no assertion is ever read as a machine-verified fact (Run C, semantic-boundary
    honesty). Two LLM-sourced states, both explicitly marked:

    - verified:true  → '[llm-claim] text (anchor: `file:line`)'. This means the LLM
      ASSERTED the rule AND supplied a code POINTER a human/grep can check — it does
      NOT mean the prose was verified against the code (the guard only enforces the
      anchor STRING is present, never reads the line). The old render 'text (`anchor`)'
      LIED: it read as an established fact with a citation, so a banking consumer
      mistook an LLM self-assertion for machine-confirmed truth.
    - verified:false / bare string → '[llm-inferred] text' (unadjudicated, §1.5).

    Neither ever renders as plain fact. NOT pipe-escaped here — the caller escapes the
    joined cell."""
    if isinstance(a, dict):
        txt = a.get("rule") or a.get("cond") or a.get("case") or ""
        if a.get("verified") is True:
            anc = str(a.get("anchor") or "").strip()
            return f"[llm-claim] {txt}" + (f" (anchor: `{anc}`)" if anc else "")
        return f"[llm-inferred] {txt}"
    return f"[llm-inferred] {a}"


def _render_step_spec_table(st: dict) -> list[str]:
    """Render the §3.2 rich step spec table (输入/输出/接口契约/前置/规则/异常)
    from step.io + step.contract + step.preconditions/rules/exceptions. Emits ONLY
    the rows that have data (a step with just a name/loc renders no table). Pure.
    Every cell value is pipe/newline-escaped (_md_cell) so table-hostile content
    (e.g. an output '{...} | 400') can't corrupt the markdown table."""
    rows: list[str] = []
    io = st.get("io") or {}
    if io.get("input"):
        rows.append(f"| 输入 | {_md_cell(io['input'])} |")
    if io.get("output"):
        rows.append(f"| 输出 | {_md_cell(io['output'])} |")
    c = st.get("contract") or {}
    if c:
        sig = c.get("signature", "")
        http = c.get("http", "")
        codes = c.get("status_codes") or {}
        codes_str = "; ".join(f"{k}={v}" for k, v in codes.items()) if isinstance(codes, dict) else ""
        contract_bits = " · ".join(x for x in (f"`{sig}`" if sig else "", http, codes_str) if x)
        if contract_bits:
            rows.append(f"| 接口契约 | {_md_cell(contract_bits)} |")
    for label, key in (("前置条件", "preconditions"), ("业务规则", "rules"), ("异常路径", "exceptions")):
        items = st.get(key)
        if isinstance(items, list) and items:
            joined = "; ".join(_fmt_assertion_row(a) for a in items)
            rows.append(f"| {label} | {_md_cell(joined)} |")
    if not rows:
        return []
    return ["| 项 | 内容 |", "|---|---|"] + rows + [""]


# ─── code-intel v3 loop-liveness: spec-details content-hash (SINGLE SOURCE) ───
#
# The staleness of a `.spec.md` vs its domain is decided by a CONTENT HASH, NOT the
# file mtime (mtime false-fires: a reindex rewrites code-intel.json — mtime bumps —
# while PRESERVING identical domains[], so every spec would read "stale" forever).
#
# This is the ONE place the hash is DEFINED (Gate-1 F1b — no two-writer drift). The
# json_exporter (core) imports THIS to stamp each domain's `spec_hash`; freshness.py
# only READS that stamp + the marker below. The hash MUST cover the domain AND its
# flows/steps, because project_domain_skeleton renders §3/§4 from them — a flow/step
# change alters the rendered spec, so a domain-dict-only hash would false-fresh
# (Gate-1 F1). We hash the rendered skeleton itself (with the marker line elided) so
# "what changed the rendered spec" and "what bumps the hash" are the SAME set by
# construction — impossible to drift out of coverage.
SPEC_HASH_MARKER_RE = re.compile(r"<!--\s*spec-hash:\s*([0-9a-f]{64})\s*-->")
_SPEC_HASH_MARKER_LINE_RE = re.compile(r"^\s*<!--\s*spec-hash:.*-->\s*$", re.MULTILINE)


def extract_spec_hash_marker(spec_text: str) -> str | None:
    """Return the sha256 hex embedded in a `.spec.md`'s ``<!-- spec-hash: X -->``
    marker, or None if absent (→ treated as stale by the detector)."""
    m = SPEC_HASH_MARKER_RE.search(spec_text or "")
    return m.group(1) if m else None


def _spec_content_hash(domain: dict, flows: list, steps: list) -> str:
    """sha256 hex of the domain's RENDERED skeleton (marker line elided). Covers
    domain + its flows + steps by construction (renders the same thing the spec
    shows). THE single definition of spec staleness — see the block comment above.
    """
    skeleton = _render_domain_skeleton_body(domain, flows, steps)
    canonical = _SPEC_HASH_MARKER_LINE_RE.sub("", skeleton)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def project_domain_skeleton(domain: dict, flows: list, steps: list) -> str:
    """Deterministically project ONE domain (+ its flows/steps) into the 8-section
    `.spec.md` skeleton (§3.2), with a ``<!-- spec-hash: X -->`` staleness marker
    embedded in the header. Pure string render — NO LLM. The skeleton region
    (§1-4,6-7) is `domains[]`-authoritative; the §5 [human] region is left as a
    stub for human authorship (owned by spec-details, protected on merge §8.2).

    LLM domain EXTRACTION and prose THICKENING are out of scope for Run 3 (the
    dropped Run-1 generation piece) — this renders the machine skeleton from an
    already-populated domains[] entry, which is what exists today.
    """
    body = _render_domain_skeleton_body(domain, flows, steps)
    marker = f"<!-- spec-hash: {_spec_content_hash(domain, flows, steps)} -->"
    # Inject the marker right after the H1 title line (stable, human-invisible).
    lines = body.split("\n")
    lines.insert(1, marker)
    return "\n".join(lines)


def _render_domain_skeleton_body(domain: dict, flows: list, steps: list) -> str:
    """The raw 8-section skeleton render (no spec-hash marker). Split out so
    _spec_content_hash can hash the exact rendered content the human sees."""
    name = domain.get("name") or domain.get("id") or "Unnamed Domain"
    did = domain.get("id")
    L = [f"# 规格:{name}", ""]
    L += ["## 1. 域概述",
          f"职责:{domain.get('summary', '(未填)')}",
          f"核心实体:{', '.join(domain.get('entities') or []) or '(未填)'}",
          f"复杂度:{domain.get('complexity', 'moderate')}", ""]

    diagram = (domain.get("diagram") or {}).get("mermaid")
    L += ["## 2. 架构图(本域)"]
    L += [f"```mermaid\n{diagram}\n```" if diagram else "_(无架构图)_", ""]

    dom_flows = [f for f in flows if f.get("domain_id") == did]
    L += ["## 3. 用户流程图(每条 flow)"]
    for fl in dom_flows:
        fd = (fl.get("diagram") or {}).get("mermaid")
        if fd:
            L.append(f"```mermaid\n{fd}\n```")
    if not any((f.get("diagram") or {}).get("mermaid") for f in dom_flows):
        L.append("_(无流程图)_")
    L.append("")

    L += ["## 4. 业务流 & 步骤规格"]
    for fl in dom_flows:
        L.append(f"### 业务流:{fl.get('name', fl.get('id'))} — 入口 {fl.get('entry_ref', '(未锚定)')}")
        fid = fl.get("id")
        fsteps = sorted((s for s in steps if s.get("flow_id") == fid),
                        key=lambda s: s.get("order", 0))
        for st in fsteps:
            lr = st.get("line_range")
            loc = f"{st.get('file_path', '?')}:{lr[0]}-{lr[1]}" if lr else st.get("file_path", "?")
            L.append(f"#### 步骤 {st.get('order', '?')} — {st.get('name', '?')} (`{loc}`)")
            L.extend(_render_step_spec_table(st))
    L.append("")

    L += ["## 5. 业务规则汇总(域级不变量)",
          "<!-- [human] 区:人工增补业务承诺,merge 时受保护不覆盖(§8.2) -->",
          "_(待人工增补 `[human]` 业务规则)_", ""]

    L += ["## 6. 潜在问题 & 风险", "| 严重度 | 位置 | 问题 | 来源 |", "|---|---|---|---|"]
    for iss in domain.get("issues") or []:
        if isinstance(iss, dict):
            L.append(f"| {iss.get('severity', '?')} | `{iss.get('file', '?')}:{iss.get('line', '?')}` "
                     f"| {iss.get('issue', '')} | {iss.get('source', 'llm')} |")
    L.append("")

    L += ["## 7. Gaps & 改进区", "| 类型 | 位置 | 建议 | 来源 |", "|---|---|---|---|"]
    for g in domain.get("gaps") or []:
        if isinstance(g, dict):
            L.append(f"| {g.get('kind', '?')} | `{g.get('file', '?')}` "
                     f"| {g.get('action', g.get('note', ''))} | {g.get('source', 'llm')} |")
    L.append("")

    xd = domain.get("cross_domain") or []
    ups = ", ".join(x.get("target", "") for x in xd if isinstance(x, dict)) or "无"
    L += ["## 8. 关联", f"上下游域:{ups}",
          "项目级教训:see IMPROVEMENT.md#(升级的问题上浮到此)", ""]
    return "\n".join(L)


# ─── Run 4 (run_b5993cdb, feature D): [human] preservation on regeneration ───

# A [human] block = a markdown LIST ITEM carrying a backtick-fenced `[human]`
# marker (§3.2 / §8.2), PLUS its continuation lines (wrapped text, indented
# sub-bullets, fenced code) up to the next top-level list item or `## ` header.
# Skill-LOCAL (NOT imported from core.recall_multi) so the skill stays portable
# (C046). NOTE — this DELIBERATELY DIVERGES from recall_multi._extract_human_blocks:
# recall does LINE-level BM25 indexing (one bullet line, comments stripped), but
# PRESERVATION needs the VERBATIM block (continuation lines + inline comments kept)
# or a multiline human rule loses its body on regen (Gate-2 CRITICAL, run_b5993cdb).
# Different concern → different extractor; they are not "keep in sync".
_HUMAN_MARKER_RE = re.compile(r"`\[human\]`")
_LIST_BULLET_RE = re.compile(r"^(?:[-*+]\s|\d+\.\s)")
_SECTION_HDR_RE = re.compile(r"^##\s")


def _is_top_level_bullet(raw: str) -> bool:
    """A list bullet at column 0 (no leading indent) — starts a new block."""
    return bool(_LIST_BULLET_RE.match(raw)) and raw[:1] in "-*+0123456789"


def extract_human_spec_blocks(spec_text: str) -> list[str]:
    """Return the human-authored §5 blocks of a .spec.md, VERBATIM (feature D).

    A block = a top-level list item whose text carries a backtick-fenced
    ``[human]`` marker, TOGETHER WITH its continuation lines (wrapped text,
    indented sub-bullets, fenced code) until the next top-level bullet or ``##``
    header. Returned verbatim (original indentation + inline comments preserved) —
    this is a PRESERVATION extractor, not the recall LINE-indexer, so a multiline
    human business rule survives regeneration intact.

    Marker detection ignores HTML comments so a legend/comment mention of the bare
    or quoted marker does NOT start a block; but once a real block starts, its own
    trailing inline ``<!-- … -->`` is kept verbatim (it's human content).
    """
    lines = spec_text.splitlines()
    inline_comment = re.compile(r"<!--.*?-->", re.DOTALL)
    blocks: list[str] = []
    n = len(lines)
    i = 0
    in_html_comment = False
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        # Track HTML-comment regions ONLY to decide whether THIS line can START a
        # block (a marker inside a comment must not trigger). We do not consume
        # comment regions destructively — a block, once started, keeps its bytes.
        scan = stripped
        started_here = False
        if not in_html_comment:
            # residue after removing closed inline comments, for marker detection
            residue = inline_comment.sub("", scan)
            if "<!--" in residue and "-->" not in residue:
                in_html_comment = True  # opens a multiline comment on this line
                residue = residue.split("<!--", 1)[0]
            if (_is_top_level_bullet(raw) and _HUMAN_MARKER_RE.search(residue)):
                started_here = True
        else:
            if "-->" in stripped:
                in_html_comment = False
        if started_here:
            block = [raw]
            i += 1
            # consume continuation lines until next top-level bullet / ## header
            while i < n:
                nxt = lines[i]
                if _is_top_level_bullet(nxt) or _SECTION_HDR_RE.match(nxt.strip()):
                    break
                block.append(nxt)
                i += 1
            # trim trailing blank lines inside the captured block
            while block and not block[-1].strip():
                block.pop()
            blocks.append("\n".join(block))
        else:
            i += 1
    return blocks


def regenerate_spec_preserving_human(existing_spec_md: str, domain: dict,
                                     flows: list, steps: list) -> str:
    """Re-render a domain's `.spec.md` skeleton from fresh domains[] WITHOUT
    destroying human-authored §5 business rules (feature D — the one irreversible
    data-loss risk in the whole v3 governance surface).

    The skeleton region (§1-4,6-8) is `domains[]`-authoritative → freshly rendered.
    The §5 `[human]` list items from the EXISTING file are spliced back in, so a
    regeneration cycle never overwrites a human business commitment (§8.2 ownership
    boundary made concrete). A first generation (existing="") is a plain skeleton.

    Idempotent: regenerating an already-preserved file re-extracts the SAME [human]
    blocks and re-injects them (no duplication — the fresh skeleton's §5 has only
    the stub, which the human blocks REPLACE, not append to).
    """
    fresh = project_domain_skeleton(domain, flows, steps)
    human_blocks = extract_human_spec_blocks(existing_spec_md or "")
    if not human_blocks:
        return fresh  # nothing human to preserve (first gen, or all-machine spec)

    # Replace the §5 stub body with the preserved human rules. §5 spans from its
    # header to the next "## " header (§6). We keep the header + the HTML-comment
    # ownership note, drop the "_(待人工增补…)_" stub line, inject the real blocks.
    lines = fresh.split("\n")
    out: list[str] = []
    i = 0
    injected = False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.startswith("## 5.") and not injected:
            i += 1
            # carry any HTML-comment ownership note lines verbatim; drop the stub
            while i < len(lines) and not lines[i].startswith("## "):
                nxt = lines[i]
                if nxt.strip().startswith("<!--"):
                    out.append(nxt)
                # skip the stub placeholder + blanks; real content comes from human_blocks
                i += 1
            out.append("")
            out.extend(human_blocks)
            out.append("")
            injected = True
            continue
        i += 1
    return "\n".join(out)


# ─── Run 5 (run_3349787d, design §10): behavioral-equivalence layer ───
#
# ⚠️ DESIGN-ONLY / CONSUMER-API for a STATIC analyzer (Run C honesty note): this layer
# scores the spec's behavioral claims against REAL runtime `observations` — but a
# static code-intel tool NEVER executes the target repo, so it cannot itself PRODUCE
# observations. `observations` MUST be supplied by an EXTERNAL caller that has them
# (a CI harness / test-runner / instrumented runtime). Do NOT build an in-tool
# "observations producer" — a static tool can only synthesize them from the same doc
# that made the claims, a closed loop that fake-passes by construction (Run C
# M3-skeptic verdict: C042 over-engineering). Absent observations the layer is
# CORRECTLY inert: score_equivalence returns 'unchecked', never 'verified'. So this
# trio is a consumer API awaiting real observations, not a production code path — it
# is expected to have no non-test caller inside this repo until such a consumer exists.

def derive_equivalence_assertions(doc: dict) -> list[dict]:
    """From each step.contract{http,status_codes} emit checkable assertion records
    (§10.2). Each status_code becomes one behavioral claim the spec makes about the
    code: "this endpoint returns <code> (<meaning>)". These are what an equivalence
    check runs against real tests/runtime. Pure — reads the doc, no IO.

    Returns [{flow_id, step_id, kind:'status_code', http, code, meaning}]. A step
    with no contract / no status_codes contributes nothing (→ its domain will be
    'unchecked' in scoring, never fake-passed).
    """
    out: list[dict] = []
    for st in doc.get("steps", []) or []:
        if not isinstance(st, dict):
            continue
        c = st.get("contract") or {}
        codes = c.get("status_codes") or {}
        if not isinstance(codes, dict):
            continue
        http = c.get("http", "")
        for code, meaning in codes.items():
            out.append({
                "flow_id": st.get("flow_id"),
                "step_id": st.get("id"),
                "kind": "status_code",
                "http": http,
                "code": str(code),
                "meaning": meaning,
            })
    return out


def score_equivalence(doc: dict, observations: dict) -> dict:
    """Score the domain layer's behavioral equivalence against real observations
    (§10.2). `observations` maps (step_id, code) → observed_bool (did a real test /
    runtime actually exhibit this status code?). Missing observation = UNVERIFIED,
    NOT a pass.

    Per-domain equivalence tag:
      - 'verified'  — domain has ≥1 assertion AND every observed assertion passed
                      AND every assertion was observed
      - 'partial'   — some assertions observed+passed, some unobserved or failed
      - 'unchecked' — domain has NO derivable assertion (no contract) OR no
                      observation at all (§10.2 honest fallback — a static/no-test
                      domain is NEVER fake-passed)
    Returns {overall_score, domains:{domain_id: {tag, passed, total, observed}}}.
    Pure.
    """
    assertions = derive_equivalence_assertions(doc)
    # map step_id → domain_id via flow
    flow_domain = {f.get("id"): f.get("domain_id") for f in doc.get("flows", []) or []}
    step_flow = {s.get("id"): s.get("flow_id") for s in doc.get("steps", []) or []}

    def _domain_of(step_id):
        return flow_domain.get(step_flow.get(step_id))

    per_domain: dict = {}
    for a in assertions:
        dom = _domain_of(a["step_id"])
        d = per_domain.setdefault(dom, {"passed": 0, "total": 0, "observed": 0})
        d["total"] += 1
        key = (a["step_id"], a["code"])
        if key in observations:
            d["observed"] += 1
            if observations[key]:
                d["passed"] += 1

    # every domain in the doc gets a tag (domains with no assertion → unchecked)
    all_domains = [x.get("id") for x in doc.get("domains", []) or [] if isinstance(x, dict)]
    result_domains: dict = {}
    total_passed = total_all = 0
    for dom in all_domains:
        d = per_domain.get(dom)
        if not d or d["total"] == 0:
            result_domains[dom] = {"tag": "unchecked", "passed": 0, "total": 0, "observed": 0}
            continue
        total_passed += d["passed"]; total_all += d["total"]
        if d["observed"] == 0:
            tag = "unchecked"          # has assertions but none observed → honest unchecked
        elif d["passed"] == d["total"] and d["observed"] == d["total"]:
            tag = "verified"
        else:
            tag = "partial"
        result_domains[dom] = {"tag": tag, **d}
    # Surface orphan assertions (steps whose flow/domain doesn't resolve to a real
    # domain) instead of silently dropping them (Gate-2 F5, run_3349787d): a
    # contract that vanishes from the report reads as "fully covered" when it isn't.
    # Fold into an explicit __unresolved__ bucket + the score denominator.
    orphan = {"passed": 0, "total": 0, "observed": 0}
    for dom, d in per_domain.items():
        if dom in all_domains:
            continue
        orphan["passed"] += d["passed"]; orphan["total"] += d["total"]; orphan["observed"] += d["observed"]
    if orphan["total"]:
        total_passed += orphan["passed"]; total_all += orphan["total"]
        result_domains["__unresolved__"] = {"tag": "unchecked", **orphan}
    overall = round(total_passed / total_all, 4) if total_all else 0.0
    return {"overall_score": overall, "domains": result_domains}


def equivalence_feedback(doc: dict, observations: dict) -> list[dict]:
    """§10.2 feedback loop + §1.5#3 SME queue: every assertion that was OBSERVED
    and FAILED becomes an SME-review-queue item AND its step is (in the returned
    copy) marked verified:false for that behavior. A failed equivalence claim means
    the spec says one thing and the code does another — a human must adjudicate.

    Returns the review-queue items [{step_id, flow_id, http, code, meaning, reason}].
    (Unobserved assertions are NOT failures — they're 'unchecked', per §10.2, and do
    not enqueue.) Pure — does not mutate doc.
    """
    queue: list[dict] = []
    for a in derive_equivalence_assertions(doc):
        key = (a["step_id"], a["code"])
        if key in observations and not observations[key]:
            queue.append({
                "step_id": a["step_id"], "flow_id": a["flow_id"],
                "http": a["http"], "code": a["code"], "meaning": a["meaning"],
                "reason": f"spec claims {a['http']} → {a['code']} ({a['meaning']}) "
                          f"but the observed behavior did NOT exhibit it — SME must adjudicate",
                "verified": False,
            })
    return queue


# ─── Git History Parsing for Gotchas ───

_FIX_PATTERN = re.compile(
    r"^(fix|hotfix|revert|bugfix)[\s:(/]",
    re.IGNORECASE,
)


def parse_git_gotchas(repo_path: Path) -> list[dict[str, str]]:
    """Extract gotchas from git history using fix/revert/hotfix commits.

    Returns list of dicts with keys: when, risk, because.
    Only returns entries with real commit hash evidence.
    Raises ValueError if repo_path is not a valid git repository.
    """
    repo_path = _validate_repo_path(Path(repo_path))
    gotchas: list[dict[str, str]] = []

    # Get git log with hash, date, subject, and files changed
    try:
        result = subprocess.run(
            [
                "git", "log", "--pretty=format:%H|%ai|%s",
                "--name-only", "--diff-filter=M",
                "-n", "200",  # Last 200 commits max
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Git log timed out for {repo_path}")
        return []

    if result.returncode != 0:
        logger.warning(f"Git log failed for {repo_path}: {result.stderr.strip()}")
        return []

    # Parse log into commit records
    commits = _parse_git_log(result.stdout)

    # Filter to fix/revert/hotfix commits
    fix_commits = [c for c in commits if _FIX_PATTERN.match(c["subject"])]

    # Group by files touched — repeated fixes to same file = gotcha
    file_fixes: dict[str, list[dict]] = {}
    for commit in fix_commits:
        for f in commit.get("files", []):
            file_fixes.setdefault(f, []).append(commit)

    # Generate gotchas for files with 2+ fix commits (repeated pain)
    for filepath, commits_list in file_fixes.items():
        if len(commits_list) >= 2:
            hashes = ", ".join(c["hash"][:7] for c in commits_list[:3])
            subjects = "; ".join(c["subject"] for c in commits_list[:2])
            gotchas.append({
                "when": f"modifying {filepath}",
                "risk": f"Repeated fixes needed — {subjects}",
                "because": f"commits {hashes} ({len(commits_list)} incidents)",
            })

    # Single fix commits that are reverts are always gotchas
    for commit in fix_commits:
        if commit["subject"].lower().startswith("revert"):
            # Extract what was reverted from subject
            subject = commit["subject"]
            files = commit.get("files", ["unknown file"])
            file_str = files[0] if files else "unknown"
            gotchas.append({
                "when": f"modifying {file_str}",
                "risk": f"Change was reverted — {subject}",
                "because": f"commit {commit['hash'][:7]}",
            })

    # Deduplicate by 'when' field
    seen = set()
    unique_gotchas = []
    for g in gotchas:
        if g["when"] not in seen:
            seen.add(g["when"])
            unique_gotchas.append(g)

    return unique_gotchas


def _parse_git_log(log_output: str) -> list[dict]:
    """Parse git log --pretty=format:%H|%ai|%s --name-only output.

    Handles pipes in commit subjects by splitting on first 2 pipes only.
    Supports both SHA-1 (40 char) and future SHA-256 (64 char) hashes.
    """
    commits = []
    current: dict | None = None

    for line in log_output.strip().split("\n"):
        if not line:
            continue

        # Check if this is a header line (hash|date|subject)
        # Split on first 2 pipes only — subject may contain pipes
        if "|" in line:
            parts = line.split("|", 2)
            if len(parts) >= 3:
                hash_candidate = parts[0]
                # Support SHA-1 (40) and SHA-256 (64) hashes
                if (
                    len(hash_candidate) in (40, 64)
                    and all(c in "0123456789abcdef" for c in hash_candidate)
                ):
                    if current:
                        commits.append(current)
                    current = {
                        "hash": hash_candidate,
                        "date": parts[1].strip(),
                        "subject": parts[2].strip(),
                        "files": [],
                    }
                    continue

        # Otherwise it's a filename
        if current and line.strip():
            current["files"].append(line.strip())

    if current:
        commits.append(current)

    return commits


# ─── Repository Info Gathering ───

_LANG_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
}


def _count_langs_by_ext(files) -> "Counter":
    """Count files by language via _LANG_EXTENSIONS. Shared by _detect_tech_stack
    and the multi-package language-mix (Gate-1 B1: single counting source, no
    third copy of the ext map). `files` = iterable of relative path strings."""
    counter: Counter = Counter()
    for f in files:
        if not f:
            continue
        ext = Path(f).suffix.lower()
        if ext in _LANG_EXTENSIONS:
            counter[_LANG_EXTENSIONS[ext]] += 1
    return counter


_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target", ".tox",
    ".eggs", ".mypy_cache", ".pytest_cache",
}
# Glob patterns that need fnmatch (can't use set membership)
_IGNORE_DIR_PATTERNS = ["*.egg-info"]


def _is_ignored_dir(part: str) -> bool:
    """Check if a path component should be ignored (exact match or glob pattern)."""
    import fnmatch
    if part in _IGNORE_DIRS:
        return True
    return any(fnmatch.fnmatch(part, pat) for pat in _IGNORE_DIR_PATTERNS)


def gather_repo_info(repo_path: Path) -> dict[str, Any]:
    """Gather repository metadata for engine input.

    Returns dict with: file_tree, tech_stack, git_stats, readme_content, config_files.
    Works on ANY git repository — no SwarmAI-specific assumptions.
    Raises ValueError if repo_path is not a valid git repository.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    return {
        "file_tree": _build_file_tree(repo_path),
        "tech_stack": _detect_tech_stack(repo_path),
        "git_stats": _get_git_stats(repo_path),
        "readme_content": _read_readme(repo_path),
        "config_files": _find_config_files(repo_path),
    }


def _build_file_tree(repo_path: Path, max_depth: int = 4) -> list[str]:
    """Build a flat file tree listing (relative paths), respecting .gitignore."""
    files = []

    # Use git ls-files if possible (respects .gitignore)
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"git ls-files timed out for {repo_path}, falling back to rglob")
        result = None

    if result and result.returncode == 0 and result.stdout.strip():
        files = [f for f in result.stdout.strip().split("\n") if f]
    else:
        # Fallback: walk filesystem
        for path in repo_path.rglob("*"):
            if path.is_file():
                rel = path.relative_to(repo_path)
                # Skip ignored directories
                if any(_is_ignored_dir(part) for part in rel.parts):
                    continue
                if len(rel.parts) <= max_depth:
                    files.append(str(rel))

    return sorted(files)[:500]  # Cap at 500 files


def _detect_tech_stack(repo_path: Path) -> dict[str, Any]:
    """Detect languages, frameworks, and build tools from config files."""
    # Count language by file extension
    lang_counter: Counter = Counter()

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        files = result.stdout.strip().split("\n") if result.returncode == 0 else []
    except subprocess.TimeoutExpired:
        logger.warning(f"git ls-files timed out in _detect_tech_stack for {repo_path}")
        files = []

    lang_counter = _count_langs_by_ext(files)

    total = sum(lang_counter.values()) or 1
    languages = {lang: round(count / total, 2) for lang, count in lang_counter.most_common(10)}

    # Detect frameworks from config files
    frameworks: list[str] = []
    configs = {
        "pyproject.toml": "python-project",
        "package.json": "node-project",
        "Cargo.toml": "rust-project",
        "go.mod": "go-project",
        "pom.xml": "java-maven",
        "build.gradle": "java-gradle",
        "Gemfile": "ruby-project",
    }

    for config_file, framework in configs.items():
        if (repo_path / config_file).exists():
            frameworks.append(framework)

    return {
        "languages": languages,
        "frameworks": frameworks,
    }


def _get_git_stats(repo_path: Path) -> dict[str, Any]:
    """Get git statistics: total commits, contributors, recent activity."""
    stats: dict[str, Any] = {"total_commits": 0, "contributors": [], "last_commit_date": ""}

    # Total commits
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        try:
            stats["total_commits"] = int(result.stdout.strip())
        except (ValueError, AttributeError):
            stats["total_commits"] = 0

    # Contributors
    result = subprocess.run(
        ["git", "shortlog", "-sn", "--no-merges", "-n", "10"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        stats["contributors"] = [
            line.strip().split("\t", 1)[-1]
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ][:10]

    # Last commit date
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ai"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        stats["last_commit_date"] = result.stdout.strip()

    return stats


def _read_readme(repo_path: Path) -> str:
    """Read README content (first 200 lines). Uses safe file read with containment check."""
    for name in ["README.md", "readme.md", "README.rst", "README.txt", "README"]:
        content = _safe_file_read(repo_path / name, repo_path)
        if content:
            lines = content.split("\n")
            return "\n".join(lines[:200])
    return ""


def _find_config_files(repo_path: Path) -> dict[str, str]:
    """Find and read key config files (first 50 lines each). Uses safe file reads."""
    config_names = [
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
        "Makefile", "Dockerfile", "docker-compose.yml",
        ".github/workflows/ci.yml", ".github/workflows/ci.yaml",
    ]

    configs: dict[str, str] = {}
    for name in config_names:
        content = _safe_file_read(repo_path / name, repo_path)
        if content:
            lines = content.split("\n")
            configs[name] = "\n".join(lines[:50])

    return configs


# ─── Data Transformers ───

def gotchas_for_agents_md(raw_gotchas: list[dict[str, str]]) -> list[dict[str, str]]:
    """Transform parse_git_gotchas output into render_agents_md input format.

    parse_git_gotchas returns: {when, risk, because}
    render_agents_md expects: {summary, evidence}
    """
    return [
        {
            "summary": f"{g['when']} — {g['risk']}",
            "evidence": g["because"],
        }
        for g in raw_gotchas
    ]


# ─── AGENTS.md Template Rendering ───

def render_agents_md(data: dict[str, Any]) -> str:
    """Render AGENTS.md from structured data. Output MUST be ≤150 lines.

    ⚠️ INPUT IS AN AGENT-ASSEMBLED dict, NOT the exported code-intel.json
    (run_5647c72c). This reads `modules[].path`/`.responsibility` and
    `entry_points[].path`/`.description` — the AGENTS.md authoring shape assembled
    by the skill's GENERATE step (INSTRUCTIONS.md §4.5), NOT the exporter shape
    (which uses `symbol_count`/`file_path`). Do NOT feed a code-intel.json
    `modules`/`entry_points` array here — it lacks these keys and will KeyError.
    The two schemas are deliberately different artifacts (AGENTS.md vs code-intel).

    Args:
        data: Dict with keys: project_name, build_command, test_command,
              lint_command, test_duration, modules, entry_points,
              critical_rules, gotchas, score, generated_date.
    """
    lines: list[str] = []

    # Header
    lines.append(f"# {data['project_name']}")
    lines.append("")
    lines.append(
        f"> AI-Ready (DDD) | Generated {data['generated_date']} "
        f"| Score: {data['score']}/10 | [Review Report](.ai-ready/REVIEW-REPORT.md)"
    )
    lines.append("")

    # Quick Start
    lines.append("## Quick Start")
    lines.append(f"```")
    lines.append(f"{data.get('build_command', 'make build')}     # Build")
    lines.append(f"{data.get('test_command', 'make test')}      # Test ({data.get('test_duration', '~30s')})")
    if data.get("lint_command"):
        lines.append(f"{data['lint_command']}      # Lint")
    lines.append("```")
    lines.append("")

    # Architecture
    modules = data.get("modules", [])
    lines.append(f"## Architecture ({len(modules)} modules)")
    for mod in modules[:15]:  # Cap at 15 modules
        lines.append(f"- `{mod['path']}` — {mod['responsibility']}")
    lines.append("")

    # Entry Points
    entry_points = data.get("entry_points", [])
    if entry_points:
        lines.append("## Entry Points")
        for ep in entry_points[:5]:
            lines.append(f"- `{ep['path']}` → {ep['type']} ({ep['description']})")
        lines.append("")

    # Critical Rules
    rules = data.get("critical_rules", [])
    if rules:
        lines.append("## Critical Rules")
        for rule in rules[:10]:
            prefix = "❌" if rule.get("type") == "never" else "✅"
            lines.append(f"- {prefix} {rule['rule']} — {rule['reason']}")
        lines.append("")

    # Top Gotchas
    gotchas = data.get("gotchas", [])
    if gotchas:
        lines.append("## Top Gotchas")
        for i, g in enumerate(gotchas[:5], 1):
            lines.append(f"{i}. {g['summary']} (evidence: {g['evidence']})")
        lines.append("")

    # Deep Context (DDD) table
    lines.append("## Deep Context (DDD)")
    lines.append("| Need to understand... | Read |")
    lines.append("|---|---|")
    lines.append("| Why this exists, what's out of scope | [PRODUCT.md](.ai-ready/PRODUCT.md) |")
    lines.append("| Architecture, conventions, invariants | [TECH.md](.ai-ready/TECH.md) |")
    lines.append("| What failed, known issues, patterns | [IMPROVEMENT.md](.ai-ready/IMPROVEMENT.md) |")
    lines.append("| Current priorities, active decisions | [PROJECT.md](.ai-ready/PROJECT.md) |")
    lines.append("| Module dependencies, blast radius | [code-intel.json](.ai-ready/code-intel.json) |")
    lines.append("")

    # User section marker
    lines.append("<!-- user: Your additions below — refresh preserves this section -->")

    # Enforce ≤150 line hard limit — trim gotchas and rules if over
    MAX_LINES = 150
    if len(lines) > MAX_LINES:
        # Find sections we can trim (gotchas first, then rules)
        for section_header in ("## Top Gotchas", "## Critical Rules"):
            if len(lines) <= MAX_LINES:
                break
            start = next((i for i, l in enumerate(lines) if l == section_header), -1)
            if start == -1:
                continue
            end = next(
                (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            # Keep header + max 2 items + blank line
            keep = min(4, end - start)
            lines = lines[:start + keep] + lines[end:]

    return "\n".join(lines)


# ─── Import Graph Extraction ───

_IMPORT_PATTERNS = {
    # Python: matches "from X import" and "import X" at start of line
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"),
    # TypeScript/JS: matches import...from and require() ANYWHERE in line (uses search, not match)
    "typescript": re.compile(r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))"""),
    # Go: matches quoted import paths (indented in import blocks)
    "go": re.compile(r'^\s*"([^"]+)"'),
}

# TypeScript/JS patterns need search() not match() because require() can appear mid-line
_SEARCH_LANGS = {"typescript"}


def extract_import_graph(repo_path: Path) -> dict[str, Any]:
    """Extract REAL dependency graph from actual import statements in source code.

    Returns dict with:
      - modules: list of {name, path, imports_from, imported_by}
      - edges: list of {from, to, file, line}
      - stats: {files_scanned, edges_found}

    This function does NOT guess. Every edge has a source file:line citation.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get all source files
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return {"modules": [], "edges": [], "stats": {"files_scanned": 0, "edges_found": 0}}

    all_files = [f for f in result.stdout.strip().split("\n") if f]

    # Filter to source files — use prioritized sampling for large repos
    source_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}
    source_files = [f for f in all_files if Path(f).suffix in source_extensions]

    # Large repo: prioritize important files (entry points, hot zones, interfaces first)
    if len(source_files) > 300:
        source_files = prioritized_file_list(repo_path, max_files=300)

    # Detect primary language
    lang_counter: Counter = Counter()
    for f in source_files:
        ext = Path(f).suffix
        if ext in (".py",):
            lang_counter["python"] += 1
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            lang_counter["typescript"] += 1
        elif ext == ".go":
            lang_counter["go"] += 1

    primary_lang = lang_counter.most_common(1)[0][0] if lang_counter else "python"

    # Extract imports from each source file
    edges: list[dict[str, str]] = []
    module_imports: dict[str, set] = {}  # file -> set of modules it imports
    files_scanned = 0

    for filepath in source_files[:300]:  # Cap at 300 files for large repos
        full_path = repo_path / filepath
        if not full_path.exists() or not full_path.is_file():
            continue

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        files_scanned += 1
        file_imports: set[str] = set()

        # Python: imports always at top (200 lines sufficient)
        # TypeScript: require() can appear anywhere — scan full file (capped at 500 lines)
        scan_limit = 500 if primary_lang in _SEARCH_LANGS else 200
        pattern = _IMPORT_PATTERNS.get(primary_lang)
        if not pattern:
            continue

        for line_num, line in enumerate(content.split("\n")[:scan_limit], 1):
            # TypeScript/JS uses search() (require can be mid-line)
            # Python/Go uses match() (imports are at line start)
            if primary_lang in _SEARCH_LANGS:
                m = pattern.search(line)
            else:
                m = pattern.match(line)

            if m:
                # Get the first non-None group
                imported = next((g for g in m.groups() if g), None)
                if imported:
                    file_imports.add(imported)
                    edges.append({
                        "from": filepath,
                        "to": imported,
                        "line": line_num,
                        "raw": line.strip(),
                    })

        if file_imports:
            module_imports[filepath] = file_imports

    # Build module-level summary (group by top-level package directory)
    dir_modules: dict[str, set] = {}
    file_to_module: dict[str, str] = {}  # filepath -> module name (for resolution)

    for filepath in source_files:
        parts = Path(filepath).parts
        if len(parts) >= 2:
            # Skip "src/" as a module name — use the next level
            module_name = parts[0] if parts[0] != "src" else (parts[1] if len(parts) > 2 else parts[0])
        else:
            module_name = Path(filepath).stem
        dir_modules.setdefault(module_name, set()).add(filepath)
        file_to_module[filepath] = module_name

    # Also index individual file stems within each module (for relative import resolution)
    # e.g., "myapp/database.py" → file_stem_to_module["database"] = "myapp"
    file_stem_to_module: dict[str, str] = {}
    for filepath in source_files:
        stem = Path(filepath).stem
        mod = file_to_module.get(filepath)
        if mod and stem != "__init__":
            file_stem_to_module[stem] = mod

    # Compute imports_from / imported_by per module
    modules: list[dict] = []
    for mod_name, mod_files in sorted(dir_modules.items()):
        imports_from: set[str] = set()
        for f in mod_files:
            for imp in module_imports.get(f, set()):
                if imp.startswith("."):
                    # Relative import: ".database" → resolve to module containing "database.py"
                    rel_name = imp.lstrip(".").split(".")[0] if imp.lstrip(".") else ""
                    if not rel_name:
                        continue  # "from . import X" = same module, skip
                    # Check if this relative import points to a file in a DIFFERENT module
                    resolved_module = file_stem_to_module.get(rel_name, mod_name)
                    if resolved_module != mod_name:
                        imports_from.add(resolved_module)
                    # Relative imports within same package are expected — not cross-module edges
                else:
                    # Absolute import: "mempalace.backends" → top-level = "mempalace"
                    imp_module = imp.split(".")[0]
                    if imp_module != mod_name and imp_module in dir_modules:
                        imports_from.add(imp_module)

        modules.append({
            "name": mod_name,
            "path": f"{mod_name}/",
            "files": sorted(mod_files)[:20],
            "imports_from": sorted(imports_from),
        })

    # Compute imported_by (inverse of imports_from)
    for mod in modules:
        mod["imported_by"] = sorted(
            m["name"] for m in modules
            if mod["name"] in m.get("imports_from", [])
        )

    return {
        "modules": modules,
        "edges": edges[:500],  # Cap for memory
        "stats": {
            "files_scanned": files_scanned,
            "edges_found": len(edges),
            "primary_language": primary_lang,
        },
    }


# ─── Output Path Resolution ───

def resolve_output_path(
    repo_path: Path,
    project_name: str | None = None,
    target: str | None = None,
) -> Path:
    """Resolve where to write AI-Ready output.

    Priority:
    1. User-specified target path (if provided)
    2. SwarmWS .artifacts/ directory (if running inside SwarmAI)
    3. Alongside the repo itself ({repo_parent}/ai-ready-{name}/)

    Always returns an absolute path. Creates directories if needed.
    """
    if target:
        out = Path(target).resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out

    repo_path = Path(repo_path).resolve()
    name = project_name or repo_path.name

    # Check if we're in SwarmAI workspace
    swarmws = Path.home() / ".swarm-ai" / "SwarmWS"
    if swarmws.exists():
        out = swarmws / "Projects" / "ai_ready_repo" / ".artifacts" / f"ai-ready-{name}"
        out.mkdir(parents=True, exist_ok=True)
        return out

    # Fallback: alongside repo
    out = repo_path.parent / f"ai-ready-{name}"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ─── AI-Ready Metadata ───

def build_ai_ready_meta(score: float, project_name: str) -> dict[str, Any]:
    """Build ai-ready.json metadata document."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "version": "1.0",
        "engine": "SwarmAI AI-Ready-Repo Engine",
        "generated_at": now,
        "project": project_name,
        "score": {
            "overall": score,
            "dimensions": {},  # Populated by LLM during GENERATE phase
        },
        "freshness": {
            "overall": "fresh",
            "last_structural_check": now,
            "last_semantic_refresh": now,
            "commits_since_refresh": 0,
            "per_file": {
                "PRODUCT.md": {"status": "fresh", "last_verified": now[:10]},
                "TECH.md": {"status": "fresh", "last_verified": now[:10]},
                "IMPROVEMENT.md": {"status": "fresh", "last_verified": now[:10]},
                "PROJECT.md": {"status": "fresh", "last_verified": now[:10]},
            },
        },
    }


# ─── Staleness Detection (P3: Self-Maintaining) ───

def check_staleness(output_path: Path, repo_path: Path) -> dict[str, Any]:
    """Compare current repo state against stored ai-ready.json snapshot.

    Returns:
        {
            "overall": "fresh" | "stale",
            "commits_since": int,
            "stale_files": ["TECH.md", ...],  # which DDD files are outdated
            "changes": ["new module added", "config changed", ...]
        }
    """
    output_path = Path(output_path)
    repo_path = _validate_repo_path(Path(repo_path))

    # Read stored snapshot
    meta_path = output_path / ".ai-ready" / "ai-ready.json"
    if not meta_path.exists():
        return {"overall": "stale", "commits_since": -1, "stale_files": ["all"], "changes": ["no ai-ready.json found"]}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"overall": "stale", "commits_since": -1, "stale_files": ["all"], "changes": ["corrupt ai-ready.json"]}

    # Get current repo state
    current_info = gather_repo_info(repo_path)
    stored_generated = meta.get("generated_at", "")

    # Count commits since generation
    commits_since = 0
    if stored_generated:
        # Extract date from ISO timestamp
        date_part = stored_generated.split("T")[0]
        try:
            result = subprocess.run(
                ["git", "log", f"--since={date_part}", "--oneline"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                commits_since = len([l for l in result.stdout.strip().split("\n") if l])
        except subprocess.TimeoutExpired:
            pass

    # Detect specific changes
    changes: list[str] = []
    stale_files: list[str] = []

    # Check file count delta (new modules?)
    stored_file_count = meta.get("score", {}).get("_file_count", 0)
    current_file_count = len(current_info["file_tree"])
    if stored_file_count and abs(current_file_count - stored_file_count) > 10:
        changes.append(f"file count changed: {stored_file_count} → {current_file_count}")
        stale_files.append("TECH.md")  # Architecture changed

    # Check config files changed
    stored_frameworks = set(meta.get("score", {}).get("_frameworks", []))
    current_frameworks = set(current_info["tech_stack"].get("frameworks", []))
    if stored_frameworks != current_frameworks:
        changes.append(f"frameworks changed: {stored_frameworks} → {current_frameworks}")
        stale_files.append("TECH.md")

    # Commits since threshold
    if commits_since > 50:
        changes.append(f"{commits_since} commits since last generation")
        stale_files.extend(["TECH.md", "IMPROVEMENT.md", "PROJECT.md"])
    elif commits_since > 20:
        changes.append(f"{commits_since} commits since last generation")
        stale_files.append("PROJECT.md")

    # Deduplicate
    stale_files = sorted(set(stale_files))

    overall = "stale" if stale_files else "fresh"
    return {
        "overall": overall,
        "commits_since": commits_since,
        "stale_files": stale_files,
        "changes": changes,
    }


def generate_hook_config(ide: str = "claude-code") -> dict[str, Any]:
    """Generate IDE hook configuration for auto-staleness detection.

    Returns a config dict that can be merged into the IDE's settings.
    Claude Code: .claude/settings.json hooks.
    Kiro: .kiro/hooks/ configuration.
    """
    if ide == "claude-code":
        return {
            "hooks": {
                "FileChanged": [{
                    "pattern": [
                        "src/**/index.*", "package.json", "pyproject.toml",
                        "Makefile", "Cargo.toml", "go.mod", "**/routes/**",
                        "**/api/**", "requirements.txt", "setup.py",
                    ],
                    "command": "echo '🔄 Code structure changed — run refresh ai-ready to update context.'",
                    "onFailure": "notify",
                    "_source": "ai-ready-engine",
                }]
            }
        }
    elif ide == "kiro":
        return {
            "hooks": {
                "onFileChange": {
                    "patterns": ["src/**", "package.json", "pyproject.toml"],
                    "action": "notify",
                    "message": "🔄 Code structure changed — run 'refresh ai-ready' to update context.",
                    "_source": "ai-ready-engine",
                }
            }
        }
    return {}


# ─── Incremental Update (Competitive Feature #2) ───

def incremental_update(output_path: Path, repo_path: Path) -> dict[str, Any]:
    """Detect changed files since last generation and return what needs re-analysis.

    Uses git diff against the commit hash stored in ai-ready.json.
    Returns only the files that need re-processing — not the full repo.

    Returns:
        {
            "needs_update": bool,
            "changed_files": [str],  # relative paths of changed source files
            "new_files": [str],      # files added since last gen
            "deleted_files": [str],  # files removed since last gen
            "commits_since": int,
            "last_commit": str,      # current HEAD hash
        }
    """
    repo_path = _validate_repo_path(Path(repo_path))
    output_path = Path(output_path)

    # Read stored generation commit
    meta_path = output_path / ".ai-ready" / "ai-ready.json"
    if not meta_path.exists():
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "no ai-ready.json — full regeneration needed"}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "corrupt ai-ready.json"}

    stored_commit = meta.get("_last_commit", "")
    if not stored_commit:
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": -1, "last_commit": "", "reason": "no stored commit hash — full regeneration needed"}

    # Get current HEAD
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        current_head = result.stdout.strip() if result.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        current_head = ""

    if not current_head:
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": "", "reason": "cannot determine HEAD"}

    if current_head == stored_commit:
        return {"needs_update": False, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": current_head}

    # Verify stored commit exists locally (handles force-push + shallow clone)
    try:
        verify = subprocess.run(
            ["git", "cat-file", "-e", stored_commit],
            cwd=repo_path, capture_output=True, timeout=5,
        )
        if verify.returncode != 0:
            return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                    "commits_since": -1, "last_commit": current_head,
                    "reason": "stored commit not in local history (force-push or shallow clone) — full regen needed"}
    except subprocess.TimeoutExpired:
        pass  # Continue anyway — best effort

    # Get diff between stored commit and HEAD
    source_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt", ".swift"}

    try:
        # Changed/modified files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=M", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        changed = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # New files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        new = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # Deleted files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=D", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        deleted = [f for f in result.stdout.strip().split("\n") if f and Path(f).suffix in source_exts]

        # Commit count
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{stored_commit}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        commits_since = int(result.stdout.strip()) if result.returncode == 0 else 0

    except (subprocess.TimeoutExpired, ValueError):
        return {"needs_update": True, "changed_files": [], "new_files": [], "deleted_files": [],
                "commits_since": 0, "last_commit": current_head, "reason": "git diff failed"}

    needs_update = bool(changed or new or deleted)
    return {
        "needs_update": needs_update,
        "changed_files": changed,
        "new_files": new,
        "deleted_files": deleted,
        "commits_since": commits_since,
        "last_commit": current_head,
    }


# ─── Guided Learning Tour (Competitive Feature #4) ───

def generate_learning_tour(import_graph: dict[str, Any]) -> list[dict[str, str]]:
    """Generate a topologically-sorted learning order for modules.

    "Learn the codebase in the right order" — start with modules that have
    no dependencies (foundations), then modules that only depend on those,
    and so on. This gives a new developer the optimal reading path.

    Returns list of {name, path, reason, depends_on} in learning order.
    """
    modules = import_graph.get("modules", [])
    if not modules:
        return []

    # Build adjacency: module_name → set of INTERNAL dependencies only
    all_module_names = {m["name"] for m in modules}
    deps: dict[str, set] = {}
    name_to_module: dict[str, dict] = {}
    for mod in modules:
        name = mod["name"]
        name_to_module[name] = mod
        # Filter to only internal deps (external packages like numpy/fastapi don't count)
        deps[name] = set(mod.get("imports_from", [])) & all_module_names

    # Topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {name: 0 for name in deps}
    for name, mod_deps in deps.items():
        for dep in mod_deps:
            if dep in in_degree:
                in_degree[name] = in_degree.get(name, 0)  # dep adds to name's in-degree
                # Actually: name depends on dep, so dep has an edge TO name
                pass
    # Recompute: in_degree[X] = number of modules X depends on (that are in our set)
    for name, mod_deps in deps.items():
        in_degree[name] = len(mod_deps & set(deps.keys()))

    tour: list[dict[str, str]] = []
    queue = sorted([n for n, d in in_degree.items() if d == 0])
    visited: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        mod = name_to_module.get(current, {})
        dep_list = sorted(deps.get(current, set()) & set(deps.keys()))

        if not dep_list:
            reason = "Foundation — no internal dependencies. Start here."
        else:
            reason = f"Depends on: {', '.join(dep_list)} (learn those first)"

        tour.append({
            "name": current,
            "path": mod.get("path", f"{current}/"),
            "reason": reason,
            "depends_on": dep_list,
        })

        # Find modules whose in-degree drops to 0 after removing current
        for name, mod_deps in deps.items():
            if name in visited:
                continue
            if current in mod_deps:
                in_degree[name] -= 1
                if in_degree[name] <= 0 and name not in visited:
                    queue.append(name)
        queue.sort()

    # Add any remaining (cycles) at the end
    for name in deps:
        if name not in visited:
            mod = name_to_module.get(name, {})
            tour.append({
                "name": name,
                "path": mod.get("path", f"{name}/"),
                "reason": "Circular dependency — read after understanding the rest",
                "depends_on": sorted(deps.get(name, set()) & set(deps.keys())),
            })

    return tour


# ─── Multi-Package Support (P4) ───

def run_multi_package(
    repo_root: Path,
    output_base: Path,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Run engine analysis on a (mono)repo — AUTO-DETECTS package boundaries, then
    produces per-package material + cross-package synthesis. No hand-fed package list.

    Composes detect_package_roots() (workspace-manifest boundary detection) so the
    caller passes ONE repo root, not a pre-computed member list (run_a9fe5ad3 — the
    detector and this runner were shipped separately and never wired; now they are).
    A single-package repo degrades to exactly one package rooted at ".".

    Skill-native + core-free by design (C046): uses the skill's own gather_repo_info /
    extract_import_graph / parse_git_gotchas — never core.code_intel. The LLM GENERATE
    fan-out (per-package code-intel.json doc assembly) consumes THIS material; it is
    the INSTRUCTIONS.md orchestration layer, not this deterministic function.

    Args:
        repo_root: the (mono)repo root — package boundaries are detected from it
        output_base: base directory for all output
        project_name: optional system name (default: repo_root dir name)

    Returns:
        {
            "packages": [{name, root, path, output_path, language_mix, detected_by, stats}],
            "partition": [build_packages_partition dicts],  # the raw navigation partition
            "cross_package": {shared_deps, dep_order},
            "output_path": str,
            "project_name": str,
        }
    """
    repo_root = Path(repo_root)
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    if not project_name:
        project_name = repo_root.name or "multi-package"

    # AUTO-DETECT package boundaries (was: hand-fed repo_paths list).
    detected = detect_package_roots(repo_root)
    partition = build_packages_partition(repo_root)

    # Use the partition's DISAMBIGUATED names (path-suffixed on collision) as the
    # single source of package names — meta-review F-1: the F1 root-coverage guard
    # can prepend a root package whose name collides with a member (repo dir 'x' +
    # a member also named 'x'); raw pkg.name would then produce two identical names
    # → same pkg_output dir → the second clobbers the first. `root` is unique, so key
    # the disambiguated name by root.
    name_by_root = {p["root"]: p["name"] for p in partition}

    packages = []
    all_imports: dict[str, set] = {}  # package_name → set of external imports

    for pkg in detected:
        # pkg.root is POSIX-relative to repo_root ("." for a single-package repo).
        repo_path = repo_root if pkg.root == "." else (repo_root / pkg.root)
        if not repo_path.exists():
            continue

        pkg_name = name_by_root.get(pkg.root, pkg.name)
        # Filesystem-safe output dir: a disambiguated name can contain '/' (e.g.
        # 'sub/core') or ':' (':.') — flatten so pkg_output is a single dir segment.
        _safe_seg = pkg_name.replace("/", "__").replace(":", "__")
        pkg_output = output_base / _safe_seg

        # Run per-package analysis (each gets full budget)
        try:
            info = gather_repo_info(repo_path)
            graph = extract_import_graph(repo_path)
            gotchas = parse_git_gotchas(repo_path)
        except (ValueError, OSError):
            packages.append({"name": pkg_name, "root": pkg.root,
                             "path": str(repo_path), "error": "analysis failed"})
            continue

        # Track external imports for cross-package synthesis
        pkg_external_imports: set[str] = set()
        for edge in graph.get("edges", []):
            target = edge["to"]
            if not target.startswith("."):  # absolute import = potentially cross-package
                pkg_external_imports.add(target.split(".")[0])
        all_imports[pkg_name] = pkg_external_imports

        packages.append({
            "name": pkg_name,
            "root": pkg.root,
            "path": str(repo_path),
            "output_path": str(pkg_output),
            "language_mix": pkg.language_mix,
            "detected_by": pkg.detected_by,
            "stats": {
                "files": len(info["file_tree"]),
                "edges": graph["stats"]["edges_found"],
                "gotchas": len(gotchas),
            },
        })

    # Cross-package synthesis: find shared dependencies
    shared_deps: list[str] = []
    if len(all_imports) >= 2:
        # Find imports that appear in 2+ packages
        from collections import Counter
        import_counts: Counter = Counter()
        for pkg_imports in all_imports.values():
            for imp in pkg_imports:
                import_counts[imp] += 1
        shared_deps = [imp for imp, count in import_counts.items() if count >= 2]

    # Determine dependency order (which packages import which)
    dep_order: list[dict] = []
    pkg_names = {p["name"] for p in packages if "error" not in p}
    for pkg_name, pkg_imports in all_imports.items():
        for imp in pkg_imports:
            if imp in pkg_names and imp != pkg_name:
                dep_order.append({"from": pkg_name, "to": imp})

    return {
        "packages": packages,
        "partition": partition,
        "cross_package": {
            "shared_deps": sorted(shared_deps),
            "dep_order": dep_order,
        },
        "output_path": str(output_base),
        "project_name": project_name,
    }


# ─── ENRICH Phase (Targeted Questions) ───

def generate_enrich_questions(
    repo_info: dict[str, Any],
    gotchas: list[dict],
    import_graph: dict[str, Any],
) -> list[dict[str, str]]:
    """Generate max 5 targeted questions about what code analysis COULDN'T determine.

    The engine already knows: file structure, tech stack, git history, import graph,
    conventions from code patterns. ENRICH asks only what's INVISIBLE in code:
    business context, priorities, tribal knowledge not in git.

    Args:
        repo_info: from gather_repo_info()
        gotchas: from parse_git_gotchas()
        import_graph: from extract_import_graph()

    Returns list of {question, target_file, why} — max 5 items.
    """
    questions: list[dict[str, str]] = []

    # Q1: Purpose / audience (PRODUCT.md) — always ask unless README is very explicit
    readme = repo_info.get("readme_content", "")
    if len(readme) < 500 or "who" not in readme.lower():
        questions.append({
            "question": "Who are the primary users of this project, and what problem does it solve for them?",
            "target_file": "PRODUCT.md",
            "why": "README doesn't clearly state audience + value proposition",
        })

    # Q2: Non-goals (PRODUCT.md) — almost never in code
    questions.append({
        "question": "What is explicitly OUT OF SCOPE? What should this project NEVER do?",
        "target_file": "PRODUCT.md",
        "why": "Non-goals are almost never expressed in code — they prevent agents from building wrong things",
    })

    # Q3: Current priorities (PROJECT.md) — git shows activity, not intent
    questions.append({
        "question": "What are your top 1-3 priorities right now? What should agents focus on vs avoid?",
        "target_file": "PROJECT.md",
        "why": "Git shows what was done, not what should be done next",
    })

    # Q4: Constraints (PRODUCT.md) — compliance, SLA, business rules
    config_files = repo_info.get("config_files", {})
    has_ci = ".github/workflows/ci.yml" in config_files or ".github/workflows/ci.yaml" in config_files
    if not has_ci or len(gotchas) > 10:
        questions.append({
            "question": "Any compliance requirements, SLAs, or hard business rules an agent must respect?",
            "target_file": "PRODUCT.md",
            "why": "Regulatory/business constraints are invisible in code but critical for agent judgment",
        })

    # Q5: Tribal knowledge not in git (IMPROVEMENT.md) — only if gotchas are sparse
    if len(gotchas) < 5:
        questions.append({
            "question": "Any gotchas or 'things that burned you' that aren't captured in git history?",
            "target_file": "IMPROVEMENT.md",
            "why": f"Only found {len(gotchas)} evidence-grounded gotchas — verbal knowledge may fill gaps",
        })

    return questions[:5]


def classify_enrich_answer(answer: str) -> str:
    """Classify a user's ENRICH answer into the target DDD file.

    Simple heuristic classification — the LLM should use this as a fallback
    when the target isn't already known from the question context.

    Returns: "PRODUCT.md" | "TECH.md" | "IMPROVEMENT.md" | "PROJECT.md"
    """
    answer_lower = answer.lower()

    # PROJECT.md signals
    project_signals = ["this quarter", "priority", "blocked", "sprint", "focused on", "don't change", "migration"]
    if any(s in answer_lower for s in project_signals):
        return "PROJECT.md"

    # IMPROVEMENT.md signals
    improvement_signals = ["broke", "burned", "reverted", "don't touch", "gotcha", "incident", "failed"]
    if any(s in answer_lower for s in improvement_signals):
        return "IMPROVEMENT.md"

    # TECH.md signals
    tech_signals = ["always use", "never call", "convention", "pattern", "architecture", "we chose"]
    if any(s in answer_lower for s in tech_signals):
        return "TECH.md"

    # Default: PRODUCT.md (purpose, audience, constraints)
    return "PRODUCT.md"


# ─── Large Repo Sampling Strategy ───

def prioritized_file_list(repo_path: Path, max_files: int = 300) -> list[str]:
    """Get source files prioritized by importance, not alphabetical order.

    Priority order:
    1. Entry points (main.*, index.*, app.*, server.*)
    2. Hot zones (recently modified files — from git log)
    3. Config/interface files (base.*, types.*, config.*)
    4. Largest files by line count (more code = more important)
    5. Everything else (alphabetical within remaining budget)

    This ensures that even with a 300-file cap, the MOST IMPORTANT files
    are always included in the import graph.
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get all source files
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        all_files = [f for f in result.stdout.strip().split("\n") if f] if result.returncode == 0 else []
    except subprocess.TimeoutExpired:
        all_files = []

    source_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java", ".kt", ".swift"}
    source_files = [f for f in all_files if Path(f).suffix in source_exts]

    if len(source_files) <= max_files:
        return source_files  # No prioritization needed

    # Priority 1: entry points
    entry_patterns = {"main", "index", "app", "server", "cli", "__main__", "mod"}
    priority_1 = [f for f in source_files if Path(f).stem in entry_patterns]

    # Priority 2: recently modified (hot files from git log)
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:", "--name-only", "-n", "50"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        recent_files = set()
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip() and Path(line.strip()).suffix in source_exts:
                    recent_files.add(line.strip())
        priority_2 = [f for f in source_files if f in recent_files and f not in priority_1]
    except subprocess.TimeoutExpired:
        priority_2 = []

    # Priority 3: interface/config files
    interface_patterns = {"base", "types", "config", "interface", "schema", "models", "constants"}
    priority_3 = [f for f in source_files
                  if Path(f).stem in interface_patterns
                  and f not in priority_1 and f not in priority_2]

    # Priority 4+5: everything else (already have the rest)
    seen = set(priority_1 + priority_2 + priority_3)
    remaining = [f for f in source_files if f not in seen]

    # Assemble prioritized list
    prioritized = priority_1 + priority_2 + priority_3 + remaining
    result_files = prioritized[:max_files]

    # Log what was skipped
    skipped = len(source_files) - len(result_files)
    if skipped > 0:
        logger.warning(
            f"Large repo: {len(source_files)} source files, cap={max_files}. "
            f"Skipped {skipped} files (lowest priority). "
            f"Included: {len(priority_1)} entry points, {len(priority_2)} hot files, "
            f"{len(priority_3)} interfaces, {len(result_files) - len([f for f in result_files if f in set(priority_1 + priority_2 + priority_3)])} others."
        )

    return result_files


# ─── Output Verification (VERIFY Phase) ───

def select_verification_tasks(repo_path: Path) -> list[dict[str, Any]]:
    """Select 3 verification tasks from git history.

    Each task has:
      - type: "fix" | "feat" | "refactor"
      - description: commit subject (what to ask the agent)
      - correct_file: primary file changed in that commit (ground truth)
      - correct_functions: functions modified (from diff, if detectable)
      - commit: hash for evidence

    Selection:
      1. Most recent fix:/hotfix:/revert: commit
      2. Most recent feat: commit
      3. Most recent large-diff commit (or refactor:)
      Fallback: 3 most recent commits of any type
    """
    repo_path = _validate_repo_path(Path(repo_path))

    # Get recent commits with files changed
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%H|%s", "--name-only", "-n", "100"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return []

    if result.returncode != 0:
        return []

    # Parse commits
    commits = []
    current: dict | None = None
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        if "|" in line:
            parts = line.split("|", 1)
            if len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
                if current:
                    commits.append(current)
                current = {"hash": parts[0], "subject": parts[1], "files": []}
                continue
        if current and line.strip():
            current["files"].append(line.strip())
    if current:
        commits.append(current)

    # Select by type
    tasks: list[dict[str, Any]] = []
    fix_pattern = re.compile(r"^(fix|hotfix|revert|bugfix)[\s:(]", re.IGNORECASE)
    feat_pattern = re.compile(r"^feat[\s:(]", re.IGNORECASE)

    # Task 1: fix commit
    for c in commits:
        if fix_pattern.match(c["subject"]) and c["files"]:
            source_files = [f for f in c["files"] if not f.startswith("tests/") and f.endswith((".py", ".ts", ".js", ".rs", ".go"))]
            if source_files:
                tasks.append({
                    "type": "fix",
                    "description": c["subject"],
                    "correct_file": source_files[0],
                    "commit": c["hash"][:7],
                })
                break

    # Task 2: feat commit
    for c in commits:
        if feat_pattern.match(c["subject"]) and c["files"]:
            source_files = [f for f in c["files"] if not f.startswith("tests/") and f.endswith((".py", ".ts", ".js", ".rs", ".go"))]
            if source_files:
                tasks.append({
                    "type": "feat",
                    "description": c["subject"],
                    "correct_file": source_files[0],
                    "commit": c["hash"][:7],
                })
                break

    # Task 3: largest diff (most files changed)
    for c in sorted(commits[:30], key=lambda x: len(x["files"]), reverse=True):
        if c["hash"][:7] not in [t.get("commit") for t in tasks] and c["files"]:
            source_files = [f for f in c["files"] if not f.startswith("tests/") and f.endswith((".py", ".ts", ".js", ".rs", ".go"))]
            if source_files:
                tasks.append({
                    "type": "refactor",
                    "description": c["subject"],
                    "correct_file": source_files[0],
                    "commit": c["hash"][:7],
                })
                break

    # Fallback: use first 3 commits with source files
    if len(tasks) < 3:
        for c in commits:
            if len(tasks) >= 3:
                break
            if c["hash"][:7] in [t.get("commit") for t in tasks]:
                continue
            source_files = [f for f in c["files"] if not f.startswith("tests/") and f.endswith((".py", ".ts", ".js", ".rs", ".go"))]
            if source_files:
                tasks.append({
                    "type": "general",
                    "description": c["subject"],
                    "correct_file": source_files[0],
                    "commit": c["hash"][:7],
                })

    return tasks[:3]


def build_verification_prompt(ddd_content: dict[str, str], tasks: list[dict]) -> str:
    """Build the sub-agent verification prompt.

    Args:
        ddd_content: dict mapping filename → content string
            Expected keys: "AGENTS.md", "TECH.md", "IMPROVEMENT.md", "code-intel.json"
        tasks: list from select_verification_tasks()

    Returns:
        Complete prompt for the verification sub-agent.
        The prompt contains ONLY DDD text — no source code, no file paths to read.
    """
    prompt_parts = [
        "You are verifying AI-Ready artifacts. You have ONLY the following context",
        "about a codebase — no source code access, no file reading tools.",
        "",
        "Your job: for each task below, identify the CORRECT file and function",
        "to modify. Use ONLY the information provided. If the answer is not",
        "findable from these artifacts, say 'INSUFFICIENT — need: [what is missing]'.",
        "",
        "=" * 60,
        "ARTIFACTS (this is ALL you have):",
        "=" * 60,
        "",
    ]

    for filename, content in ddd_content.items():
        prompt_parts.append(f"### {filename}")
        prompt_parts.append("```")
        prompt_parts.append(content)
        prompt_parts.append("```")
        prompt_parts.append("")

    prompt_parts.append("=" * 60)
    prompt_parts.append("TASKS (answer each):")
    prompt_parts.append("=" * 60)
    prompt_parts.append("")

    for i, task in enumerate(tasks, 1):
        prompt_parts.append(f"Task {i} ({task['type']}): {task['description']}")
        prompt_parts.append(f"  → Which file would you modify?")
        prompt_parts.append(f"  → Which function/class in that file?")
        prompt_parts.append(f"  → What's your approach (1 sentence)?")
        prompt_parts.append("")

    prompt_parts.append("=" * 60)
    prompt_parts.append("FORMAT: For each task, respond exactly:")
    prompt_parts.append("  TASK N: FILE: <path> | FUNCTION: <name> | APPROACH: <1 sentence>")
    prompt_parts.append("  or: TASK N: INSUFFICIENT — need: <what specific info is missing>")

    return "\n".join(prompt_parts)


def evaluate_verification_response(
    response: str,
    tasks: list[dict],
) -> dict[str, Any]:
    """Evaluate the sub-agent's verification response against ground truth.

    Returns:
        {
            "passed": bool (2/3 correct = pass),
            "score": "2/3",
            "results": [{"task": ..., "correct": bool, "detail": str}],
            "feedback": [str] (specific gaps if any task failed)
        }
    """
    results = []
    feedback = []
    correct_count = 0

    for i, task in enumerate(tasks, 1):
        # Look for "TASK N:" in response
        task_pattern = re.compile(
            rf"TASK\s*{i}:?\s*(.*?)(?=TASK\s*{i+1}|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = task_pattern.search(response)

        if not match:
            results.append({"task": task["description"][:50], "correct": False, "detail": "No response found"})
            feedback.append(f"Task {i}: sub-agent gave no answer — DDD output may be unclear")
            continue

        answer = match.group(1).strip()

        if "INSUFFICIENT" in answer.upper():
            results.append({"task": task["description"][:50], "correct": False, "detail": f"Insufficient: {answer}"})
            # Extract what's missing for feedback
            need_match = re.search(r"need:\s*(.+)", answer, re.IGNORECASE)
            if need_match:
                feedback.append(f"Task {i} ({task['type']}): Missing from output — {need_match.group(1).strip()}")
            else:
                feedback.append(f"Task {i} ({task['type']}): Sub-agent said INSUFFICIENT but didn't specify what's missing")
            continue

        # Check if correct file is mentioned
        correct_file = task["correct_file"]
        # Match on filename (without full path) or full path
        filename = Path(correct_file).name
        file_stem = Path(correct_file).stem

        if correct_file in answer or filename in answer or file_stem in answer:
            correct_count += 1
            results.append({"task": task["description"][:50], "correct": True, "detail": f"Found: {filename}"})
        else:
            results.append({"task": task["description"][:50], "correct": False, "detail": f"Expected: {correct_file}, got: {answer[:80]}"})
            feedback.append(f"Task {i} ({task['type']}): Agent pointed to wrong file. Expected {correct_file}. TECH.md may need better module mapping for this area.")

    return {
        "passed": correct_count >= 2,
        "score": f"{correct_count}/{len(tasks)}",
        "results": results,
        "feedback": feedback,
    }


# ─── M5 Multi-package: deterministic package-boundary detection ───
#
# Navigational, NOT a correctness fix. Symbol ids are already path-qualified
# (parser.py:_qualify uses rel_path=relative_to(repo_root)) and route.id hashes
# file_path, so a monorepo does NOT collide — verified by Gate-0 (run_693e08de).
# Wired end-to-end (run_a9fe5ad3): run_multi_package(repo_root) AUTO-DETECTS via
# detect_package_roots (no hand-fed list); packages[] IS emitted into code-intel.json
# by BOTH producers (core json_exporter reindex + skill INSTRUCTIONS §4.6); the
# INSTRUCTIONS.md §4.9 monorepo fan-out orchestrates per-package GENERATE.
#
# Still skill-layer + core-free (C046): detection uses only stdlib + yaml/tomllib.
# Deferred: per-package full v3 (domains/flows/steps) generation is the LLM fan-out
# layer (§4.9 orchestration), not a deterministic helper.

import fnmatch as _fnmatch
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackageRoot:
    """A detected package boundary within a (mono)repo. `root` is POSIX-relative
    to repo_root ('.' for the repo itself). `language_mix` is {lang: file_count}
    from _LANG_EXTENSIONS, excluding _IGNORE_DIRS. `detected_by` names the
    manifest signal(s) that surfaced it (sorted, comma-joined)."""
    name: str
    root: str
    language_mix: dict = field(default_factory=dict)
    detected_by: str = ""


def _validate_dir_path(p: Path) -> Path:
    """Lighter sibling of _validate_repo_path for boundary detection: resolves
    symlinks (traversal safety) + requires exists/is-dir, but does NOT require
    .git. Boundary detection operates on manifests/filesystem, so it must work on
    a package sub-root or a non-git tarball — requiring git would break the real
    fan-out use case."""
    p = Path(p).resolve()
    if not p.exists():
        raise ValueError(f"Path does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"Path is not a directory: {p}")
    return p


def _rel_posix(path: Path, repo_root: Path) -> str:
    """Relative POSIX path of `path` under `repo_root`; '.' for repo_root itself."""
    rel = path.resolve().relative_to(repo_root.resolve())
    s = rel.as_posix()
    return s if s else "."


def _expand_globs(repo_root: Path, patterns) -> list[Path]:
    """Expand a list of workspace glob patterns ('packages/*', 'libs/core') to
    REAL directories on disk under repo_root. Non-dir / non-existent matches are
    dropped (a glob must never leak a literal pattern or a file — Gate-1 B3).
    Fail-soft: a bad pattern yields nothing, never raises."""
    dirs: list[Path] = []
    for pat in patterns or []:
        if not isinstance(pat, str) or not pat.strip():
            continue
        pat = pat.strip().rstrip("/")
        try:
            # Path.glob handles '*' / '**' relative to repo_root. A literal
            # (non-glob) pattern falls through to a direct existence check.
            if any(ch in pat for ch in "*?[]"):
                matches = list(repo_root.glob(pat))
            else:
                matches = [repo_root / pat]
        except (ValueError, OSError):
            continue
        for m in matches:
            try:
                if m.is_dir():
                    dirs.append(m)
            except OSError:
                continue
    return dirs


def _read_json_soft(path: Path):
    """Parse JSON, returning None on missing/malformed (never raises) — Gate-1 B2."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ── per-ecosystem manifest readers ──
# Contract (all): take repo_root, return list[Path] of REAL package dirs, or []
# if the manifest is absent/malformed/empty. NEVER raise.

def _npm_workspaces(repo_root: Path) -> list[Path]:
    data = _read_json_soft(repo_root / "package.json")
    if not isinstance(data, dict):
        return []
    ws = data.get("workspaces")
    patterns: list = []
    if isinstance(ws, list):
        patterns = ws
    elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
        patterns = ws["packages"]
    return _expand_globs(repo_root, patterns)


def _pnpm_workspaces(repo_root: Path) -> list[Path]:
    f = repo_root / "pnpm-workspace.yaml"
    if not f.is_file():
        return []
    try:
        import yaml
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs = data.get("packages")
    return _expand_globs(repo_root, pkgs) if isinstance(pkgs, list) else []


def _lerna(repo_root: Path) -> list[Path]:
    data = _read_json_soft(repo_root / "lerna.json")
    if not isinstance(data, dict):
        return []
    pkgs = data.get("packages")
    return _expand_globs(repo_root, pkgs) if isinstance(pkgs, list) else []


def _cargo_workspace(repo_root: Path) -> list[Path]:
    f = repo_root / "Cargo.toml"
    if not f.is_file():
        return []
    try:
        import tomllib
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    ws = data.get("workspace") if isinstance(data, dict) else None
    members = ws.get("members") if isinstance(ws, dict) else None
    return _expand_globs(repo_root, members) if isinstance(members, list) else []


def _go_modules(repo_root: Path) -> list[Path]:
    """Every go.mod BELOW the root (a nested go.mod = an independent module).
    The root go.mod itself is handled by the [root] fallback, not here."""
    dirs: list[Path] = []
    try:
        for gomod in repo_root.rglob("go.mod"):
            parent = gomod.parent
            if parent.resolve() == repo_root.resolve():
                continue  # root module → fallback covers it
            if any(_is_ignored_dir(p) for p in parent.relative_to(repo_root).parts):
                continue
            dirs.append(parent)
    except OSError:
        return []
    return dirs


def _python_packages(repo_root: Path) -> list[Path]:
    """Multiple pyproject.toml / setup.py in SUBDIRS = a python multi-package
    layout. Root-level manifest → [root] fallback, not here."""
    dirs: list[Path] = []
    seen: set = set()
    try:
        for marker in ("pyproject.toml", "setup.py"):
            for mf in repo_root.rglob(marker):
                parent = mf.parent
                if parent.resolve() == repo_root.resolve():
                    continue
                if any(_is_ignored_dir(p) for p in parent.relative_to(repo_root).parts):
                    continue
                rp = parent.resolve()
                if rp not in seen:
                    seen.add(rp)
                    dirs.append(parent)
    except OSError:
        return []
    return dirs


# nx.json / turbo.json are monorepo *signals* but carry no portable member list
# (nx infers projects from project.json files; turbo from the package manager's
# workspaces). We surface them as a signal that boosts confidence the repo IS a
# monorepo, but rely on the npm/pnpm reader for the actual member dirs. Presence
# alone never fabricates a member — it just tags detected_by.
def _monorepo_signal(repo_root: Path) -> list[str]:
    sigs = []
    for name in ("nx.json", "turbo.json"):
        if (repo_root / name).is_file():
            sigs.append(name.split(".")[0])
    return sigs


_PACKAGE_READERS = {
    "npm": _npm_workspaces,
    "pnpm": _pnpm_workspaces,
    "lerna": _lerna,
    "cargo": _cargo_workspace,
    "go": _go_modules,
    "python": _python_packages,
}


def _language_mix(abs_root: Path) -> dict:
    """{lang: file_count} under abs_root via the shared counter, excluding
    _IGNORE_DIRS. Uses git ls-files scoped to abs_root when possible (respects
    .gitignore), else rglob. Paths are relative to abs_root throughout."""
    files: list[str] = []
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=abs_root,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, OSError):
        files = []
    if not files:
        try:
            for p in abs_root.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(abs_root)
                if any(_is_ignored_dir(part) for part in rel.parts):
                    continue
                files.append(str(rel))
        except OSError:
            pass
    else:
        # git ls-files does not descend into ignored dirs, but be defensive
        files = [f for f in files
                 if not any(_is_ignored_dir(part) for part in Path(f).parts)]
    return dict(_count_langs_by_ext(files))


def detect_package_roots(repo_root) -> list[PackageRoot]:
    """Deterministically detect package boundaries in a (mono)repo from workspace
    manifests. Returns >=1 PackageRoot, ALWAYS (falls back to [root] when no
    multi-package signal is found). Dedups by resolved path so a dir surfaced by
    two manifests (e.g. npm + lerna both globbing packages/*) appears once, with
    its detected_by merged. Fail-soft throughout — a malformed manifest degrades
    to fewer signals, never an exception."""
    repo_root = _validate_dir_path(Path(repo_root))

    # resolved-path -> {"path": Path, "by": set[str]}
    found: dict = {}
    for label, reader in _PACKAGE_READERS.items():
        try:
            dirs = reader(repo_root)
        except Exception:
            dirs = []
        for d in dirs:
            try:
                key = d.resolve()
            except OSError:
                continue
            if key == repo_root.resolve():
                continue  # a member that IS the root → fallback territory
            entry = found.setdefault(key, {"path": d, "by": set()})
            entry["by"].add(label)

    signals = _monorepo_signal(repo_root)

    if not found:
        # Single-package repo: exactly [root]. detected_by records any monorepo
        # signal seen (nx/turbo present but no members) or "root".
        by = ",".join(sorted(signals)) if signals else "root"
        return [PackageRoot(
            name=repo_root.name,
            root=".",
            language_mix=_language_mix(repo_root),
            detected_by=by,
        )]

    roots: list[PackageRoot] = []
    for key in sorted(found, key=lambda k: str(k)):
        entry = found[key]
        d = entry["path"]
        by = sorted(entry["by"]) + signals
        roots.append(PackageRoot(
            name=d.name,
            root=_rel_posix(d, repo_root),
            language_mix=_language_mix(d),
            detected_by=",".join(sorted(set(by))),
        ))

    # Root-coverage guard (Gate-2 F1): a lone nested manifest (e.g. root app.py +
    # tools/gen/pyproject.toml) surfaces the nested dir as the ONLY member, silently
    # dropping the root application from the partition — the repo gets mislabeled a
    # monorepo whose main code vanishes. If the root carries substantive source that
    # NO detected member contains, include the root itself as a package so nothing is
    # lost. Language-mix comparison uses the root's OWN files vs the union of members'.
    root_mix = _language_mix(repo_root)
    member_total = sum(sum(r.language_mix.values()) for r in roots)
    root_total = sum(root_mix.values())
    if root_total > member_total:
        # The root has source files beyond what the members account for → represent it.
        roots.insert(0, PackageRoot(
            name=repo_root.name,
            root=".",
            language_mix=root_mix,
            detected_by=(",".join(sorted(signals)) if signals else "root"),
        ))
    return roots


def build_packages_partition(repo_root) -> list[dict]:
    """Wrap detect_package_roots() into navigation-metadata dicts for a
    code-intel.json `packages[]` partition. Emitted into code-intel.json by BOTH
    producers (run_a9fe5ad3): the core reindex writer (json_exporter.export_code_intel_json)
    and the skill GENERATE path (INSTRUCTIONS §4.6). Names are made unique
    (path-suffixed on collision) so two packages both named 'core' stay distinguishable."""
    roots = detect_package_roots(repo_root)
    # Two-pass (Gate-2 F2): disambiguate ALL colliding names symmetrically, not
    # just the 2nd+ occurrence — otherwise the first 'core' keeps the bare name
    # and is indistinguishable from a truly root-level 'core'. Pass 1 counts
    # names; pass 2 path-qualifies every name that collides.
    from collections import Counter as _Counter
    name_counts = _Counter(r.name for r in roots)
    out: list[dict] = []
    for r in roots:
        name = r.name
        if name_counts[name] > 1:
            parent = Path(r.root).parent.name
            name = f"{parent}/{r.name}" if parent and parent != "." else f"{r.name}:{r.root}"
        out.append({
            "name": name,
            "root": r.root,
            "language_mix": r.language_mix,
            "detected_by": r.detected_by,
        })
    return out
