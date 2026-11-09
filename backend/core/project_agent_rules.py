"""project_agent_rules — a Brain's rules: the person's own words, verified once when added
(design docs/design/2026-11-09-project-agent-contract.md §4).

A rule is a standing requirement the person gave for one Brain, kept in the ``rules``
list of the Brain's ``project-agent.yaml`` as their words (``said``) and the day it was
added. It is written only by ``add_rule`` (the ``scripts.project_agent_rule`` command):
the words must be one whole sentence, or several in a row, of the person's latest message
of this session (``whole_sentences``, in CDF's normalization ``ingestion.pipeline_v2._quote_norm``,
on the hash-checked authored text ``session_inputs.user_authored_text``; a part of a sentence
could drop a "never" or a "不要" and say the opposite), at least ``MIN_CHARS`` characters once
normalized. Removing one needs a turn the person opened whose message contains the whole
rule. The session is the run owner gate's (``scripts.project_agent_rule``).

What this guards against is the agent's mistake: recording a rule the person did not say,
or a piece of what they said. Like every command an agent runs (the pipeline's, ``ingest
submit``), it is not a defence against an agent built to get around it: a command that
hides its session (a variable that builds the name, a script by path, ``py -c``) is not
seen, the product-wide limit of checks made in the agent's own process (S6-3).

Each verification appends one JSON line to
``<app data>/state/pipeline_agent_audit/project_agent_rules/<brain>.jsonl``: inside the
directory the audit write gate already protects from the agent's writes, moves and
deletes (``security_hooks.create_audit_marker_write_gate``), the guard level of the
pipeline's own audit log.

It is verified once, never on read: the message expires after 90 days and its event id
means nothing on another machine. ``verified_rules`` gives the rules whose words have a
record, and the others apart (a row written by hand or by an agent is never delivered).
Rules are never a problem of the definition (``project_agent.validate`` ignores the key)
and never block a dispatch.

Key public symbols:
- ``MIN_CHARS``            the shortest rule, normalized
- ``rules_dir()``          where the verification records are
- ``quote_key(said)``      sha256 of the normalized words: what a record keeps
- ``add_rule`` / ``remove_rule``  the only writers of ``rules``
- ``rule_rows(project_dir)`` every rule with its ``verified`` flag (the Hub's list)
- ``verified_rules(project_dir)`` -> (verified, unverified), each ``[{said, added}]``
- ``current_rules(path)``  the ``rules`` value of a definition file as it is now (the draft
  writer carries it, so a redraft never drops a rule added meanwhile)

Not here yet: delivering the verified rules to chat, review experts and Hive tasks, and
CDF keeping such a rule out of STEERING. Both land together in the delivery run (design
§3, §4.3): until rules are delivered, STEERING stays the path that reaches every session.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

MIN_CHARS = 6
RULES_KEY = "rules"
_SUFFIX = ".jsonl"
# what ends a sentence of the person's message: a rule is one sentence or several in a row,
# never a part of one (a part can drop a "never" or a "不要" and say the opposite)
# a "." "!" "?" ends a sentence before a space, the end, a CJK character (Chinese written
# with half-width marks) or a closing quote or bracket, and not as the end of an
# abbreviation (i.e., e.g., etc., vs., any case): "v2.0" and "i.e. deploy" stay in their sentence
_SENTENCE_END = re.compile(
    r"[。！？\n]|(?<!\b[A-Za-z]\.[A-Za-z])(?<!\b(?i:etc))(?<!\b(?i:vs))[.!?]"
    r"(?=\s|$|[^\x00-\x7F]|[\"'”’)\]])")
_EDGE = " \t\r\n。！？.!?\"'“”‘’「」『』"


class RuleError(ValueError):
    """Why a rule was not added or removed (the command prints it)."""


def rules_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "pipeline_agent_audit" / "project_agent_rules"


def _norm(text: str) -> str:
    from core.ingestion.pipeline_v2 import _quote_norm
    return _quote_norm(text)


def quote_key(said: str) -> str:
    return hashlib.sha256(_norm(said).encode("utf-8")).hexdigest()


def _record_file(brain: str) -> Path:
    # brain names are checked by project_agent_session.brain_dir before any write
    return rules_dir() / f"{brain}{_SUFFIX}"


def whole_sentences(said: str, text: str) -> bool:
    """Whether *said* is one whole sentence of *text*, or several in a row, compared in CDF's
    normalization (case, width, whitespace and quotation marks do not count)."""
    target = _norm(said.strip(_EDGE))
    if not target:
        return False
    marks = list(_SENTENCE_END.finditer(text))
    starts = [0] + [m.end() for m in marks]
    ends = [m.start() for m in marks] + [len(text)]
    return any(_norm(text[i:j].strip(_EDGE)) == target for i in starts for j in ends if j > i)


def _record_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return keys
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("key"), str):
            keys.add(row["key"])
    return keys


def _rows(doc: Any) -> list[dict[str, Any]]:
    """The rule rows of a parsed definition that are mappings with a text ``said``."""
    raw = doc.get(RULES_KEY) if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict) and isinstance(r.get("said"), str) and r["said"].strip()]


def _definition(project_dir: Path) -> Path:
    from core.project_agent import PROJECT_AGENT_FILE
    return Path(project_dir) / PROJECT_AGENT_FILE


def _read(path: Path) -> tuple[list[str], dict[str, Any]]:
    """(header comment lines, parsed mapping) of a definition. Raises RuleError."""
    if not path.is_file() or path.is_symlink():
        raise RuleError("this Brain has no Project Agent definition")
    try:
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise RuleError("the Project Agent definition cannot be read") from None
    if not isinstance(doc, dict):
        raise RuleError("the Project Agent definition is not a mapping")
    header = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    return header, doc


def _write(path: Path, header: list[str], doc: dict[str, Any]) -> None:
    # the keys keep their order (a new ``rules`` goes last); the header comments stay
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    text = ("\n".join(header) + "\n" if header else "") + body
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=".project-agent.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)              # mkstemp makes 0600; the file keeps its own mode
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _persons_words(session_id: str) -> tuple[str, str]:
    """(event id, authored text) of this session's latest user message. Raises RuleError."""
    from core import session_inputs
    if not session_id:
        raise RuleError("no session id: run it from the desktop chat")
    event = session_inputs.latest_user_event_id(session_id)
    if not event:
        raise RuleError("this turn was not opened by your own message in the desktop chat "
                        "(a channel message cannot add a rule)")
    text = session_inputs.user_authored_text(event)
    if not text:
        raise RuleError("your message of this turn cannot be read back")
    return event, text


