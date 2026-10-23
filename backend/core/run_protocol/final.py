"""Pipeline v1 TEST final report construction and validation.

Public symbols:
- ``build_repo_final`` — fold one repo's signed runner calls into a sealed final row.
- ``merge_final`` — merge independently completed repo rows without losing identity.
- ``validate_final`` — fail-closed structural and provenance validation.
- ``FinalError`` — malformed or incomplete runner evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from core.run_protocol import events as protocol_events
from core.run_protocol.validations import manifest_digest, validate_manifest

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
CALL_TYPES = frozenset({"test", "build", "lint", "typecheck", "security_scan", "probe", "mutation"})
CALL_RESULTS = frozenset({"pass", "fail", "error", "not_run"})
TEST_VERDICTS = frozenset({"green", "red", "incomplete"})
VALIDATION_VERDICTS = frozenset({"pass", "fail", "error", "not_run"})
_CALL_BINDING_FIELDS = frozenset({"record_hash", "event_id", "event_hash", "event"})


class FinalError(ValueError):
    """Final evidence is malformed, incomplete, duplicated, or not tree-bound."""


def _canonical_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FinalError(f"record is not canonical JSON: {exc}") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def call_hash(call: Mapping[str, Any]) -> str:
    """Hash the immutable subprocess receipt, excluding its event-binding envelope."""
    return _canonical_hash({key: value for key, value in dict(call).items()
                            if key not in _CALL_BINDING_FIELDS})


def _bound_event(raw: Mapping[str, Any], record_hash: str) -> dict[str, Any]:
    event = raw.get("event")
    if not isinstance(event, Mapping):
        raise FinalError("runner call lacks its signed test_call event")
    value = dict(event)
    payload = value.get("payload")
    details = payload.get("details") if isinstance(payload, Mapping) else None
    if value.get("kind") != "test_call" or value.get("producer") != "runner" \
            or not SHA64.fullmatch(str(value.get("event_id") or "")) \
            or not SHA64.fullmatch(str(value.get("signature") or "")) \
            or not SHA64.fullmatch(str(value.get("authority_id") or "")) \
            or not isinstance(payload, Mapping) \
            or payload.get("seq") != raw.get("seq") \
            or payload.get("call_type") != raw.get("call_type") \
            or payload.get("code_tree") != raw.get("code_tree") \
            or payload.get("result") != raw.get("result") \
            or not isinstance(details, list) \
            or f"record_hash:{record_hash}" not in details:
        raise FinalError("runner call and signed event are not bound")
    if raw.get("event_id") != value["event_id"] \
            or raw.get("event_hash") != protocol_events.event_digest(value):
        raise FinalError("runner call event identity/hash mismatch")
    return value


def _calls(calls: Sequence[Mapping[str, Any]], *, repo: str, code_tree: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    seen_events: set[str] = set()
    for raw in calls:
        if not isinstance(raw, Mapping):
            raise FinalError("runner call must be an object")
        required = {"seq", "repo", "call_type", "code_tree", "argv", "tool_version",
                    "result", "started_at", "finished_at", "record_hash", "event_id",
                    "event_hash", "event"}
        if not required <= set(raw):
            raise FinalError("runner call lacks receipt/event provenance fields")
        seq = raw["seq"]
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or seq in seen:
            raise FinalError("runner call seq must be a unique positive integer")
        seen.add(seq)
        if raw["repo"] != repo:
            raise FinalError("runner call belongs to a different repo")
        if raw["call_type"] not in CALL_TYPES or raw["result"] not in CALL_RESULTS:
            raise FinalError("runner call type/result is invalid")
        if raw["code_tree"] != code_tree:
            raise FinalError("runner call belongs to a different code tree")
        if not isinstance(raw["argv"], list) or not raw["argv"] or any(
            not isinstance(arg, str) for arg in raw["argv"]
        ):
            raise FinalError("runner call argv is invalid")
        if not isinstance(raw["tool_version"], str) or not raw["tool_version"]:
            raise FinalError("runner call tool version is required")
        if not all(isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool)
                   for key in ("started_at", "finished_at")) \
                or raw["finished_at"] < raw["started_at"]:
            raise FinalError("runner call timing is invalid")
        record_hash = raw["record_hash"]
        if not SHA64.fullmatch(str(record_hash or "")) or record_hash != call_hash(raw):
            raise FinalError("runner call record_hash mismatch")
        event = _bound_event(raw, record_hash)
        if event["event_id"] in seen_events:
            raise FinalError("runner call event is duplicated")
        seen_events.add(event["event_id"])
        row = dict(raw)
        row["event"] = event
        out.append(row)
    return sorted(out, key=lambda row: row["seq"])


def _selection(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FinalError("selection provenance must be an object")
    selection = dict(value)
    required = {"files", "nodeids", "summary", "dependency_scope", "risk_rules", "mode"}
    if not required <= set(selection):
        raise FinalError("selection provenance lacks actual files/nodeids/summary/scope/rules")
    if any(not isinstance(selection[key], list) or any(not isinstance(item, str)
                                                       for item in selection[key])
           for key in ("files", "nodeids", "risk_rules")):
        raise FinalError("selection files/nodeids/risk_rules must be string lists")
    if len(selection["files"]) != len(set(selection["files"])) \
            or len(selection["nodeids"]) != len(set(selection["nodeids"])):
        raise FinalError("selection files/nodeids must be unique")
    if not isinstance(selection["summary"], Mapping) \
            or not isinstance(selection["dependency_scope"], Mapping) \
            or not isinstance(selection["mode"], str) or not selection["mode"]:
        raise FinalError("selection summary/dependency_scope/mode is invalid")
    return selection


def _final_record_hash(value: Mapping[str, Any]) -> str:
    row = dict(value)
    provenance = dict(row.get("provenance") or {})
    provenance.pop("record_hash", None)
    row["provenance"] = provenance
    return _canonical_hash(row)


def build_repo_final(
    *, repo: str, code_tree: str, base: str, calls: Sequence[Mapping[str, Any]],
    required_validation_ids: Sequence[str], validation_manifest: Mapping[str, Any],
    runner_code_hash: str, pipeline_code_hash: str, contract_pipeline_code_hash: str,
    runner_argv: Sequence[str], selection: Mapping[str, Any], cache_keys: Sequence[str],
    main_red: Mapping[str, Any], trace: Sequence[Mapping[str, Any]],
    carried_from: str | None = None, tests_required: bool = True,
    node_results: Mapping[str, Mapping[str, Any]] | None = None,
    tdd_required: Sequence[str] | None = None,
    whole_files: Sequence[str] | None = None,
    collected: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Fold current-tree, runner-signed calls into one repo final row.

    With *node_results* (each node's last outcome on this tree, from the runner's
    sealed receipts) the verdict is per node: a later run replaces an earlier one, so
    a continuation's not_run does not stick (fixing spec B2). Without it, the
    aggregate call results decide (callers that record no receipts)."""
    if not repo or not SHA40.fullmatch(code_tree or "") or not SHA40.fullmatch(base or ""):
        raise FinalError("repo, code_tree and base are required")
    for name, value in (("runner_code_hash", runner_code_hash),
                        ("pipeline_code_hash", pipeline_code_hash),
                        ("contract_pipeline_code_hash", contract_pipeline_code_hash)):
        if not SHA64.fullmatch(value or ""):
            raise FinalError(f"{name} must be sha256")
    if not isinstance(runner_argv, Sequence) or not runner_argv \
            or any(not isinstance(item, str) for item in runner_argv):
        raise FinalError("runner argv is required")
    if any(not SHA64.fullmatch(key or "") for key in cache_keys):
        raise FinalError("cache keys must be sha256")
    manifest = validate_manifest(validation_manifest)
    rows = _calls(calls, repo=repo, code_tree=code_tree)
    actual_selection = _selection(selection)
    tests = [row for row in rows if row["call_type"] == "test"]
    if node_results is not None:
        results = [str((row or {}).get("result")) for row in node_results.values()]
        if any(value not in {"pass", "fail", "incomplete"} for value in results):
            raise FinalError("node results must be pass / fail / incomplete")
        if "incomplete" in results or (tests_required and not results):
            verdict = "incomplete"
        elif "fail" in results:
            verdict = "red"
        else:
            verdict = "green"
    elif any(row["result"] in {"error", "not_run"} for row in tests) or (tests_required and not tests):
        verdict = "incomplete"
    elif any(row["result"] == "fail" for row in tests):
        verdict = "red"
    else:
        verdict = "green"
    required = tuple(dict.fromkeys(required_validation_ids))
    if len(required) != len(tuple(required_validation_ids)):
        raise FinalError("required validation ids must be unique")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        validation_id = row.get("validation_id")
        if validation_id is not None:
            if validation_id in by_id:
                raise FinalError(f"validation {validation_id} ran more than once")
            by_id[str(validation_id)] = row
    validations = [{"id": validation_id,
                    "verdict": (by_id[validation_id]["result"]
                                if validation_id in by_id else "not_run"),
                    "seq": by_id[validation_id]["seq"] if validation_id in by_id else None}
                   for validation_id in required]
    call_seqs = {call["seq"] for call in rows}
    trace_rows = [dict(row) for row in trace]
    for row in trace_rows:
        if not isinstance(row.get("exit"), str) or row.get("kind") not in {"test", "probe", "mutation"} \
                or row.get("result") not in {"pass", "fail", "error", "not_run"} \
                or not isinstance(row.get("seq"), int) or not isinstance(row.get("covers"), list):
            raise FinalError("trace row is malformed")
        if row["seq"] not in call_seqs:
            raise FinalError("trace references an unknown runner call")
    manifest_sha = manifest_digest(manifest)
    provenance = {
        "runner_code_hash": runner_code_hash,
        "pipeline_code_hash": pipeline_code_hash,
        "contract_pipeline_code_hash": contract_pipeline_code_hash,
        "argv": list(runner_argv),
        "selection": actual_selection,
        "summary": dict(actual_selection["summary"]),
        "dependency_scope": dict(actual_selection["dependency_scope"]),
        "risk_rules": list(actual_selection["risk_rules"]),
        "rules_version": str(actual_selection.get("rules_version") or ""),   # F-11
        "manifest_identity": {"sha256": manifest_sha, "base": base,
                              "required_validation_ids": list(required)},
        "validation_manifest": {"sha256": manifest_sha, "base": base,
                                "required_validation_ids": list(required)},
        "built_from": [{"seq": row["seq"], "record_hash": row["record_hash"],
                        "test_call_event_id": row["event_id"],
                        "test_call_event_hash": row["event_hash"]} for row in rows],
        "cache_keys": list(cache_keys),
        "main_red": dict(main_red),
    }
    final = {
        "repo": repo, "code_tree": code_tree, "base": base,
        "tests": {"verdict": verdict,
                  "blocking": [row["seq"] for row in tests if row["result"] == "fail"],
                  "pre_existing": [row["seq"] for row in tests if row.get("pre_existing") is True]},
        "validations": validations, "calls": rows, "provenance": provenance,
        "trace": trace_rows,
    }
    if node_results is not None:
        # each node's last result on this tree: what direct's BX-2 reads
        final["tests"]["nodes"] = {str(node): {"result": str(row["result"])}
                                   for node, row in sorted(node_results.items())}
    if tdd_required is not None:
        if any(not isinstance(item, str) or not item for item in tdd_required):
            raise FinalError("tdd_required must be repo paths")
        final["tdd_required"] = sorted(set(tdd_required))
    if whole_files is not None:
        final["tests"]["whole_files"] = sorted({str(item) for item in whole_files})
    if collected is not None:   # pytest --collect-only of the design's nodes (B8)
        final["tests"]["collected"] = sorted({str(item) for item in collected})
    if carried_from is not None:
        if not SHA40.fullmatch(carried_from):
            raise FinalError("carried_from must be a git commit id")
        final["carried_from"] = carried_from
    if generated_at is not None:
        final["generated_at"] = generated_at       # inside the record hash
    provenance["record_hash"] = _final_record_hash(final)
    return final


