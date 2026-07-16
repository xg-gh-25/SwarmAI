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
    """Validate repo path: must exist, be a directory, and contain .git.

    Resolves symlinks to prevent traversal attacks.
    Raises ValueError if validation fails.
    """
    repo_path = Path(repo_path).resolve()

    if not repo_path.exists():
        raise ValueError(f"Path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise ValueError(f"Path is not a directory: {repo_path}")
    if not (repo_path / ".git").exists():
        raise ValueError(f"Not a git repository (no .git): {repo_path}")

    return repo_path


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

_REQUIRED_TOP_LEVEL = {"$schema", "version", "repo", "modules", "edges", "entry_points"}
_REQUIRED_REPO = {"name", "languages", "total_symbols", "total_edges"}
_REQUIRED_MODULE = {"name", "path", "responsibility"}
_OPTIONAL_TOP_LEVEL = {"routes", "hot_zones", "risk_areas", "dead_code", "dependencies", "generated_at"}


def validate_code_intel_json(doc: dict) -> list[str]:
    """Validate a code-intel.json document against v2 schema.

    Returns list of error strings. Empty list = valid.
    Does NOT use jsonschema library — pure Python for zero-dep operation.
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

    # Entry points validation
    entry_points = doc.get("entry_points")
    if isinstance(entry_points, list):
        for i, ep in enumerate(entry_points):
            if not isinstance(ep, dict):
                errors.append(f"entry_points[{i}] must be a dict")
            elif "path" not in ep:
                errors.append(f"entry_points[{i}] must have 'path' field")

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

    Each assertion object anywhere in the domain layer:
    - MUST be a dict carrying an explicit boolean `verified` — a plain-string rule
      or a dict with no `verified` is an UN-adjudicated claim, flagged (else an LLM
      dodges the guard by omitting `verified` — Gate-2 HIGH, run_aad6d4f2).
    - `verified` MUST be a real bool (not "true"/"false"/1 — the `is True` identity
      check silently mis-branched string values, Gate-2 CRITICAL).
    - verified:true  → non-blank `anchor` (code file:line); else spurious (paper 0.67).
    - verified:false → non-blank `absence_evidence` (grep=0 proof); §1.5#4: a
      "rule doesn't exist" negative is unreliable unless proven absent (the exact
      false-negative that bit Run 0's fixed-column grep).
    Pure function → unit-testable + mutation-verifiable.
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
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]  # 32 bits
    return f"route:{slug}-{h}"


# ─── Incremental merge (Run 2, run_36266b66) ───

def merge_code_intel(baseline: dict, new_nodes: list, new_edges: list) -> dict:
    """Merge freshly-analyzed nodes/edges into a baseline graph (§2, UA keep-last).

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


def finalize_v3(doc: dict, domains: list, flows: list, steps: list) -> dict:
    """Assemble a generated domain layer into a v3 doc — FAIL-CLOSED gate.

    Attaches domains/flows/steps, bumps version to '3.0', then runs the existing
    Run-1 validators (structural + referential integrity + LLM-assertion guards).
    Raises ValueError with ALL errors if any guard fails — a generation that
    produces a dangling entry_ref or an unanchored 'verified:true' claim (§1.5
    spurious) is REJECTED, never persisted. Pure: deep-copies, never mutates input.

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
    out["domains"] = list(domains or [])
    out["flows"] = list(flows or [])
    out["steps"] = list(steps or [])
    out["version"] = "3.0"
    errors = validate_code_intel_json(out)
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

    - completeness (recall proxy): fraction of flows that resolve to a real route
      entry_ref (an unanchored flow = a missing/hallucinated element).
    - precision (consistency): fraction of assertions that are VERIFIED (a real
      bool True + non-blank anchor). FP here = spurious (paper: LLM 0.67) — an
      un-anchored / verified:false assertion counts against precision.
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

    # completeness: flows anchored to a real route
    anchored = sum(1 for f in flows
                   if f.get("entry_type") != "http" or f.get("entry_ref") in route_ids)
    n_flows = len(flows)
    completeness = anchored / n_flows if n_flows else 0.0

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

    f1 = (2 * completeness * precision / (completeness + precision)
          if (completeness + precision) else 0.0)

    return {
        "completeness": round(completeness, 4),
        "precision": round(precision, 4),
        "explicit": round(explicit, 4),
        "f1": round(f1, 4),
        "denominators": {"flows": n_flows, "assertions": total_assert, "steps": n_steps},
    }


def project_domain_skeleton(domain: dict, flows: list, steps: list) -> str:
    """Deterministically project ONE domain (+ its flows/steps) into the 8-section
    `.spec.md` skeleton (§3.2). Pure string render — NO LLM. The skeleton region
    (§1-4,6-7) is `domains[]`-authoritative; the §5 [human] region is left as a
    stub for human authorship (owned by spec-details, protected on merge §8.2).

    LLM domain EXTRACTION and prose THICKENING are out of scope for Run 3 (the
    dropped Run-1 generation piece) — this renders the machine skeleton from an
    already-populated domains[] entry, which is what exists today.
    """
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

    for f in files:
        ext = Path(f).suffix.lower()
        if ext in _LANG_EXTENSIONS:
            lang_counter[_LANG_EXTENSIONS[ext]] += 1

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
    repo_paths: list[Path],
    output_base: Path,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Run engine analysis on multiple packages, produce per-package output + cross-package synthesis.

    Each package gets independent file/edge budgets.
    Cross-package context identifies shared dependencies across packages.

    Args:
        repo_paths: list of paths to package roots
        output_base: base directory for all output
        project_name: optional system name (default: parent dir name)

    Returns:
        {
            "packages": [{name, path, output_path, stats}],
            "cross_package": {shared_deps, dep_order},
            "output_path": str
        }
    """
    output_base = Path(output_base)
    output_base.mkdir(parents=True, exist_ok=True)

    if not project_name:
        # Use common parent directory name
        parents = [p.parent for p in repo_paths]
        project_name = parents[0].name if parents else "multi-package"

    packages = []
    all_imports: dict[str, set] = {}  # package_name → set of external imports

    for repo_path in repo_paths:
        repo_path = Path(repo_path)
        if not repo_path.exists():
            continue

        pkg_name = repo_path.name
        pkg_output = output_base / pkg_name

        # Run per-package analysis (each gets full budget)
        try:
            info = gather_repo_info(repo_path)
            graph = extract_import_graph(repo_path)
            gotchas = parse_git_gotchas(repo_path)
        except (ValueError, OSError):
            packages.append({"name": pkg_name, "path": str(repo_path), "error": "analysis failed"})
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
            "path": str(repo_path),
            "output_path": str(pkg_output),
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
