"""Claude model registry — the SINGLE authority for model names and Bedrock IDs.

This module is the one place that knows which Claude models this deployment can
use and how each short name maps to a Bedrock inference-profile ID. Every other
module DERIVES from it instead of holding its own literal copy.

Key public symbols:
  - ``MODEL_REGISTRY``     — ordered short-name -> Bedrock-ID mapping (flagship FIRST)
  - ``MODEL_NAMES``        — the short names, flagship first (derived)
  - ``FLAGSHIP_MODEL``     — the current default/flagship short name (derived)
  - ``DEFAULT_CONTEXT_WINDOW`` / ``LARGE_CONTEXT_WINDOW`` — the two window tiers
  - ``resolve_bedrock_id`` — short name -> Bedrock ID, or ``None`` if unknown
  - ``is_large_context_model`` — family predicate for 1M-context capability
  - ``geo_for_region`` / ``regional_id`` — the profile geography a region calls, and an
    id rewritten to it (``us.`` ids are valid only from US regions)
  - ``is_full_bedrock_id`` — an ARN / foundation id / profile id of any geography

⚠️ STDLIB-ONLY — DO NOT ADD A THIRD-PARTY IMPORT TO THIS MODULE.

``hive/release.sh`` generates its Hive seed config by importing this module with
the SYSTEM ``python3`` (no venv is activated during packaging). A third-party
import here (``pydantic``, ``boto3``, ...) makes that generation crash, which is
exactly why the registry could NOT live in ``backend/config.py`` — that module
imports ``pydantic_settings``, so ``/usr/bin/python3 -c "import config"`` raises
ModuleNotFoundError. Verify any change with the SYSTEM interpreter, not the venv
or a mise/pyenv shim that happens to have extra packages installed:

    cd backend && /usr/bin/python3 -c "import model_registry"

⚠️ ORDER IS LOAD-BEARING — the flagship MUST be first.

``routers/settings.py`` auto-resets ``default_model`` to ``available_models[0]``
when the available list changes. If a newer model were appended at the END, that
auto-reset would silently select the OLDEST model as the default.

Why this module exists: the same short-name -> Bedrock-ID table was independently
hardcoded in five places (``config.py``, ``app_config_manager.DEFAULT_CONFIG``,
``eval_runner._KNOWN_GOOD``, ``prompt_builder._MODEL_CONTEXT_WINDOWS``, and the
``hive/release.sh`` seed heredoc). They drifted: a new flagship was configured in
the live workspace while every hardcoded copy stopped two generations back, so a
missing/corrupt config silently downgraded the model. One authority + derivation
makes the next model release a one-line edit here.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# The registry — short name -> Bedrock inference-profile ID
# ---------------------------------------------------------------------------
#
# FLAGSHIP FIRST (see the order warning in the module docstring).
#
# ⚠️ Flagship is DELIBERATELY CHOSEN, not "newest-version-wins".
#
# - claude-opus-5-5 is the flagship/default — promoted 2026-10-17 by an explicit
#   human decision. It needs Claude Code CLI >= 2.1.280 (an older bundled CLI
#   rejects it with a 400 "does not support this model"). Reordering changes
#   the DEFAULT only: an existing config.json keeps its stored default_model
#   (AppConfigManager merges stored values over DEFAULT_CONFIG).
# - claude-opus-4-8 sits second: the previous flagship and the ROLLBACK target.
#   To roll back, move it to index 0 (and update the two lockstep literals
#   listed below) — nothing has to be deleted. A rollback only changes the
#   DEFAULT too: any config.json written while 5-5 was the default (a fresh
#   install, or AppConfigManager re-seeding a missing/corrupt file, or a Hive
#   first boot) has default_model=claude-opus-5-5 persisted and keeps it until
#   that config is edited — there is no migration that follows the flagship.
# - claude-opus-5 is present and selectable but INTENTIONALLY NOT first: tried
#   as flagship, it ran far slower per turn, did not follow sedimented
#   cognition, and did not converge on adversarial loops, so it was rolled back.
#   Do NOT re-sort this registry by version number; that would silently
#   re-promote it via settings.py's available_models[0] auto-reset.
#
# What the promotion was NOT based on: an OS-Eval A/B. No current evaluator can
# discriminate production models — ``eval_runner.eval_llm_judge`` asks a pinned
# judge model whether the RULES would produce compliant behaviour (the
# production model never runs), and ``trajectory_capture`` runs a real agent
# but ``scripts/scenario_runner.py`` hard-codes ``--model sonnet``. Until the
# production model is wired into that runner, a model A/B is unavailable, so a
# promotion here is a human judgement with a one-line code rollback, not a
# measured one.
#
# Bedrock IDs are NOT mechanically derivable from the short name — note that
# 4-6 carries a "-v1" suffix while 4-8 and 5 do not. Never synthesize an ID as
# f"us.anthropic.{short_name}"; look it up here.
MODEL_REGISTRY: dict[str, str] = {
    "claude-opus-5-5": "us.anthropic.claude-opus-5-5",
    "claude-opus-4-8": "us.anthropic.claude-opus-4-8",
    "claude-opus-5": "us.anthropic.claude-opus-5",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
}

# Derived — never hand-maintain a parallel list.
MODEL_NAMES: list[str] = list(MODEL_REGISTRY)

# The flagship / default model. Derived from position: promoting a new flagship
# means moving it to the top of MODEL_REGISTRY, PLUS updating the two lockstep
# literals that cannot import this module — ``jobs/model_resolve.py``
# (``_flagship_fallback`` last resort) and ``desktop/src/pages/OnboardingPage.tsx``
# (``FALLBACK_FLAGSHIP_MODEL``). Both are bound by tests that fail on drift.
FLAGSHIP_MODEL: str = MODEL_NAMES[0]

# ---------------------------------------------------------------------------
# Context-window tiers
# ---------------------------------------------------------------------------
#
# LARGE == the 1M window that Claude opus/sonnet gen-4+ supports on Bedrock.
LARGE_CONTEXT_WINDOW: int = 1_000_000

# DEFAULT == what an UNKNOWN or non-large-context model resolves to.
#
# It applies ONLY to a RECOGNIZED-but-not-large Claude (haiku, gen-3). An
# unresolved or unrecognized id (a custom-model ARN, a non-Claude id) stands in
# for the flagship instead — see is_large_context_model. That distinction is not
# cosmetic: prompt_builder previously returned the LARGE window for anything
# unrecognized, so blanket-lowering it to this value would have halved the
# context budget (100K -> 50K), shrunk the resume budget (150K -> 60K), and made
# a custom-ARN deployment report `warn` at 150K tokens. Only the haiku/gen-3
# case is intentionally lowered here.
#
# ⚠️ FLOOR: this value must stay >= context_directory_loader.THRESHOLD_USE_L1
# (64_000). A window below that threshold routes the session onto the L0
# compact-cache path, so a too-small default would silently send those models
# down a different prompt-assembly branch. 200_000 also matches what the sibling
# modules (compaction_guard, retry_manager, context_injector) already used as
# their own conservative default, so the duplicate literals collapse onto it
# without changing their values.
DEFAULT_CONTEXT_WINDOW: int = 200_000

# The self-eval judge model.
#
# Deliberately pinned to a CHEAPER tier than production: the judge must not
# drift in lockstep with the agent it scores, or a regression in both cancels
# out and the eval reports health it has not verified. It is a REGISTRY MEMBER
# so it always resolves — an unresolvable value silently swapped the judge for
# weeks. This is the ONE definition; eval_runner, DEFAULT_CONFIG and the Hive
# seed all read it rather than each naming their own default.
DEFAULT_JUDGE_MODEL: str = "claude-sonnet-4-6"

# Families whose gen-4-and-later models support the 1M context window.
_LARGE_CONTEXT_FAMILIES: tuple[str, ...] = ("claude-opus-", "claude-sonnet-")

# Bedrock ID prefix to strip before a short-name lookup: "anthropic." optionally led by
# a geography ("us.", "global.", "eu.", "apac.", ...). A deployment's bedrock_model_map
# may name any geography, and jobs.bedrock decides temperature and tool choice from
# the full ID, so every geography has to reduce to the same short name.
_ID_PREFIX = re.compile(r"^(?:[a-z]{2,8}(?:-[a-z]+)?\.)?anthropic\.")

# A cross-region inference-profile id: "<geo>.anthropic.<model>". The geo decides which
# source regions may call it ("us." only from US regions), so it follows the main region
# (design docs/design/2026-11-01-bedrock-id-follows-region.md).
_GEO_ID = re.compile(r"^([a-z]{2,8}(?:-[a-z]+)?)\.(anthropic\..+)$")
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+$")


def geo_for_region(region: str | None) -> str | None:
    """The inference-profile geography a call from *region* uses, or None for no region.

    us-gov-* -> "us-gov", us-* -> "us", eu-* -> "eu", ap-* -> "apac", any other valid
    region -> "global". An empty or malformed region gives None (callers then leave the
    id as it is rather than guess).
    """
    if not region or not _REGION.match(region):
        return None
    if region.startswith("us-gov-"):
        return "us-gov"
    for prefix, geo in (("us-", "us"), ("eu-", "eu"), ("ap-", "apac")):
        if region.startswith(prefix):
            return geo
    return "global"


def regional_id(model_id: str | None, region: str | None) -> str | None:
    """*model_id* with its geography replaced by the one *region* calls.

    Only a geography-scoped profile id ("us.anthropic.x", "eu.anthropic.x", ...) changes;
    "global.anthropic.x" (callable from most regions), a foundation id ("anthropic.x"), an
    ARN, a short name and anything else come back unchanged, as does every id when the
    region gives no geography. Suffixes ("-v1", "[1m]", ":0") are kept.
    """
    if not model_id:
        return model_id
    geo = geo_for_region(region)
    m = _GEO_ID.match(model_id)
    if geo is None or m is None or m.group(1) == "global":
        return model_id
    return f"{geo}.{m.group(2)}"


def is_full_bedrock_id(model: str | None) -> bool:
    """True for an id Bedrock takes as it is: an ARN, a foundation id, or a profile id of
    any geography. A short name ("claude-opus-5") is not one."""
    return bool(model) and (model.startswith("arn:") or bool(_ID_PREFIX.match(model)))


def normalize_short_name(model: str | None) -> str:
    """Strip Bedrock prefixes/suffixes to recover the bare short name.

    Handles ``us.anthropic.claude-opus-4-6-v1`` -> ``claude-opus-4-6`` and the
    ``[1m]`` suffix the CLI uses as a context-window signal.

    Args:
        model: A short name, a full Bedrock ID, or ``None``.

    Returns:
        The bare short name, or ``""`` for falsy input.
    """
    if not model:
        return ""
    base = _ID_PREFIX.sub("", model, count=1)
    # Literal suffixes only — never str.rstrip(":0"), which is a character-class
    # strip that would eat a trailing "0" of a date-suffixed id
    # (claude-sonnet-4-20250510 -> ...2025051) or mangle "-10" -> "-1".
    if base.endswith("[1m]"):
        base = base[:-4]
    if base.endswith(":0"):
        base = base[:-2]
    if base.endswith("-v1"):
        base = base[:-3]
    return base


def resolve_bedrock_id(model: str | None) -> str | None:
    """Resolve a short name (or full ID) to its Bedrock inference-profile ID.

    Args:
        model: Short name, full Bedrock ID, or ``None``.

    Returns:
        The Bedrock ID, or ``None`` when the model is not in the registry.
        Returning ``None`` (rather than a guess or a silent substitution) lets
        each caller decide its own fail-loud behavior.
    """
    base = normalize_short_name(model)
    if not base:
        return None
    return MODEL_REGISTRY.get(base)


def _is_recognized_claude(base: str) -> bool:
    """True when ``base`` names a Claude model whose size we can judge.

    Anything else — a custom-model ARN, a provisioned-throughput ARN, a
    non-Claude id — is UNRECOGNIZED, which is a different state from
    "recognized and small" and must not be conflated with it.
    """
    return any(
        base.startswith(f) for f in _LARGE_CONTEXT_FAMILIES + ("claude-haiku-", "claude-")
    )


def is_large_context_model(model: str | None) -> bool:
    """Return True when ``model`` is KNOWN to support the 1M context window.

    A FAMILY predicate, not a membership test: 1M-capable == Claude
    ``opus``/``sonnet`` family at generation >= 4. Deriving from the family
    keeps a newly released (or Bedrock-auto-discovered) model correct with no
    code edit — a hardcoded allowlist would silently run it below its real
    window, the silent capability-degradation class of COE 039c4f32.

    Gen-3 Claude, haiku, non-Claude ids, custom ARNs and falsy input are all
    False: this answers "is this model PROVEN 1M-capable", which is the question
    ``resolve_model`` needs before it appends the CLI's ``[1m]`` flag. Claiming
    1M for an unknown id would ask the CLI to open a window the model may not
    have.

    ⚠️ Do NOT reuse this for "how big a window should I assume" — that is
    :func:`context_window_for`, which treats unresolved/unrecognized as
    "stand in for the default model" rather than as evidence of a small window.
    The two questions have different right answers for an unknown id, and
    conflating them broke a real deployment in both directions: a strict
    predicate halved a custom-ARN session's context budget, while a lenient one
    made the CLI claim 1M for ``gpt-4o``.

    Args:
        model: Short name, full Bedrock ID, or ``None``.

    Returns:
        True only if the model is a known large-context Claude.
    """
    base = normalize_short_name(model)
    if not base:
        return False
    for family in _LARGE_CONTEXT_FAMILIES:
        if base.startswith(family):
            # Remainder looks like "5", "4-8", "4-20250514" — take the leading int.
            lead = base[len(family):].split("-", 1)[0]
            try:
                return int(lead) >= 4
            except ValueError:
                return False
    return False


# A trailing part this long is a yyyymmdd DATE SNAPSHOT, not a minor version.
# Length-based because ``int()`` parses a date perfectly well: without this
# guard ``claude-opus-4-20250514`` yields minor=20250514, which then outranks
# every real 4.x on a version comparison.
_DATE_SUFFIX_MIN_LEN = 6


def _model_version(base: str, family: str) -> tuple[int, int] | None:
    """Parse a family-prefixed short name into a comparable ``(major, minor)``.

    ``claude-opus-4-8`` -> ``(4, 8)``; ``claude-opus-5`` -> ``(5, 0)``;
    ``claude-opus-4-20250514`` -> ``(4, 0)`` (a date snapshot has no minor).
    Returns ``None`` when ``base`` is not in ``family`` or is unparseable.
    """
    if not base.startswith(family):
        return None
    parts = base[len(family):].split("-")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return None
    minor = 0
    if len(parts) > 1 and len(parts[1]) < _DATE_SUFFIX_MIN_LEN:
        try:
            minor = int(parts[1])
        except ValueError:
            minor = 0
    return (major, minor)


def model_version(model: str | None) -> tuple[str, int, int] | None:
    """Return ``(family, major, minor)`` for any known Claude family member.

    Used to rank models across families — an opus-only ranking spuriously fails
    the moment a sonnet becomes the flagship.
    """
    base = normalize_short_name(model)
    if not base:
        return None
    for family in _LARGE_CONTEXT_FAMILIES + ("claude-haiku-",):
        version = _model_version(base, family)
        if version is not None:
            return (family, version[0], version[1])
    return None


def _opus_version(base: str) -> tuple[int, int] | None:
    """Parse an OPUS short name into a comparable ``(major, minor)`` tuple.

    Thin wrapper over :func:`_model_version` kept for the temperature rule,
    which is opus-specific.
    """
    return _model_version(base, "claude-opus-")


# Opus 4.7 and later reject ``temperature != 1`` when adaptive thinking is on.
_MIN_OPUS_REJECTING_TEMPERATURE: tuple[int, int] = (4, 7)


def supports_custom_temperature(model: str | None) -> bool:
    """Return True when ``model`` accepts a ``temperature`` other than 1.

    A FAMILY comparison, not a hardcoded allowlist. The allowlist form
    (``{"claude-opus-4-7", "claude-opus-4-8"}``) silently mis-answered for every
    NEWER opus: ``claude-opus-5`` was absent from the set, so it read as
    "supports temperature" and would have had a temperature sent that it
    rejects. Comparing versions keeps a future opus correct with no code edit.

    Non-opus models (sonnet, haiku, anything unrecognized) are treated as
    supporting a custom temperature — the historical default.

    Args:
        model: Short name, full Bedrock ID, or ``None``.

    Returns:
        True if a custom temperature may be sent.
    """
    version = _opus_version(normalize_short_name(model))
    if version is None:
        return True
    return version < _MIN_OPUS_REJECTING_TEMPERATURE


# Opus 5 and later reject thinking.type "disabled" and "enabled" (manual budget):
# both return a 400 invalid_request_error. Adaptive is the only accepted on-mode.
# (Threshold is (5, 0) not (5, 5): opus-5 is also adaptive-first, and silently
# upgrading a disabled/enabled request to adaptive is harmless for it — the guard
# that consumes this just avoids sending a mode the model will 400 on.)
_MIN_OPUS_REJECTING_DISABLED_THINKING: tuple[int, int] = (5, 0)


def supports_disabled_thinking(model: str | None) -> bool:
    """Return True when ``model`` accepts ``thinking.type`` "disabled"/"enabled".

    A FAMILY comparison, not a hardcoded allowlist — the exact shape of
    :func:`supports_custom_temperature`, and for the same reason: an allowlist
    (``{"claude-opus-5-5"}``) would silently mis-answer the NEXT opus, reading it
    as "supports disabled" and sending a mode it 400s on. Comparing versions
    keeps a future opus correct with no code edit.

    Non-opus models (sonnet, haiku, anything unrecognized) and falsy input are
    treated as supporting a disabled thinking mode — the historical default — so
    this never down-grades a model that genuinely accepts ``disabled``.

    The single family rule behind :func:`supported_thinking_modes` /
    :func:`effective_thinking_mode`, which ``prompt_builder.build_options`` (the
    wire) and ``/api/settings`` (the Settings UI) both use, so a configured
    ``disabled``/``enabled`` mode is run as ``adaptive`` on a model that rejects
    it rather than failing on the wire.

    Args:
        model: Short name, full Bedrock ID, or ``None``.

    Returns:
        True if a disabled/enabled thinking mode may be sent.
    """
    version = _opus_version(normalize_short_name(model))
    if version is None:
        return True
    return version < _MIN_OPUS_REJECTING_DISABLED_THINKING


# Models that reject a forced tool choice (Converse toolChoice "tool" / "any": a 400
# "tool_choice: type "tool" and "any" are not supported for this model"). MEASURED on
# Bedrock, 2026-10-28: claude-opus-5-5 rejects it; claude-opus-5, claude-opus-4-8,
# claude-opus-4-6 and claude-sonnet-4-6 accept it. Not a version rule: no rule fits
# (opus-5 accepts, opus-5-5 does not), and the capability does not follow
# supports_disabled_thinking either. A model missing here that rejects it fails loudly
# (a ValidationException on the first structured call), never silently.
_REJECTS_FORCED_TOOL_CHOICE: frozenset[str] = frozenset({"claude-opus-5-5"})


def supports_forced_tool_choice(model: str | None) -> bool:
    """Return True when ``model`` accepts a forced tool choice (Converse
    ``toolChoice`` "tool"). ``jobs.bedrock.structured`` forces its answer tool on
    such a model and lets any other model choose ("auto") while telling it to answer
    through the tool.

    Args:
        model: Short name, full Bedrock ID, or ``None``.
    """
    return normalize_short_name(model) not in _REJECTS_FORCED_TOOL_CHOICE


THINKING_MODES: tuple[str, ...] = ("adaptive", "enabled", "disabled")


def supported_thinking_modes(model: str | None) -> list[str]:
    """Thinking modes ``model`` accepts on the wire.

    Opus >= 5 accepts only ``adaptive`` (``disabled``/``enabled`` 400); every
    other model accepts all three. Derived from :func:`supports_disabled_thinking`
    so there is exactly one rule. Exposed to the Settings UI via /api/settings.
    """
    return list(THINKING_MODES) if supports_disabled_thinking(model) else ["adaptive"]


def effective_thinking_mode(stored: str | None, model: str | None) -> str:
    """The thinking mode a session on ``model`` actually runs with.

    The stored mode when the model accepts it; ``adaptive`` when it does not.
    With no resolved model the stored mode is returned unchanged — never guess.
    The stored value itself is never rewritten: switching back to a model that
    accepts it makes it effective again.
    """
    mode = stored if stored in THINKING_MODES else "adaptive"
    if not model:
        return mode
    return mode if mode in supported_thinking_modes(model) else "adaptive"


def context_window_for(model: str | None) -> int:
    """Return the context-window size to ASSUME for ``model``.

    Deliberately a DIFFERENT question from :func:`is_large_context_model`, which
    answers "is this model PROVEN 1M-capable" (what the CLI's ``[1m]`` flag
    needs). Three input classes, three answers:

    - a known large-context Claude          -> :data:`LARGE_CONTEXT_WINDOW`
    - a RECOGNIZED small Claude (haiku/gen-3) -> :data:`DEFAULT_CONTEXT_WINDOW`
    - UNRESOLVED (``None``/empty) or UNRECOGNIZED (custom ARN, non-Claude)
      -> whatever the DEFAULT MODEL this deployment runs would get

    That third class is the load-bearing one, and getting it wrong broke things
    in both directions. Treating it as small halved a custom-ARN deployment's
    context budget (100K -> 50K), shrank its resume budget (150K -> 60K) and
    made a 150K-token turn report ``warn``; ``session_unit._model_name`` is also
    ``None`` until the SDK options are built, while the context-warning path
    already reads the window, so a real flagship session would false-alarm at
    140K. Treating it as 1M in the *predicate* instead made the CLI claim a 1M
    window for ``gpt-4o``. Hence: lenient HERE, strict THERE.

    Args:
        model: Short name, full Bedrock ID, or ``None``.

    Returns:
        :data:`LARGE_CONTEXT_WINDOW` or :data:`DEFAULT_CONTEXT_WINDOW`.
    """
    base = normalize_short_name(model)
    if not base or not _is_recognized_claude(base):
        # Stand in for the default model. Recurse on the flagship, with a guard
        # so an unparseable flagship cannot loop.
        if base == normalize_short_name(FLAGSHIP_MODEL):
            return LARGE_CONTEXT_WINDOW if is_large_context_model(base) else DEFAULT_CONTEXT_WINDOW
        return context_window_for(FLAGSHIP_MODEL)
    return LARGE_CONTEXT_WINDOW if is_large_context_model(base) else DEFAULT_CONTEXT_WINDOW


def default_bedrock_model_map() -> dict[str, str]:
    """Return a fresh copy of the registry for seeding config defaults.

    A copy (not the module dict) so a caller mutating its config cannot corrupt
    the authority.
    """
    return dict(MODEL_REGISTRY)


# ---------------------------------------------------------------------------
# Minimum Claude Code CLI version per model
# ---------------------------------------------------------------------------
#
# A CLI older than this rejects the model with a 400 ("Claude Code X does not
# support this model; version Y or newer is required"), or may not fail fast at
# all. Headless jobs run the SYSTEM ``claude`` on PATH (not the SDK-bundled one),
# so they check this floor before spawning (``jobs/cli_result``). Add an entry
# when a model with a newer floor is registered.
MIN_CLI_VERSION: dict[str, tuple[int, int, int]] = {
    "claude-opus-5-5": (2, 1, 280),
}


def min_cli_version_for(model: str | None) -> tuple[int, int, int] | None:
    """Minimum CLI version for ``model`` (short name, Bedrock ID or ``[1m]`` form),
    or None if the model has no known floor."""
    return MIN_CLI_VERSION.get(normalize_short_name(model))


def default_available_models() -> list[str]:
    """Return a fresh copy of the model-name list, flagship first."""
    return list(MODEL_NAMES)
