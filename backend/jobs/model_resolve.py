"""
Swarm Job System — model resolution (the ONE SSOT for every job CLI).

Every headless `claude --print` command in the job system takes its `--model`
argument from :func:`resolve_job_model`. There are three such call sites (the
agent_task main path, the Slack-DM path, and the ddd_self_audit handler), and
they each used to carry their own hardcoded ``"--model", "sonnet"``.

WHY THAT BROKE: a bare family alias has neither the Bedrock inference-profile
prefix nor the ``[1m]`` suffix, and that suffix is the CLI's signal to open the
full 1M context window. So every agent_task ran against the SMALL window and 6
scheduled jobs died on ``400 Input is too long`` (morning-reflect,
docs-freshness-audit, context-sweep-live, context-sweep-archive,
github-community-morning, github-community-evening). The session path
(`core.prompt_builder.resolve_model`) already did both steps correctly — the job
path was a second copy of that logic, and this bug IS the drift between them.

WHY THIS IS ITS OWN LEAF MODULE, not a function in ``executor.py``: importing
``executor`` costs ~1s and has a module-level side effect — it runs
``_fix_path_from_login_shell()`` (a ``zsh -lic`` subprocess, timeout 10s) and
REPLACES ``os.environ["PATH"]``. Having a handler import the resolver from
``executor`` therefore dragged that subprocess into pytest collection (clobbering
PATH for the whole test process) and onto an HTTP request path
(``core/ddd_drift_signal.py`` lazy-imports the handler, reached from
``routers/eval.py``). A leaf module keeps the resolver reachable with zero side
effects. Found by adversarial review, not by the tests.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("swarm.jobs.model_resolve")


def resolve_job_model(config: "dict | None" = None) -> str:
    """Return the model string for a headless job's ``--model`` argument.

    Resolution order: the caller-supplied (or live) config ``default_model`` —
    the same key the desktop session honours — then ``model_registry``'s
    ``FLAGSHIP_MODEL`` as the fallback. The Bedrock inference-profile
    translation and the ``[1m]`` suffix are then applied exactly as
    :meth:`core.prompt_builder.PromptBuilder.resolve_model` applies them, because
    the CLI needs both to reach the right model at its full window.

    NEVER raises. A scheduled job runs unattended, so a missing or malformed
    config must degrade to a usable default rather than kill the run — an
    exception here would turn a config gap into a permanently dead job. Every
    degradation is logged at WARNING (see the comment on the first handler).
    """
    fallback = _flagship_fallback()
    model = ""
    cfg: dict = config if isinstance(config, dict) else {}

    if config is None:
        try:
            from core.app_config_manager import AppConfigManager
            mgr = AppConfigManager.instance()
            # NOTE: `.get()` lazily calls `.load()` on a cold cache, and `load()`
            # writes DEFAULT_CONFIG to disk when the file is missing/malformed
            # (verified by execution — an earlier version of this comment claimed
            # the opposite, which was simply false). In the daemon the singleton is
            # already warm, so no write occurs; we accept the cold-start write
            # rather than re-implement config reading, because a second reader
            # would be exactly the kind of duplicate this module exists to remove.
            model = (mgr.get("default_model") or "").strip()
            cfg = {"use_bedrock": mgr.get("use_bedrock"),
                   "bedrock_model_map": mgr.get("bedrock_model_map")}
        except Exception as exc:  # noqa: BLE001 — unattended path, see docstring
            # LOUD on purpose: a PERMANENT failure here (renamed symbol, moved
            # module) would make every job silently ignore the configured model
            # forever. The first version of this resolver imported a function that
            # does not exist; the ImportError hit a bare `except: pass`, so it read
            # nothing and looked correct only because the fallback happened to
            # equal the configured value. This log is the finding-surface.
            logger.warning(
                "job model config read failed (%s: %s) — falling back to %s",
                type(exc).__name__, exc, fallback,
            )
            model = ""
    else:
        model = (cfg.get("default_model") or "").strip()

    if not model:
        model = fallback
    elif not _is_usable_model(model):
        # An unrecognisable bare alias (e.g. a stale "sonnet") would pass through
        # Bedrock translation verbatim and then FAIL the 1M-capability check —
        # silently reproducing the original 400 by way of config instead of a
        # literal. Prefer the flagship and say so.
        logger.warning(
            "configured default_model %r does not resolve to a known model — "
            "using %s so the job keeps its full context window",
            model, fallback,
        )
        model = fallback

    # Bedrock translation BEFORE the suffix, mirroring prompt_builder: the job
    # path exports CLAUDE_CODE_USE_BEDROCK=true, so the CLI expects the
    # inference-profile id (us.anthropic.*), not the bare family name.
    if cfg.get("use_bedrock"):
        try:
            from config import get_bedrock_model_id
            model = get_bedrock_model_id(
                model, config_map=cfg.get("bedrock_model_map")
            ) or model
        except Exception as exc:  # noqa: BLE001 — never kill an unattended job
            logger.warning(
                "job model Bedrock translation failed (%s: %s) — using %s verbatim",
                type(exc).__name__, exc, model,
            )

    if not model.endswith("[1m]"):
        try:
            from model_registry import is_large_context_model
            if is_large_context_model(model):
                model = model + "[1m]"
        except Exception as exc:  # noqa: BLE001 — a registry import failure must
            logger.warning(  # not kill the job; it runs without the wide window.
                "job model 1M-capability check failed (%s: %s) — no [1m] suffix",
                type(exc).__name__, exc,
            )

    # FAIL-LOUD on the remaining silent path. Reaching here without `[1m]` under
    # Bedrock means the CLI opens the SMALL window — the exact condition that
    # produced the original 400 — while every branch above believed it was done.
    # The known live case is an inference-profile ARN: `is_large_context_model`
    # cannot parse one, so a user who legitimately configures an ARN silently gets
    # the small window. Deliberately a WARNING and NOT a substitution: the ARN is
    # very likely the model the operator MEANT, and swapping it for the flagship is
    # the "configured value silently ignored" class this module exists to remove.
    # `core.prompt_builder` has the same ARN blind spot, so staying loud-but-
    # faithful keeps the two paths in parity rather than forking their behaviour.
    # Found by adversarial review.
    # SCOPED to UNPARSEABLE ids only. A first version warned on any Bedrock model
    # lacking `[1m]`, which false-alarmed on a legitimately NON-1M id like
    # `us.anthropic.claude-haiku-4-5` — haiku having no 1M window is the correct
    # answer, and a guard that cries on correct output trains the reader to ignore
    # it. `normalize_short_name` is the discriminator: it reduces a prefixed id to
    # a real short name (`claude-haiku-4-5` — known, deliberately not 1M) but
    # returns an ARN unchanged, i.e. it could not be parsed at all. Only the
    # unparseable case is a silent hazard.
    if cfg.get("use_bedrock") and not model.endswith("[1m]"):
        try:
            from model_registry import normalize_short_name
            _unparseable = normalize_short_name(model) == model and (
                model.startswith("arn:") or "." in model
            )
        except Exception:  # noqa: BLE001 — warn rather than stay silent
            _unparseable = True
        if _unparseable:
            logger.warning(
                "job model %r got no [1m] suffix and its 1M capability cannot be "
                "derived from the id — the CLI will open the SMALL context window "
                "and a large prompt may fail with 400 Input-is-too-long (an "
                "inference-profile ARN is the known case)",
                model,
            )
    return model


def _flagship_fallback() -> str:
    """The registry's flagship, or a last-resort literal if the registry is broken.

    Derived rather than hardcoded: a hardcoded fallback in the very commit that
    removes hardcoded model literals is the same drift shape one level down —
    promoting a new flagship would update the registry and leave this stale.
    """
    try:
        from model_registry import FLAGSHIP_MODEL
        if FLAGSHIP_MODEL:
            return FLAGSHIP_MODEL
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "model registry unavailable for the job fallback (%s: %s)",
            type(exc).__name__, exc,
        )
    # Only reachable if model_registry itself is unimportable, in which case the
    # job is going to fail anyway; a plausible string beats an empty --model.
    return "claude-opus-5"


def _is_usable_model(model: str) -> bool:
    """True unless ``model`` is an unrecognisable bare alias.

    DELIBERATELY PERMISSIVE, and that direction was earned. An earlier version
    tested membership in ``MODEL_NAMES`` — but the Settings API
    (``routers/settings.py``) validates ``default_model`` against
    ``available_models``, which is AUTO-DISCOVERED from Bedrock inference profiles
    and is a SUPERSET of the registry's hardcoded names; it explicitly documents
    that ``us.*``/``global.*``/``anthropic.*``/``arn:`` ids and
    ``bedrock_model_map`` keys are legitimate and that "a narrower check would 400
    a legitimate existing deployment". So a membership test rejected a
    newly-discovered ``claude-opus-6`` (verified: absent from MODEL_NAMES, yet
    ``is_large_context_model`` answers True) and every ARN — making jobs SILENTLY
    substitute the flagship for the model the user chose in Settings. That is the
    same "configured value silently ignored" class this module exists to
    eliminate, re-introduced with the opposite sign. Found by adversarial review.

    What this rejects is only the real failure shape: a bare alias (``"sonnet"``,
    ``"opus"``) that no normaliser resolves to a real model, which would pass
    Bedrock translation verbatim and then fail the 1M check — reproducing the
    original 400 by way of config.

    Fail-OPEN on an unimportable registry: refusing a possibly-valid configured
    model is worse than skipping an advisory check.
    """
    if model.startswith(("us.", "global.", "anthropic.", "arn:")):
        return True  # fully-qualified Bedrock id / ARN — never second-guess it
    try:
        from model_registry import (
            MODEL_NAMES, is_large_context_model, normalize_short_name,
        )
    except Exception:  # noqa: BLE001
        return True
    # normalize_short_name is the CANONICAL stripper (prefixes, :0, -v1, and the
    # [1m] suffix). Hand-rolling that logic here is exactly what missed [1m].
    bare = normalize_short_name(model)
    return bool(bare) and (bare in set(MODEL_NAMES) or is_large_context_model(bare))
