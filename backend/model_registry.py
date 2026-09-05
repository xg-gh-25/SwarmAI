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

# ---------------------------------------------------------------------------
# The registry — short name -> Bedrock inference-profile ID
# ---------------------------------------------------------------------------
#
# FLAGSHIP FIRST (see the order warning in the module docstring).
#
# Bedrock IDs are NOT mechanically derivable from the short name — note that
# 4-6 carries a "-v1" suffix while 4-8 and 5 do not. Never synthesize an ID as
# f"us.anthropic.{short_name}"; look it up here.
MODEL_REGISTRY: dict[str, str] = {
    "claude-opus-5": "us.anthropic.claude-opus-5",
    "claude-opus-4-8": "us.anthropic.claude-opus-4-8",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
}

# Derived — never hand-maintain a parallel list.
MODEL_NAMES: list[str] = list(MODEL_REGISTRY)

# The flagship / default model. Derived from position, so promoting a new
# flagship means inserting it at the top of MODEL_REGISTRY and nothing else.
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

# Bedrock ID prefixes / suffixes to strip before a short-name lookup.
_ID_PREFIXES: tuple[str, ...] = ("us.anthropic.", "global.anthropic.")


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
    base = model
    for prefix in _ID_PREFIXES:
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
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


def default_available_models() -> list[str]:
    """Return a fresh copy of the model-name list, flagship first."""
    return list(MODEL_NAMES)