def merge_final(run_id: str, expected_repos: Sequence[str], rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    expected = tuple(expected_repos)
    if not run_id.startswith("run_") or not expected or len(expected) != len(set(expected)):
        raise FinalError("run id and unique expected repos are required")
    if set(rows) != set(expected):
        raise FinalError("final must contain exactly the expected repo set")
    result = {"schema": 1, "run_id": run_id,
              "repos": {repo: dict(rows[repo]) for repo in expected}}
    validate_final(result, expected_repos=expected)
    return result


def validate_final(value: Any, *, expected_repos: Sequence[str]) -> None:
    if not isinstance(value, Mapping) or value.get("schema") != 1:
        raise FinalError("final must be a schema-1 object")
    repos = value.get("repos")
    expected = set(expected_repos)
    if not expected or not isinstance(repos, Mapping) or set(repos) != expected:
        raise FinalError("final repo set is missing or unexpected")
    for repo, row in repos.items():
        if not isinstance(row, Mapping) or row.get("repo") != repo \
                or not SHA40.fullmatch(str(row.get("code_tree") or "")) \
                or not SHA40.fullmatch(str(row.get("base") or "")):
            raise FinalError(f"{repo}: invalid repo/tree/base")
        tests = row.get("tests")
        if not isinstance(tests, Mapping) or tests.get("verdict") not in TEST_VERDICTS:
            raise FinalError(f"{repo}: invalid test verdict")
        validations = row.get("validations")
        if not isinstance(validations, list) or len({item.get("id") for item in validations
                                                    if isinstance(item, Mapping)}) != len(validations):
            raise FinalError(f"{repo}: invalid validations")
        if any(not isinstance(item, Mapping) or item.get("verdict") not in VALIDATION_VERDICTS
               for item in validations):
            raise FinalError(f"{repo}: invalid validation verdict")
        provenance = row.get("provenance")
        calls = row.get("calls")
        if not isinstance(provenance, Mapping) or not isinstance(calls, list):
            raise FinalError(f"{repo}: missing provenance/calls")
        carried = provenance.get("carried_from")
        if carried is None:
            call_tree = str(row["code_tree"])
            manifest_base = row["base"]
        else:
            if not isinstance(carried, Mapping) \
                    or row.get("carried_from") != carried.get("base") \
                    or not SHA40.fullmatch(str(carried.get("base") or "")) \
                    or not SHA40.fullmatch(str(carried.get("code_tree") or "")) \
                    or not SHA64.fullmatch(str(carried.get("record_hash") or "")):
                raise FinalError(f"{repo}: invalid carried_from provenance")
            call_tree = str(carried["code_tree"])
            manifest_base = carried["base"]
            import copy
            source_row = copy.deepcopy(dict(row))
            source_row["base"] = carried["base"]
            source_row["code_tree"] = carried["code_tree"]
            source_row.pop("carried_from", None)
            source_provenance = dict(source_row["provenance"])
            source_provenance.pop("carried_from", None)
            source_provenance["record_hash"] = carried["record_hash"]
            source_row["provenance"] = source_provenance
            if _final_record_hash(source_row) != carried["record_hash"]:
                raise FinalError(f"{repo}: carried_from source record hash mismatch")
        checked_calls = _calls(calls, repo=str(repo), code_tree=call_tree)
        expected_built = [{"seq": call["seq"], "record_hash": call["record_hash"],
                           "test_call_event_id": call["event_id"],
                           "test_call_event_hash": call["event_hash"]}
                          for call in checked_calls]
        if provenance.get("built_from") != expected_built:
            raise FinalError(f"{repo}: built_from does not match runner calls")
        _selection(provenance.get("selection"))
        selection = provenance["selection"]
        if provenance.get("summary") != selection["summary"] \
                or provenance.get("dependency_scope") != selection["dependency_scope"] \
                or provenance.get("risk_rules") != selection["risk_rules"]:
            raise FinalError(f"{repo}: duplicated selection provenance disagrees")
        manifest = provenance.get("manifest_identity")
        if manifest != provenance.get("validation_manifest"):
            raise FinalError(f"{repo}: manifest identity disagrees")
        if not isinstance(manifest, Mapping) or not SHA64.fullmatch(str(manifest.get("sha256") or "")) \
                or manifest.get("base") != manifest_base \
                or not isinstance(manifest.get("required_validation_ids"), list):
            raise FinalError(f"{repo}: invalid validation manifest identity")
        for name in ("runner_code_hash", "pipeline_code_hash", "contract_pipeline_code_hash"):
            if not SHA64.fullmatch(str(provenance.get(name) or "")):
                raise FinalError(f"{repo}: invalid {name}")
        if provenance.get("record_hash") != _final_record_hash(row):
            raise FinalError(f"{repo}: final record_hash mismatch")


def carry_repo_final(value: Mapping[str, Any], *, new_base: str,
                     new_code_tree: str) -> dict[str, Any]:
    """Rebind a validated final after dependency-disjoint integration movement.

    The original signed calls remain attached to their original code tree.  Explicit
    ``carried_from`` provenance records that source identity; validation refuses any
    implicit old-tree/new-tree mixture.
    """
    import copy

    if not isinstance(value, Mapping) or not SHA40.fullmatch(new_base or "") \
            or not SHA40.fullmatch(new_code_tree or ""):
        raise FinalError("carried final requires valid new base/tree")
    row = copy.deepcopy(dict(value))
    provenance = row.get("provenance")
    if not isinstance(provenance, dict) or not SHA64.fullmatch(
            str(provenance.get("record_hash") or "")):
        raise FinalError("source final lacks a validated record hash")
    source = {
        "base": row.get("base"),
        "code_tree": row.get("code_tree"),
        "record_hash": provenance["record_hash"],
    }
    if not SHA40.fullmatch(str(source["base"] or "")) \
            or not SHA40.fullmatch(str(source["code_tree"] or "")):
        raise FinalError("source final identity is invalid")
    row["base"] = new_base
    row["code_tree"] = new_code_tree
    row["carried_from"] = source["base"]
    provenance["carried_from"] = source
    provenance["record_hash"] = _final_record_hash(row)
    return row
