"""The headless-job CLI must take its model from the registry SSOT, not a literal.

Root cause (run_33585252, re-verified 2026-09-11): `backend/jobs/executor.py` built
its `claude --print` command with a hardcoded `"--model", "sonnet"`. A bare family
alias carries NO `[1m]` suffix, and that suffix is what makes the CLI open the full
1M context window (`prompt_builder.resolve_model` appends it for 1M-capable models).
So every agent_task job ran against the SMALL window and 6 scheduled jobs failed
with `400 Input is too long`: morning-reflect, docs-freshness-audit,
context-sweep-live, context-sweep-archive, github-community-morning,
github-community-evening.

The fix direction is A (single SSOT), not B (keep the literal + add a beta flag):
`model_registry` already owns the model list and the 1M-capability predicate, so a
second hardcoded opinion in the job path is exactly the drift that broke this.

METHODOLOGY: TDD RED->GREEN. Each test below was observed FAILING against the
hardcoded literal before the fix, and the model-source test is mutation-proven
(restoring the literal turns it RED again).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

_JOBS_DIR = Path(__file__).resolve().parents[1] / "jobs"


def _model_args_in_cli_lists() -> list[tuple[str, ast.AST]]:
    """Every value passed positionally after a `"--model"` element, (file, node).

    Scans the WHOLE `jobs/` tree, not just executor.py: the first version of this
    guard was executor-scoped and therefore blind to a third, identically-shaped
    call site in `jobs/handlers/ddd_self_audit.py` (found by adversarial review).
    A model-passing site can live in any handler, so the guard follows the shape
    across the package.

    Parsed via AST rather than regex-over-source ON PURPOSE. A source-string
    assertion is vacuous-prone in both directions: it matched the phrase
    `"--model", "sonnet"` inside this fix's OWN explanatory comment (observed —
    the test stayed RED after both call sites were correctly fixed), and it would
    equally match a commented-OUT call site while the live one regressed. The AST
    sees only executable code, so a comment can never satisfy or break it.
    """
    found: list[tuple[str, ast.AST]] = []
    for py in sorted(_JOBS_DIR.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a broken file is another test's problem
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            for i, elt in enumerate(node.elts[:-1]):
                if isinstance(elt, ast.Constant) and elt.value == "--model":
                    found.append((str(py.relative_to(_JOBS_DIR)), node.elts[i + 1]))
    return found


def test_agent_task_model_is_not_a_hardcoded_family_alias():
    """AC1 (RED before fix): no CLI command may pass a bare model literal.

    A literal alias carries no `[1m]` suffix, which caps the context window and
    reproduces the 400. Only a resolved expression is acceptable.
    """
    args = _model_args_in_cli_lists()
    assert args, "no `--model` argument found in any CLI command list"
    literals = [(f, a.value) for f, a in args if isinstance(a, ast.Constant)]
    assert not literals, (
        f"job CLI passes hardcoded model literal(s) {literals!r} — must resolve "
        "from the registry SSOT so the [1m] context suffix is applied"
    )


def test_every_model_arg_calls_the_one_shared_resolver():
    """AC2: ONE helper owns resolution — not an inlined `config.get` per call site.

    Two separate call sites build a CLI command (agent_task main path, Slack-DM
    path). Per-site inlining is the drift shape that produced this bug, so the
    contract is that every `--model` argument is a call to the same resolver.
    """
    from jobs import model_resolve as mr

    assert callable(getattr(mr, "resolve_job_model", None)), (
        "expected a single resolve_job_model() helper owning model resolution"
    )
    args = _model_args_in_cli_lists()
    assert len(args) >= 3, (
        f"expected all 3 known CLI call sites to pass --model, found {len(args)}: "
        f"{[f for f, _ in args]}"
    )
    for fname, a in args:
        assert isinstance(a, ast.Call) and getattr(a.func, "id", "") == "_resolve_job_model", (
            f"{fname}: a --model argument is {ast.dump(a)[:80]} — every one must "
            "call _resolve_job_model() so the call sites cannot drift"
        )


def test_resolver_actually_reads_the_live_config(monkeypatch, caplog):
    """AC5 — THE test whose absence let a CRITICAL ship green.

    Every other test passes `config` explicitly, so none exercised the
    `config is None` branch. The first version of that branch imported
    `core.app_config_manager.get_app_config` — a symbol that DOES NOT EXIST — so
    the ImportError hit a silent `except Exception` and the helper returned the
    fallback on every call. It looked correct only because the fallback happened
    to equal the configured `default_model`.

    This drives the real no-arg path with a monkeypatched singleton and asserts
    the CONFIGURED value comes through, so the branch cannot be dead again.
    """
    import logging
    from jobs import model_resolve as mr
    from core.app_config_manager import AppConfigManager

    seen: list[str] = []

    def _spy(self, key, default=None):
        seen.append(key)
        return {"default_model": "claude-sonnet-4-6"}.get(key, default)

    monkeypatch.setattr(AppConfigManager, "get", _spy, raising=True)
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model()

    # The spy makes non-vacuity STRUCTURAL: if the resolver ever stops using this
    # accessor the patch goes inert, and without this assertion the test would
    # keep passing on whatever the developer's live config.json happens to say.
    assert "default_model" in seen, (
        "the resolver never asked the config for default_model — the patched "
        "accessor is not the one it uses, so this test is no longer testing it"
    )

    assert "claude-sonnet-4-6" in resolved, (
        f"no-arg resolve returned {resolved!r} — it is NOT reading the live config "
        "(the config-read branch is dead, the exact CRITICAL this test exists for)"
    )
    assert not [r for r in caplog.records if "config read failed" in r.message], (
        "the config read logged a failure — the accessor is wrong again"
    )


def test_resolver_logs_loudly_when_the_config_read_breaks(monkeypatch, caplog):
    """AC6: a broken config read must be LOUD, never silently swallowed.

    A permanent failure (moved module / renamed symbol) would otherwise make every
    scheduled job ignore the configured model forever with zero signal — which is
    precisely what happened. The fallback is still returned (an unattended job must
    not die), but it must leave a WARNING behind.
    """
    import logging
    from jobs import model_resolve as mr
    from core.app_config_manager import AppConfigManager

    def _boom(self, key, default=None):
        raise RuntimeError("simulated config backend failure")

    monkeypatch.setattr(AppConfigManager, "get", _boom, raising=True)
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model()

    assert resolved, "a broken config must still yield a usable model"
    assert any("config read failed" in r.message for r in caplog.records), (
        "a broken config read was swallowed silently — that is how the original "
        "dead-code CRITICAL stayed invisible"
    )


def test_resolver_prefers_config_default_and_keeps_the_1m_suffix():
    """AC3: the helper returns the configured default WITH its [1m] suffix intact.

    The suffix is the whole point — stripping it reproduces the original 400. Driven
    through the real helper with a stubbed config so the test needs no live
    config.json (self-verifying in a bare checkout, per the BVT admission rule).
    """
    from jobs import model_resolve as mr

    resolved = mr.resolve_job_model({"default_model": "claude-opus-5"})
    assert resolved, "resolver returned nothing for a valid configured model"
    assert "[1m]" in resolved, (
        f"resolved model {resolved!r} lost the [1m] suffix — the CLI would open the "
        "small window and reproduce 400 Input-is-too-long"
    )


def test_resolver_falls_back_when_config_is_unusable():
    """AC4: a missing/broken config must not crash a scheduled job.

    A job runs unattended; raising here would turn a config gap into a dead job.
    The fallback must still be a real, resolvable model string.
    """
    from jobs import model_resolve as mr

    for bad in ({}, {"default_model": ""}, {"default_model": None}):
        resolved = mr.resolve_job_model(bad)
        assert resolved and isinstance(resolved, str), (
            f"resolver returned {resolved!r} for config {bad!r} — a job would lose "
            "its --model argument entirely"
        )


def test_unknown_configured_model_falls_back_loudly(caplog):
    """AC7: a config value the registry does not know must NOT silently pass through.

    Found by adversarial review. `get_bedrock_model_id` passes an unknown id through
    verbatim, and `is_large_context_model` then answers False for it — so a stale
    bare alias like "sonnet" sitting in config would reproduce the original 400 by
    way of CONFIG instead of a literal, with no signal at all. The resolver must
    prefer the registry flagship and say so.
    """
    import logging
    from jobs import model_resolve as mr

    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model({"default_model": "sonnet"})

    assert "[1m]" in resolved, (
        f"unknown model fell through to {resolved!r} with no [1m] — that is the "
        "original 400 reintroduced via config"
    )
    assert any("does not resolve to a known model" in r.message for r in caplog.records), (
        "an unknown configured model was replaced silently — the operator would "
        "never learn their config value is being ignored"
    )


def test_fallback_derives_from_the_registry_not_a_literal():
    """AC8: the fallback tracks the registry flagship.

    A hardcoded fallback inside the very change that removes hardcoded model
    literals is the same drift one level down: promoting a new flagship would
    update the registry and leave the job fallback stale.
    """
    from jobs import model_resolve as mr
    from model_registry import FLAGSHIP_MODEL

    assert mr._flagship_fallback() == FLAGSHIP_MODEL, (
        "job fallback does not track model_registry.FLAGSHIP_MODEL"
    )


def test_resolver_module_has_no_import_side_effects():
    """AC9: importing the resolver must not spawn a subprocess or mutate PATH.

    This is why the resolver is a leaf module instead of a function in executor.py:
    importing `jobs.executor` runs `_fix_path_from_login_shell()` (a `zsh -lic`
    subprocess) and REPLACES os.environ["PATH"]. When the handler imported the
    resolver from executor, that fired during pytest collection and on an HTTP
    request path. Asserted structurally (no executor import) rather than by timing,
    so the guard is deterministic rather than machine-speed dependent.
    """
    import ast as _ast

    src = (_JOBS_DIR / "model_resolve.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    # Walk EVERY module-level node for a Call, not just bare `ast.Expr` statements:
    # a side effect hides just as well in an assignment (`_CFG = something.load()`),
    # and an Expr-only scan would have shipped that green (adversarial review).
    offenders: list[str] = []
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            continue  # a call INSIDE a function only runs when called
        for sub in _ast.walk(node):
            if not isinstance(sub, _ast.Call):
                continue
            dotted = _ast.unparse(sub.func) if hasattr(_ast, "unparse") else ""
            if dotted == "logging.getLogger":
                continue  # the one allowed module-level call: no IO, no env change
            offenders.append(dotted or type(sub.func).__name__)
    assert not offenders, (
        f"model_resolve makes module-level call(s) {offenders!r} — it must stay "
        "side-effect-free so any handler can import it cheaply"
    )
    # And the handler must not reach executor at MODULE level. A function-local
    # `from ..executor import ...` is fine and deliberate (the handler uses one to
    # avoid an import cycle) — only a top-level import fires at import time, which
    # is the thing that dragged the zsh subprocess into pytest collection.
    handler_tree = _ast.parse(
        (_JOBS_DIR / "handlers" / "ddd_self_audit.py").read_text(encoding="utf-8"))
    module_level_executor = [
        n for n in handler_tree.body
        if isinstance(n, _ast.ImportFrom) and (n.module or "").endswith("executor")
    ]
    assert not module_level_executor, (
        "the handler imports executor at MODULE level — that re-drags the zsh "
        "subprocess and the os.environ['PATH'] replacement into its import path"
    )


def test_a_model_that_loses_the_1m_suffix_warns(caplog):
    """AC10: never silently return a Bedrock model without `[1m]`.

    Found by adversarial review. `is_large_context_model` cannot parse an
    inference-profile ARN, so a user who legitimately configures one reaches the
    end of the resolver with no `[1m]` — the CLI then opens the SMALL window and a
    large prompt reproduces the original 400. The ARN is kept (it is very likely
    what the operator meant; substituting the flagship would be the
    silently-ignored-config class), but the degradation must be VISIBLE.
    """
    import logging
    from jobs import model_resolve as mr

    arn = "arn:aws:bedrock:us-east-1:1:inference-profile/us.anthropic.claude-opus-5"
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model({"default_model": arn, "use_bedrock": True})

    assert resolved == arn, "the configured ARN must be preserved, not substituted"
    assert any("no [1m] suffix" in r.message for r in caplog.records), (
        "a Bedrock model reached the CLI without [1m] and nobody was told — that is "
        "the original 400 waiting to happen, silently"
    )


def test_a_normal_model_does_not_trigger_the_no_1m_warning(caplog):
    """AC10b: the new warning must not fire on the healthy path.

    A guard that cries on every run is noise that trains the reader to ignore it.
    """
    import logging
    from jobs import model_resolve as mr

    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model(
            {"default_model": "claude-opus-5", "use_bedrock": True})

    assert resolved.endswith("[1m]"), f"healthy path lost its suffix: {resolved!r}"
    assert not [r for r in caplog.records if "no [1m] suffix" in r.message], (
        "the no-[1m] warning fired on a perfectly resolved model — false alarm"
    )


def test_a_legitimately_non_1m_bedrock_id_does_not_warn(caplog):
    """AC10c: the no-[1m] warning must not fire on a correctly-non-1M model.

    Found by adversarial review of the warning itself. `us.anthropic.claude-haiku-4-5`
    resolves fine and SHOULD have no `[1m]` — haiku has no 1M window. Warning there
    is a false alarm on correct output, and a guard that cries on correct output
    trains the reader to ignore it (which would cost us the ARN case it exists for).
    """
    import logging
    from jobs import model_resolve as mr

    with caplog.at_level(logging.WARNING, logger="swarm.jobs.model_resolve"):
        resolved = mr.resolve_job_model(
            {"default_model": "us.anthropic.claude-haiku-4-5", "use_bedrock": True})

    assert not resolved.endswith("[1m]"), (
        f"haiku must NOT get a 1M suffix, got {resolved!r}"
    )
    assert not [r for r in caplog.records if "no [1m] suffix" in r.message], (
        "warned about a legitimately non-1M model — false alarm that erodes the "
        "signal the ARN case depends on"
    )
