"""What a session's wait is about, decided by the gate that made it wait.

Design docs/design/2026-10-31-hive-approvals-person-and-host.md §2.2 (implementation
docs/design/2026-11-01-hive-approvals-impl.md). Every wait a gate posts to the prompt
board carries one ``approval_class``, computed only from facts the gate already has (the
kind of wait, the rule that tripped). No model judges it (SOUL P5).

Who may answer follows the responsibility chain: the agent that dispatched a run to a
Hive answers the waits whose effect stays inside the work it manages for that run
(``HOST_CLASSES``); anything that reaches the person's data or the world outside goes to
the person, because the dispatching agent could not do it without asking either. The
Hive's bridge refuses a dispatching agent's answer to any other class, so this list is
the boundary, not advice.

Public: ``HOST_CLASSES``, ``CLASSES``, ``for_ask``, ``for_rule``, ``for_rules``,
``for_external_tool``, ``for_destructive_data``, ``clean``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

QUESTION = "question"
DECISION = "decision"
HIVE_LOCAL = "hive_local"
RUN_AUTHORIZATION = "run_authorization"
REMOTE_CODE = "remote_code"
OFF_MACHINE = "off_machine"
IRREVERSIBLE_EXTERNAL = "irreversible_external"
DESTRUCTIVE_DATA = "destructive_data"
UNKNOWN = "unknown"

#: the classes the agent that dispatched the run may answer (its own decision, recorded
#: as the host's, never as the person's)
HOST_CLASSES = frozenset({QUESTION, DECISION, HIVE_LOCAL, RUN_AUTHORIZATION})
CLASSES = HOST_CLASSES | {REMOTE_CODE, OFF_MACHINE, IRREVERSIBLE_EXTERNAL, DESTRUCTIVE_DATA, UNKNOWN}

#: the product's own dangerous patterns (core/security_hooks DEFAULT_DANGEROUS_PATTERNS)
#: and what each touches. A pattern the person added is not here: ``unknown``.
_PATTERN_CLASS = {
    # sudo can run anything: the gate gives it a host class only for a program it
    # allows (security_hooks._host_answerable_shape), so ``sudo …`` is the person's
    "sudo *": HIVE_LOCAL,
    "chmod 777 *": HIVE_LOCAL,
    "chmod -R 777 *": HIVE_LOCAL,
    "chown -R * /": HIVE_LOCAL,
    "kill -9 *": HIVE_LOCAL,
    "mkfs.*": HIVE_LOCAL,
    "dd if=*": HIVE_LOCAL,
    "> /dev/sda*": HIVE_LOCAL,
    "> /dev/hda*": HIVE_LOCAL,
    "> /dev/nvme*": HIVE_LOCAL,
    "> /dev/vda*": HIVE_LOCAL,
    "> /etc/*": HIVE_LOCAL,
    ":()*{*:*|*:*&*}*;*:*": HIVE_LOCAL,
    # runs code fetched from outside: it can reach the Brain copy and the bridge's keys
    "curl *|bash*": REMOTE_CODE,
    "curl *|sh*": REMOTE_CODE,
    "wget *|bash*": REMOTE_CODE,
    "wget *|sh*": REMOTE_CODE,
}

_RULE_CLASS = {
    "rm_unresolved_target": HIVE_LOCAL,
    "discard_foreign_uncommitted": HIVE_LOCAL,
    "discard_unchecked": HIVE_LOCAL,
    "v1_authorization": RUN_AUTHORIZATION,
    "reserved_action": OFF_MACHINE,
    "irreversible_external": IRREVERSIBLE_EXTERNAL,
    "irreplaceable_store": DESTRUCTIVE_DATA,
}


def for_ask() -> str:
    """An AskUserQuestion: a question about the task itself."""
    return QUESTION


def for_rule(rule: Any) -> str:
    """The class of a dangerous-command gate's rule id (``pattern:<glob>`` or a named
    rule); anything not listed is ``unknown``."""
    rule = str(rule or "")
    if rule.startswith("pattern:"):
        return _PATTERN_CLASS.get(rule[len("pattern:"):], UNKNOWN)
    return _RULE_CLASS.get(rule, UNKNOWN)


#: the person's classes, most serious first: a command that trips several rules gets
#: the first of these it has
_PERSON_ORDER = (DESTRUCTIVE_DATA, IRREVERSIBLE_EXTERNAL, OFF_MACHINE, REMOTE_CODE, UNKNOWN)

#: code fetched from outside piped into a shell, anywhere in the command (the gate's own
#: patterns match it only at the start)
_REMOTE_CODE_RE = re.compile(r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b")


def for_rules(rules: Iterable[Any], command: str = "") -> str:
    """The class of a dangerous command from EVERY rule it trips (not the first one):
    the person's when any of them is the person's (``sudo git push --force`` is the
    person's, though ``sudo *`` alone is the Hive's), else the first rule's class."""
    classes = [for_rule(r) for r in rules]
    if _REMOTE_CODE_RE.search(str(command or "")):
        classes.append(REMOTE_CODE)
    for cls in _PERSON_ORDER:
        if cls in classes:
            return cls
    return classes[0] if classes else UNKNOWN


def for_external_tool() -> str:
    """The external-approval gate: a tool call with off-machine side effects."""
    return OFF_MACHINE


def for_destructive_data() -> str:
    """data_safety: destroying an irreplaceable store (STEERING #20)."""
    return DESTRUCTIVE_DATA


def clean(value: Any) -> Optional[str]:
    """A class read from another machine's record: a known one, ``unknown`` for any
    other string, None when there is none (an older Hive that writes no class)."""
    if value is None:
        return None
    return value if isinstance(value, str) and value in CLASSES else UNKNOWN
