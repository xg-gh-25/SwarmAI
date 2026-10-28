"""Offline record and online replay for CDF Phase A.

- Record mode consumes pre-recorded card outputs and is deterministic.
- Online mode runs the real judge repeatedly and records replayable outputs.
- The corpus snapshot is hashed before and after every run and never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.ingestion.contracts import (  # noqa: E402
    CONTRACT_VERSION, NORMALIZER_VERSION, PROMPT_VERSION, RULES_VERSION,
    Candidate, SOURCE_PAYLOAD,
)
from core.ingestion.llm import BedrockJudge, JudgeResult
from core.ingestion.normalize import normalize_batch
from core.ingestion.pipeline import run_unit
from core.ingestion.prompts import (
    UntypedOutput, card1_prompt, card3_prompt, card3b_prompt, parse_card1, parse_card3,
    parse_card3b, prompt_hash,
)

_SOURCE_KINDS = {
    "remember": "remember", "persist": "remember", "correction": "correction",
    "distillation": "distillation", "daily_activity": "daily_activity", "backflow": "backflow",
    "memory_extractor": "extracted", "extracted": "extracted", "evolution_optimizer": "evolution",
    "evolution": "evolution", "reflect": "reflect", "cultivation": "cultivation",
}


class RecordJudge:
    """Judge facade that returns typed values from a replay record."""

    def __init__(self, recorded: dict[str, Any]) -> None:
        self.recorded = recorded
        self.calls = {"card1": 0, "card3": 0, "card3b": 0}

    def _next(self, card: str) -> Any:
        value = self.recorded.get(card)
        index = self.calls[card]
        self.calls[card] += 1
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            # Raw recorded replies: a call sequence for every card.
            value = value[min(index, len(value) - 1)]
        elif isinstance(value, list) and card in {"card1", "card3b"} and value and isinstance(value[0], (dict, str)):
            # A list is a call sequence for object-valued cards. Card 3 itself is an array.
            value = value[min(index, len(value) - 1)]
        return value

    def _vector(self, prompt: str) -> dict[str, Any]:
        supplied = self.recorded.get("version_vector", {})
        return {
            "model_id": supplied.get("model_id", "recorded"),
            "provider": supplied.get("provider", "record"),
            "provider_version": supplied.get("provider_version", "record-v1"),
            "temperature": supplied.get("temperature", 0.0),
            "top_p": supplied.get("top_p"),
            "seed": supplied.get("seed"),
            "prompt_hash": prompt_hash(prompt),
            "contract_version": CONTRACT_VERSION,
            "rules_version": RULES_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "prompt_version": PROMPT_VERSION,
            "runtime": f"python-{platform.python_version()}",
        }

    def card1(self, text: str, projects: tuple[str, ...], context: str = "") -> Any:
        value = self._next("card1")
        parsed = parse_card1(value) if isinstance(value, str) else parse_card1(json.dumps(value))
        return JudgeResult(parsed, self._vector(card1_prompt(text, projects, context)), json.dumps(value))

    def card3(self, text: str, hits: list[dict[str, Any]]) -> Any:
        value = self._next("card3")
        parsed = parse_card3(value, hits) if isinstance(value, str) else parse_card3(json.dumps(value))
        return JudgeResult(parsed, self._vector(card3_prompt(text, hits)), json.dumps(value))

    def card3b(self, old: str, candidate: str) -> Any:
        value = self._next("card3b")
        parsed = parse_card3b(value) if isinstance(value, str) else parse_card3b(json.dumps(value))
        return JudgeResult(parsed, self._vector(card3b_prompt(old, candidate)), json.dumps(value))


class CapturingJudge:
    """Capture real judge JSON so online decisions can be replayed."""

    def __init__(self, inner: BedrockJudge) -> None:
        self.inner = inner
        self.recorded: dict[str, list[Any]] = {"card1": [], "card3": [], "card3b": []}

    def _capture(self, card: str, call: Any) -> JudgeResult[Any]:
        """Record the RAW reply text for every call, typed or not, before re-raising."""
        try:
            result = call()
        except UntypedOutput as exc:
            self.recorded[card].append(exc.raw if exc.raw is not None else "")
            raise
        self.recorded[card].append(result.raw)
        return result

    def card1(self, text: str, projects: tuple[str, ...], context: str = "") -> Any:
        return self._capture("card1", lambda: self.inner.card1(text, projects, context))

    def card3(self, text: str, hits: list[dict[str, Any]]) -> Any:
        return self._capture("card3", lambda: self.inner.card3(text, hits))

    def card3b(self, old: str, candidate: str) -> Any:
        return self._capture("card3b", lambda: self.inner.card3b(old, candidate))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _temporary_inbox(loaded: list[tuple[Candidate, dict[str, Any]]], corpus: Path) -> tempfile.TemporaryDirectory[str]:
    """Persist replay inputs in a disposable SQLite Inbox outside the corpus."""
    # Disposable: the process temp dir, never next to (or inside) the corpus snapshot.
    temporary = tempfile.TemporaryDirectory(prefix="cdf-replay-")
    connection = sqlite3.connect(Path(temporary.name) / "ingestion.db")
    try:
        connection.execute(
            "CREATE TABLE ingestion_inbox (sequence INTEGER PRIMARY KEY, id TEXT UNIQUE, source TEXT, payload TEXT, envelope TEXT, project_snapshot TEXT, cluster_id TEXT, origin TEXT)"
        )
        for sequence, (candidate, _) in enumerate(loaded):
            connection.execute(
                "INSERT INTO ingestion_inbox VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sequence, candidate.id, candidate.source,
                 candidate.payload.model_dump_json(by_alias=True), candidate.envelope.model_dump_json(),
                 json.dumps(candidate.project_snapshot, sort_keys=True), candidate.cluster_id,
                 json.dumps(candidate.origin, sort_keys=True)),
            )
        connection.commit()
    finally:
        connection.close()
    return temporary


def _flatten(value: Any, prefix: str = "") -> dict[str, str]:
    """Flatten JSON fields to stable paths for consistency measurement."""
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else key
            out.update(_flatten(value[key], child))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: json.dumps(value, sort_keys=True)}


def _card_consistency(runs: list[dict[str, Any]]) -> dict[str, float]:
    """Return the share of stable leaf fields for each card across all runs."""
    result: dict[str, float] = {}
    for card in ("card0", "card1", "card2", "rules", "card3", "hit_judgments", "card3b"):
        flattened = [_flatten(run.get(card)) for run in runs]
        paths = set().union(*(item.keys() for item in flattened))
        if not paths:
            result[card] = 1.0
            continue
        stable = sum(len({item.get(path, "<missing>") for item in flattened}) == 1 for path in paths)
        result[card] = stable / len(paths)
    return result


def _outside_corpus(path: Path | None, corpus: Path) -> None:
    if path is None:
        return
    resolved = path.resolve()
    if resolved == corpus.resolve() or corpus.resolve() in resolved.parents:
        raise SystemExit("output path must be outside the read-only corpus root")


def _load(path: Path) -> list[tuple[Candidate, dict[str, Any]]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        recorded_by_unit = raw.pop("recorded_by_unit", None)
        recorded = recorded_by_unit if recorded_by_unit is not None else raw.pop("recorded", {})
        payload = dict(raw["payload"])
        payload.setdefault("kind", _SOURCE_KINDS.get(raw["source"], raw["source"]))
        raw["payload"] = SOURCE_PAYLOAD.validate_python(payload)
        try:
            rows.append((Candidate.model_validate(raw), recorded))
        except Exception as exc:
            raise ValueError(f"invalid candidate at line {line_number}: {exc}") from exc
    return rows


def _jsonable(decision: Any) -> dict[str, Any]:
    return decision.model_dump(mode="json", by_alias=True)


def replay(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Run a replay and return one result row per normalized unit."""
    corpus = args.corpus.resolve()
    _outside_corpus(args.output, corpus)
    _outside_corpus(args.recordings_out, corpus)
    before = _tree_digest(corpus)
    loaded = _load(args.candidates)
    temporary_inbox = _temporary_inbox(loaded, corpus)
    candidates = [item[0] for item in loaded]
    records = {item[0].id: item[1] for item in loaded}
    normalized = normalize_batch(candidates)
    rows: list[dict[str, Any]] = []
    recordings_by_candidate: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for candidate in candidates:
        for group in normalized[candidate.id]:
            per_run = []
            per_recording = []
            run_count = 1 if args.mode == "record" else args.runs
            for run_index in range(run_count):
                if args.mode == "record":
                    source = records[candidate.id]
                    unit_id = f"{candidate.id}.{group.slot}"
                    if isinstance(source, dict) and unit_id in source:
                        source = source[unit_id]
                    if isinstance(source, list):
                        source = source[min(run_index, len(source) - 1)]
                    judge: Any = RecordJudge(source)
                else:
                    judge = CapturingJudge(BedrockJudge(model_id=args.model_id))
                decision = run_unit(candidate, corpus, judge, group=group,
                                    exclude_origin=args.exclude_origin)
                per_run.append(_jsonable(decision))
                if isinstance(judge, CapturingJudge):
                    per_recording.append(judge.recorded)
            pairs = {(item["action"], json.dumps(item.get("destination"), sort_keys=True)) for item in per_run}
            stable = len(pairs) == 1
            row = dict(per_run[0])
            row["runs"] = per_run
            row["stable"] = stable
            row["card_consistency"] = _card_consistency(per_run)
            rows.append(row)
            if per_recording:
                recordings_by_candidate.setdefault(candidate.id, {})[row["unit_id"]] = per_recording

    after = _tree_digest(corpus)
    if before != after:
        raise RuntimeError("read-only corpus changed during replay")
    stability = sum(row["stable"] for row in rows) / len(rows) if rows else 1.0
    print(json.dumps({"units": len(rows), "stability": stability}, sort_keys=True), file=sys.stderr)
    if args.recordings_out:
        recorded_rows = []
        for candidate in candidates:
            raw = candidate.model_dump(mode="json", by_alias=True)
            raw["recorded_by_unit"] = recordings_by_candidate.get(candidate.id, {})
            recorded_rows.append(raw)
        args.recordings_out.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in recorded_rows),
            encoding="utf-8",
        )
    temporary_inbox.cleanup()
    return rows


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--mode", choices=("record", "online"), required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--exclude-origin", action="store_true")
    parser.add_argument("--model-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recordings-out", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    rows = replay(args)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
