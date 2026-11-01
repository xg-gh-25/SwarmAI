"""The Project Agent standard: one form every Brain's definition fills (design
docs/design/2026-10-29-project-agent-standard-template.md §3, §5).

``TEMPLATE`` is the one list of the definition's fields: eight sections in a fixed
order, each field with its label, its purpose, who may change it and where it is
stored. Everything else that names fields is generated from it:

- ``SHARED_ORDER`` / ``MODE_ORDER`` / ``IDENTITY_ORDER`` / ``INTAKE_ORDER``: the key
  order a written definition uses (``ordered``);
- ``proposal_keys()``: the format the drafting agent proposes in
  (``project_agent_draft.PROPOSAL_CONTRACT``);
- ``editable(field)``: which stored fields the Brain Hub form may change
  (``project_agent_review.edit``);
- ``core.project_agent_form``: the sections the Brain Hub shows;
- ``standard_md_table()``: the field table of templates/project-agents/STANDARD.md (a
  test compares the file with it).

What a standard is NOT: a validation rule. ``project_agent.validate`` decides what is
wrong; ``required`` says what a finished draft has (P6 reports a skeleton), and
``deviations`` lists where a definition leaves the standard as advisory remarks.

- ``MODES``: the standard modes, for which shape of Brain and their ``when``. How a mode's
  work runs (native or a pipeline profile) is not the Project Agent's: each dispatch picks
  it from the request (design 2026-10-30-run-spec §3).
  A Brain "has a repo" when its ``bindings.yaml`` binds one (``project_agent._bindings``).
- ``standard_modes(has_repo)`` / ``default_mode(has_repo)``: a draft's mode skeleton.
- ``HEADER``: the comment every definition file starts with.
- ``is_skeleton(doc)``: a definition code wrote that no one has drafted yet.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Optional

from core.project_agent import RESERVED_ACTIONS

#: how a field may change. edit: drafted from the Brain's documents, changed on the form;
#: brain: read from the Brain's files, changed there (``removable``: rows may be dropped
#: here); you: only a person sets it; fixed: the product's, the same for every Brain.
KINDS: dict[str, str] = {
    "edit": "Editable", "brain": "From the Brain", "you": "Your choice", "fixed": "Fixed"}


@dataclass(frozen=True)
class Field:
    """One field of the form. ``store``: its path in the file (``identity.summary``,
    ``modes[].when`` for a mode's own field), None when it is read from the Brain or the
    product. ``shape``: text | list | rows | names | choice | view."""
    key: str
    section: str
    label: str
    help: str
    kind: str
    shape: str
    store: Optional[str] = None
    required: bool = False
    removable: bool = False
    example: str = ""
    columns: tuple[str, ...] = ()
    propose: Optional[tuple[str, Any]] = None     # (proposal key, contract text)


#: (id, title, purpose): the order a person reads the form in — what it is and has, its
#: limits, how work comes in and when you are brought in, then the reference.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("identity", "Identity",
     "Who this agent is and what it is attached to. Routing reads the summary and scope."),
    ("capabilities", "Capabilities",
     "What the Brain gives it to work with. Defined in the Brain; the agent only uses it."),
    ("modes", "Modes",
     "The kinds of work it does. The agent picks the mode whose \"used when\" matches the request."),
    ("guardrails", "Guardrails",
     "What it never does on its own, and what checks it. Every mode inherits these and can only add to them."),
    ("runs", "Runs in",
     "Where its work may run. A mode that needs this machine stays here even when the cloud is allowed."),
    ("intake", "Intake", "How a request reaches it: a new request is compared with these."),
    ("judgment", "Judgment", "When it decides on its own and when it brings you in."),
    ("profiles", "Pipeline profiles",
     "For reference: what each profile runs. How a task runs (native, or one of these) is picked "
     "for each request when it is dispatched, not set here."),
)

TEMPLATE: tuple[Field, ...] = (
    # 1 Identity
    Field("name", "identity", "Name", "The agent is named after its Brain.", "brain", "view"),
    Field("brain", "identity", "Brain", "The Brain (DDD) it works from: its knowledge, skills and gates.",
          "brain", "view"),
    Field("repos", "identity", "Repositories",
          "The code it may change: where it is checked out here, and where it lives remotely. "
          "From bindings.yaml.", "brain", "view", columns=("name", "local", "remote")),
    Field("summary", "identity", "Summary",
          "One sentence: what the work in this Brain usually is. Shown wherever the agent is listed.",
          "edit", "text", "identity.summary", required=True,
          example="Fix and extend the billing service and its jobs",
          propose=("identity", {"summary": "str", "in_scope": ["str"], "out_of_scope": ["str"],
                                "source": "str"})),
    Field("in_scope", "identity", "Takes on", "The kinds of work it accepts.", "edit", "list",
          "identity.in_scope", example="bugs and features in the API"),
    Field("out_of_scope", "identity", "Refuses", "Work that must go elsewhere. Routing excludes it.",
          "edit", "list", "identity.out_of_scope", example="another team's services"),
    # 2 Capabilities
    Field("skills", "capabilities", "Skills",
          "The Brain's own skills (4-capabilities/). Which mode may use each is set in Modes.",
          "brain", "view", columns=("name", "description", "modes", "hive")),
    Field("tools", "capabilities", "Tools and MCP", "Scripts and MCP servers the Brain declares (aim.json).",
          "brain", "view", columns=("name", "kind", "hive")),
    Field("jobs", "capabilities", "Jobs", "Scheduled work this Brain owns.", "brain", "view"),
    # 3 Modes (a mode's own fields)
    Field("when", "modes", "Used when", "The kind of request this mode handles. Modes must not overlap.",
          "edit", "text", "modes[].when", required=True,
          propose=("when", "the kind of intent this mode handles (a new `when` needs a source)")),
    Field("mode_repos", "modes", "Repositories", "The repos this mode works in.", "brain", "names",
          "modes[].repos", propose=("repos", ["names from facts.repos (new modes only; code-change has them all)"])),
    Field("setup", "modes", "Setup",
          "Installs run before the checks, copied from the file the repo declares them in.",
          "brain", "rows", "modes[].setup", removable=True, columns=("command", "cwd", "source"),
          propose=("setup", ["a facts.commands id, or {command, source, repo, cwd}"])),
    Field("validations", "modes", "Checks a run must pass",
          "Commands from the repo's validations.json or CI, copied verbatim.",
          "brain", "rows", "modes[].validations", removable=True, columns=("id", "command", "cwd", "source"),
          propose=("validations", ["a facts.commands id, or {id, command, source, repo, cwd}"])),
    Field("mode_skills", "modes", "Skills", "Skills of this Brain the mode may use.", "edit", "names",
          "modes[].skills",
          propose=("skills", ["names from facts.skills; a skill no mode lists goes to the default mode"])),
    Field("delivery", "modes", "Delivers", "How code comes back: bundle = you review the commits, then "
          "they land on main.", "edit", "choice", "modes[].delivery"),
    Field("default_mode", "modes", "Default mode", "The mode used when a request names none.", "edit",
          "choice", "default_mode", propose=("default_mode", "a mode id")),
    # 4 Guardrails
    Field("reserved_for_human", "guardrails", "Always ask me before",
          "Each time, it stops and waits for your approval.", "you", "names", "reserved_for_human"),
    Field("gates", "guardrails", "Brain gates",
          "Checks from this Brain's 3-gates/, run on every command. Untick one to stop enforcing it.",
          "brain", "names", "gates.enforce", removable=True,
          propose=("gates", ["names from facts.gates to enforce; omitted = all of them"])),
    Field("product_guards", "guardrails", "SwarmAI guards", "The product's own guards. Every session runs them.",
          "fixed", "view"),
    Field("landing", "guardrails", "Code lands", "Who approves code before it reaches main.", "fixed", "view"),
    # 5 Runs in
    Field("placements", "runs", "Where it may run", "Hive lets it run unattended on your cloud SwarmAI.",
          "you", "names", "placements"),
    # 6 Intake
    Field("handles", "intake", "Typical requests", "3-5 requests it handles, one sentence each.",
          "edit", "list", "intake.handles", required=True, example="Fix a reproducible bug in the API",
          propose=("handles", [{"text": "a typical intent, one sentence", "source": "str"}])),
    Field("aliases", "intake", "Other names",
          "Names people use for this Brain. Each routes a request here; the ones you keep move into "
          "aim.json when you confirm.", "you", "rows", "intake.proposed_aliases", removable=True,
          columns=("name", "source"),
          propose=("aliases", [{"name": "a module or product name people use for this Brain",
                                "source": "str"}])),
    # 7 Judgment
    Field("confidence", "judgment", "When unsure", "What it does before acting on a guess.", "edit", "text",
          "confidence", example="Research first; ask before changing a public API",
          propose=("confidence", {"text": "str", "source": "str"})),
    Field("watches", "judgment", "Review focus",
          "What every review of work here checks, each with the document it comes from.", "edit", "rows",
          "watches", columns=("area", "why", "source"),
          propose=("watches", [{"area": "str", "why": "str", "source": "str"}])),
    # 8 Pipeline profiles
    Field("profiles", "profiles", "Profiles",
          "The profiles a pipeline run can use. A dispatch picks native or one of them per request.",
          "fixed", "view",
          columns=("name", "for", "stages")),
)

#: the definition's own bookkeeping, written by code before the template's fields
_META = ("schema", "status", "reviewed_at", "reviewed_by")


def _order_from_template(prefix: str) -> tuple[str, ...]:
    out: list[str] = []
    for f in TEMPLATE:
        if f.store and f.store.startswith(prefix):
            k = f.store[len(prefix):].split(".")[0]
            if k and k not in out:
                out.append(k)
    return tuple(out)


def _shared_order() -> tuple[str, ...]:
    out: list[str] = list(_META)
    for f in TEMPLATE:
        if not f.store:
            continue
        top = f.store.split(".")[0].split("[")[0]
        if top not in out:
            out.append(top)
    out.append("sources")
    return tuple(out)


SHARED_ORDER: tuple[str, ...] = _shared_order()
IDENTITY_ORDER: tuple[str, ...] = _order_from_template("identity.") + ("source",)
INTAKE_ORDER: tuple[str, ...] = _order_from_template("intake.")
MODE_ORDER: tuple[str, ...] = ("id",) + _order_from_template("modes[].") + (
    "placements", "gates", "reserved_for_human", "pr_remote", "source")


def field(key: str) -> Field:
    return next(f for f in TEMPLATE if f.key == key)


def editable(f: Field) -> bool:
    """A stored field the form may change: kind edit or you, or rows it may drop."""
    return bool(f.store) and (f.kind in ("edit", "you") or f.removable)


def proposal_keys() -> dict[str, Any]:
    """The top-level keys the drafting agent may propose, from the template, in order."""
    return {f.propose[0]: f.propose[1] for f in TEMPLATE
            if f.propose and not (f.store or "").startswith("modes[].")}


def mode_proposal_keys() -> dict[str, Any]:
    """The keys of one proposed mode, from the template, in order."""
    return {f.propose[0]: f.propose[1] for f in TEMPLATE
            if f.propose and (f.store or "").startswith("modes[].")}


def standard_md_table() -> str:
    """The field table of STANDARD.md, generated from the template."""
    rows = ["| Section | Field | What it is for | Who changes it | Stored as |", "|---|---|---|---|---|"]
    titles = {s: t for s, t, _ in SECTIONS}
    for f in TEMPLATE:
        rows.append(f"| {titles[f.section]} | {f.label}{' (required)' if f.required else ''} | "
                    f"{f.help.replace('|', '/')} | {KINDS[f.kind]}{' (rows may be dropped)' if f.removable and f.kind == 'brain' else ''} | "
                    f"{'`' + f.store + '`' if f.store else 'read from the Brain' if f.kind == 'brain' else 'the product'} |")
    return "\n".join(rows)


def is_skeleton(doc: dict[str, Any]) -> bool:
    """A definition code wrote and no one has drafted yet: it has neither a summary nor
    typical requests (design §6.2). P6 reports it, so a Brain is not ready on it."""
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    has_summary = isinstance(ident.get("summary"), str) and bool(ident["summary"].strip())
    handles = intake.get("handles")
    has_handles = isinstance(handles, list) and any(isinstance(h, str) and h.strip() for h in handles)
    return not has_summary and not has_handles


#: the actions a draft reserves for a person: all the product knows
DEFAULT_RESERVED: tuple[str, ...] = tuple(sorted(RESERVED_ACTIONS))

#: the standard modes. ``for``: every Brain, a Brain with a repo, or one without. The
#: mode inside a Brain is chosen by matching these ``when`` texts (core/task_spec), so the
#: modes of one shape never overlap: without a repo, research answers in the conversation
#: and write delivers a document; with a repo there is no write and a design is research.
MODES: dict[str, dict[str, Any]] = {
    "code-change": {"for": "repo", "delivery": "bundle",
                    "when": "a change to the code of a bound repo"},
    "research": {"for": "every",
                 "when": {"repo": "a question, an investigation or a design, with no change to code",
                          "no-repo": "a question or an investigation, answered in the conversation"}},
    "write": {"for": "no-repo",
              "when": "a document or a report as the deliverable"},
}

HEADER = ("# Project Agent definition: how this Brain's work is taken in, done and delivered.\n"
          "# The agent keeps it current and confirms it; the person confirms what gives the agent more.\n"
          "# Fields and defaults: the Project Agent standard, templates/project-agents/STANDARD.md.\n")


def _applies(spec: dict[str, Any], has_repo: bool) -> bool:
    return spec["for"] == "every" or (spec["for"] == "repo") == has_repo


def standard_when(mode_id: str, has_repo: bool) -> str:
    when = MODES[mode_id]["when"]
    return when if isinstance(when, str) else when["repo" if has_repo else "no-repo"]


def standard_modes(has_repo: bool) -> list[dict[str, Any]]:
    """The standard modes of a Brain of this shape, in order (code-change first when
    there is a repo: it is the default mode then)."""
    out = []
    for mid, spec in MODES.items():
        if _applies(spec, has_repo):
            m = {"id": mid, "when": standard_when(mid, has_repo)}
            if "delivery" in spec:
                m["delivery"] = spec["delivery"]
            out.append(m)
    return out


def default_mode(has_repo: bool) -> str:
    return "code-change" if has_repo else "research"


def _order(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out = {k: d[k] for k in keys if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


#: keys the standard no longer has; every write drops them (a file written before they
#: were removed would otherwise carry them through each edit and confirm)
RETIRED_KEYS = ("landing", "drafted_at")


def ordered(doc: dict[str, Any]) -> dict[str, Any]:
    """A copy of *doc* with its keys (and those of identity, intake and each mode) in the
    standard order, retired keys dropped; keys the standard does not name keep their
    order, after the rest."""
    d = {k: v for k, v in copy.deepcopy(doc).items() if k not in RETIRED_KEYS}
    d = _order(d, SHARED_ORDER)
    if isinstance(d.get("identity"), dict):
        d["identity"] = _order(d["identity"], IDENTITY_ORDER)
    if isinstance(d.get("intake"), dict):
        d["intake"] = _order(d["intake"], INTAKE_ORDER)
    if isinstance(d.get("modes"), list):
        d["modes"] = [_order(m, MODE_ORDER) if isinstance(m, dict) else m for m in d["modes"]]
    return d


def deviations(doc: dict[str, Any], has_repo: bool) -> list[str]:
    """Where a (normalized, schema 2) definition leaves the standard: a missing summary or
    handles (the model proposed none, or a person removed them), fewer reserved actions
    than the default, a repo without a code-change mode, a mode that still carries
    ``profile`` / ``profile_rules`` (no longer read: a dispatch picks how work runs).
    Advisory, never a problem."""
    out: list[str] = []
    ident = doc.get("identity") if isinstance(doc.get("identity"), dict) else {}
    if not (isinstance(ident.get("summary"), str) and ident["summary"].strip()):
        out.append("standard: identity.summary is missing (routing and the review card show it)")
    intake = doc.get("intake") if isinstance(doc.get("intake"), dict) else {}
    handles = intake.get("handles")
    if not (isinstance(handles, list) and any(isinstance(h, str) and h.strip() for h in handles)):
        out.append("standard: intake.handles is missing (routing compares a task with them)")
    reserved = doc.get("reserved_for_human")
    have = set(reserved) if isinstance(reserved, list) else set()
    gone = [a for a in DEFAULT_RESERVED if a not in have]
    if gone:
        out.append(f"standard: no longer reserved for a person: {', '.join(gone)}")
    modes = doc.get("modes") if isinstance(doc.get("modes"), list) else []
    if has_repo and not any(isinstance(m, dict) and m.get("id") == "code-change" for m in modes):
        out.append("standard: this Brain has a bound repo but no code-change mode")
    legacy = [str(m.get("id")) for m in modes if isinstance(m, dict) and ("profile" in m or "profile_rules" in m)]
    if legacy:
        out.append("standard: modes " + ", ".join(legacy) + " still name a pipeline profile; it no longer "
                   "has an effect (each dispatch picks how its work runs) and can be removed")
    return out