def add_rule(workspace: Path, brain: str, said: str, session_id: str,
             now: Optional[datetime.datetime] = None) -> dict[str, Any]:
    """Add *said* to *brain*'s rules when it is the person's words of this turn.
    ``{"added": bool, "said": ..., "brain": ...}``; ``added`` False when it was already a
    rule (its record is ensured). Raises RuleError with the reason; then nothing is written."""
    from core.project_agent_session import PinError, brain_dir
    from utils.file_lock import md_lock
    said = (said or "").strip()
    if len(_norm(said)) < MIN_CHARS:
        raise RuleError(f"a rule needs at least {MIN_CHARS} characters of your words")
    try:
        project_dir = brain_dir(Path(workspace), brain)
    except PinError as exc:
        raise RuleError(str(exc)) from None
    path = _definition(project_dir)
    event, text = _persons_words(session_id)
    if not whole_sentences(said, text):
        raise RuleError("these words are not a whole sentence of your message of this turn; "
                        "a rule is only what you said, whole sentences copied exactly")
    key = quote_key(said)
    when = (now or datetime.datetime.now()).date().isoformat()
    with md_lock(path):
        header, doc = _read(path)
        rows = doc.get(RULES_KEY) if isinstance(doc.get(RULES_KEY), list) else []
        exists = any(isinstance(r, dict) and isinstance(r.get("said"), str) and quote_key(r["said"]) == key
                     for r in rows)
        record = _record_file(brain)
        if key not in _record_keys(record):
            record.parent.mkdir(parents=True, exist_ok=True)
            with record.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "event": event, "at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat()}) + "\n")
        if exists:
            return {"added": False, "said": said, "brain": brain}
        doc[RULES_KEY] = list(rows) + [{"said": said, "added": when}]
        _write(path, header, doc)
    return {"added": True, "said": said, "brain": brain}


