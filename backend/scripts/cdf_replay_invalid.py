"""One-time: put back the review units refused for their format (design
docs/design/2026-10-31-memory-chain-audit.md M3).

Until 2026-10-30 11:25 the after-session review (S7) wrote some evidence ids without
their ``event:`` prefix; the validator refused those units, and they were kept only in
``extraction_attempts.invalid_units``. This puts the cognition units among them back:

- each unit's session is the one its attempt reviewed (``evidence_range.session_id``);
- an evidence id given bare gets its ``event:input:`` prefix only when that event is one
  of the session's registered inputs (``session_inputs``); an id that is not stays as it
  was, and the unit is refused again as missing evidence, never guessed;
- what the unit was read with is every input the session had registered before that
  review finished: a superset of the review's own read set, so a unit that read external
  content is still marked by it (a smaller set would weaken that check);
- ``project:SwarmWS`` (the workspace, not a project) becomes ``project:SwarmAI``;
- a unit whose evidence is not, after that, all registered inputs of its session (it cited
  a sentence or a message id) is left out: it points to nothing, and CDF keeps only what
  it can point to;
- the units in ``NO_LONGER_TRUE`` (judged 2026-10-31 against the code as it is) are left out;
- the rest are enqueued as S7 units of their session, so the normal decision applies
  (where it goes, what it rests on, whether it is already known).

Events, gaps and references among the refused units are not put back: their moment has
passed, and a gap's lifecycle belongs to its own chain. Dry run by default.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# titles whose claim the code no longer makes true (the mechanism was removed or changed)
NO_LONGER_TRUE = (
    "Redacted tombstone retains its cand: identity",
    "General Brain scope for the change feed covers both Knowledge/Learned and Knowledge/Library",
    "DDD decay is age-only; usage records do not feed it",
    ".ddd-usage.json writers are dead code",
    "DDD superseded_by marker must be inside the metadata comment",
    "DDD supersession mechanism deferred",
    "Withdrawing the highest EVOLUTION entry causes id reuse",
    "CDF judge `same` verdict is only 53% precise",
    "Fix `same` by requiring judge to enumerate",
    "Judge `adds` verdict treats same domain as same subject",
    "Verbatim quote check over-blocks `contradicts`",
    "Delete .ddd-usage.json rather than repair it",
)
_BARE = re.compile(r"^[0-9a-f]{32}$")
_SCOPES = {"project:swarmws": "project:SwarmAI"}


def _stale(title: str) -> bool:
    return any(str(title).startswith(t) for t in NO_LONGER_TRUE)


def candidates(conn: sqlite3.Connection, inputs: sqlite3.Connection) -> list[dict]:
    """The refused cognition units of S7, repaired, with their session and whether kept."""
    out = []
    for attempt_id, rng, invalid, finished in conn.execute(
            "SELECT attempt_id, evidence_range, invalid_units, finished_at FROM extraction_attempts "
            "WHERE source='S7' AND units_invalid>0 ORDER BY started_at"):
        session_id = (json.loads(rng or "{}") or {}).get("session_id")
        for item in json.loads(invalid or "[]"):
            raw = dict(item.get("raw") or {})
            if (raw.get("kind") or "cognition") != "cognition":
                continue
            evidence, fixed = [], 0
            for ref in raw.get("evidence") or []:
                ref = str(ref)
                if _BARE.match(ref) and inputs.execute(
                        "SELECT 1 FROM session_inputs WHERE event_id=? AND session_id=?",
                        (f"input:{ref}", session_id)).fetchone():
                    ref, fixed = f"event:input:{ref}", fixed + 1
                evidence.append(ref)
            raw["evidence"] = evidence
            scope = str(raw.get("scope") or "")
            raw["scope"] = _SCOPES.get(scope.casefold(), scope)
            read = tuple(r for (r,) in inputs.execute(
                "SELECT root_id FROM session_inputs WHERE session_id=? AND created_at<=?",
                (session_id, finished or "9999")))
            # every evidence item must name a registered input of the session: a unit that
            # cites a sentence or a message id instead can point to nothing, and CDF keeps
            # only what it can point to (it would be refused again as missing evidence)
            cited = bool(evidence) and all(
                ref.startswith("event:input:") and inputs.execute(
                    "SELECT 1 FROM session_inputs WHERE event_id=? AND session_id=?",
                    (ref[len("event:"):], session_id)).fetchone()
                for ref in evidence)
            out.append({"session_id": session_id, "attempt_id": attempt_id, "unit": raw, "read": read,
                        "title": raw.get("title"), "fixed": fixed, "cited": cited,
                        "keep": cited and not _stale(raw.get("title"))})
    return out


def run(*, apply: bool) -> dict:
    from config import get_app_data_dir
    from core.ingestion import api_v2, contracts_v2
    from core.ingestion.store import connect_ro
    conn = connect_ro()
    inputs = sqlite3.connect(f"file:{Path(get_app_data_dir()) / 'data.db'}?mode=ro", uri=True)
    try:
        found = candidates(conn, inputs)
        jobs = {sid: project for sid, project in conn.execute(
            "SELECT session_id, project FROM ingestion_s2_jobs")}
    finally:
        inputs.close()
        conn.close()
    report = {"refused_cognition": len(found), "no_evidence": sum(not f["cited"] for f in found),
              "no_longer_true": sum(f["cited"] and _stale(f["title"]) for f in found),
              "to_put_back": sum(f["keep"] for f in found), "results": {}}
    if not apply:
        report["units"] = [{"title": f["title"], "keep": f["keep"], "cited": f["cited"], "evidence_fixed": f["fixed"],
                            "scope": f["unit"].get("scope")} for f in found]
        return report
    for f in (f for f in found if f["keep"]):
        try:
            unit = contracts_v2.SOURCE_UNIT.validate_python(f["unit"])
        except Exception as exc:  # noqa: BLE001 - reported per unit, never stops the run
            report["results"][f["title"]] = f"invalid: {str(exc).splitlines()[0][:120]}"
            continue
        project = jobs.get(f["session_id"])
        result = api_v2.enqueue_unit(
            "S7", f["session_id"], unit, session_id=f["session_id"], run_id=None,
            read_root_ids=f["read"],
            project_snapshot={"session_project": project} if project else None,
            attempt_id=f["attempt_id"], extraction_pass="main")
        report["results"][f["title"]] = getattr(result, "status", str(result))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