def remove_rule(workspace: Path, brain: str, said: str, session_id: str) -> dict[str, Any]:
    """Remove *brain*'s rule with these words (normalized), in a turn the person opened in
    the desktop chat (a channel or injected turn cannot take a rule away). The
    verification record stays (an audit). The person's message must contain the whole rule.
    Raises RuleError when there is no such rule, no such turn or no such
    quote; then nothing changes."""
    from core.project_agent_session import PinError, brain_dir
    from utils.file_lock import md_lock
    try:
        project_dir = brain_dir(Path(workspace), brain)
    except PinError as exc:
        raise RuleError(str(exc)) from None
    path = _definition(project_dir)
    _event, text = _persons_words(session_id)
    if not said or _norm(said) not in _norm(text):
        # a page read in the person's turn must not take their rule away
        raise RuleError("your message of this turn does not contain this rule; to remove it, "
                        "say the whole rule")
    key = quote_key(said or "")
    with md_lock(path):
        header, doc = _read(path)
        rows = doc.get(RULES_KEY) if isinstance(doc.get(RULES_KEY), list) else []
        keep = [r for r in rows if not (isinstance(r, dict) and isinstance(r.get("said"), str)
                                       and quote_key(r["said"]) == key)]
        if len(keep) == len(rows):
            raise RuleError("this Brain has no rule with these words")
        if keep:
            doc[RULES_KEY] = keep
        else:
            doc.pop(RULES_KEY, None)
        _write(path, header, doc)
    return {"removed": True, "said": said, "brain": brain}


def _brain_of(project_dir: Path) -> str:
    from core.project_agent import GENERAL_BRAIN, _is_general_dir
    return GENERAL_BRAIN if _is_general_dir(Path(project_dir)) else Path(project_dir).name


def rule_rows(project_dir: Path) -> list[dict[str, Any]]:
    """Every rule of the Brain at *project_dir* in the file's order, ``{said, added,
    verified}``. Never raises: what cannot be read is left out."""
    try:
        doc = yaml.safe_load(_definition(project_dir).read_text(encoding="utf-8"))
        rows = _rows(doc)
    except Exception:  # noqa: BLE001 — a reader of the definition never fails on it
        return []
    try:
        keys = _record_keys(_record_file(_brain_of(project_dir)))
    except Exception:  # noqa: BLE001
        keys = set()
    return [{"said": r["said"].strip(), "added": str(r.get("added") or ""),
             "verified": quote_key(r["said"]) in keys} for r in rows]


def verified_rules(project_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(verified, unverified) rules of the Brain at *project_dir*, each ``{said, added}`` in
    the file's order. Never raises. Only the verified ones are ever delivered."""
    rows = rule_rows(project_dir)
    strip = lambda r: {"said": r["said"], "added": r["added"]}  # noqa: E731
    return [strip(r) for r in rows if r["verified"]], [strip(r) for r in rows if not r["verified"]]


def current_rules(path: Path) -> Any:
    """The ``rules`` value of the definition at *path* as it is now, or None (no file, no
    rules, unreadable). Never raises."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return doc.get(RULES_KEY) if isinstance(doc, dict) and doc.get(RULES_KEY) else None
