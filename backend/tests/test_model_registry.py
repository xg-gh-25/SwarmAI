"""Tests for the single model-registry authority and every derivation from it.

What is tested and WHY each test has teeth (each one goes RED if the specific
defect it guards is reintroduced — stated per class):

- ``TestStdlibOnly``          — the registry must import under the SYSTEM python3
- ``TestFlagshipOrdering``    — flagship-first, because settings.py auto-resets
                                default_model to available_models[0]
- ``TestNoDuplicateTables``   — every derived consumer agrees with the authority
- ``TestWindowFamilyAgreement`` — get_model_context_window and _is_1m_model can
                                never disagree, and the unknown default respects
                                the 64K L0-routing floor
- ``TestTemperatureDerivation`` — a newly promoted opus is not silently treated
                                as temperature-supporting
- ``TestHiveSeedGeneration``  — the shell seed is generated from the authority,
                                so the two cannot drift
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import model_registry as reg
from config import ANTHROPIC_TO_BEDROCK_MODEL_MAP, get_bedrock_model_id
from core.app_config_manager import DEFAULT_CONFIG
from core.context_directory_loader import THRESHOLD_USE_L1
from core.prompt_builder import PromptBuilder

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

# The SYSTEM interpreter — deliberately NOT the venv/mise python. The
# stdlib-only requirement exists because hive/release.sh packages with no
# venv activated, and a shim that happens to have extra packages installed
# would mask a violation (that exact instrument error produced a wrong plan).
SYSTEM_PY = "/usr/bin/python3"


def _skip_without_system_python() -> None:
    """Skip when /usr/bin/python3 is absent (many Linux CI images).

    A hard failure there is a FALSE red: a missing interpreter means "cannot
    check", not "the stdlib-only property is broken".
    """
    if not Path(SYSTEM_PY).exists():
        pytest.skip(f"{SYSTEM_PY} not present on this platform")


class TestStdlibOnly:
    """The registry MUST be importable by the system interpreter.

    Teeth: adding any third-party import to model_registry.py turns this RED.
    That property is load-bearing — hive/release.sh generates the Hive seed by
    importing this module with the SYSTEM python3 (no venv is activated during
    packaging). This is also why the registry could not live in config.py:
    that module imports pydantic_settings.
    """

    @pytest.fixture(autouse=True)
    def _require_system_python(self):
        _skip_without_system_python()

    def test_system_python3_can_import_registry(self):
        result = subprocess.run(
            [SYSTEM_PY, "-c", "import model_registry; print(model_registry.FLAGSHIP_MODEL)"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            "the SYSTEM python3 must be able to import model_registry (a "
            "third-party import was likely added). stderr:\n" + result.stderr
        )
        assert result.stdout.strip() == reg.FLAGSHIP_MODEL

    def test_config_module_is_NOT_importable_by_system_python3(self):
        """Documents WHY the registry is a separate module, not part of config.py.

        ⚠️ ASYMMETRIC on purpose: a system python that HAPPENS to have
        pydantic_settings (distro package, `pip --user`) makes config.py
        importable without anything being broken, so this SKIPS rather than
        fails in that case. The load-bearing direction is the sibling test
        (the registry MUST import); this one only documents the rationale.
        """
        result = subprocess.run(
            [SYSTEM_PY, "-c", "import config"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            pytest.skip(
                "system python has config.py's third-party deps installed — the "
                "rationale for a separate registry module cannot be demonstrated "
                "here, but the stdlib-only requirement still holds"
            )
        assert "pydantic_settings" in result.stderr


class TestFlagshipOrdering:
    """Flagship must be FIRST in the derived model list.

    Teeth: appending a newer model at the END of MODEL_REGISTRY turns this RED.
    routers/settings.py auto-resets default_model to available_models[0] when
    the available list changes, so a newest-last order would silently select the
    OLDEST model as the default.
    """

    def test_flagship_is_first(self):
        assert reg.MODEL_NAMES[0] == reg.FLAGSHIP_MODEL

    def test_first_entry_is_the_NEWEST_model_independently_of_the_derivation(self):
        """The teeth for flagship-first — derived independently of FLAGSHIP_MODEL.

        ``FLAGSHIP_MODEL`` is *defined* as ``MODEL_NAMES[0]``, so asserting
        ``MODEL_NAMES[0] == FLAGSHIP_MODEL`` is a tautology: it stays GREEN even
        if a newer model is appended at the END (verified by mutation — that
        exact reordering did not turn the tautological test red).

        This test instead compares VERSIONS: the first entry must be the highest
        opus version present. Moving a newer model out of first place turns it
        RED, which is what protects settings.py's ``new_models[0]`` auto-reset
        from silently selecting an older model as the default.
        """
        # Rank across ALL families (model_version), not opus-only: an
        # opus-only ranking spuriously FAILS the moment a sonnet becomes the
        # flagship, because no sonnet would be in the ranking set at all.
        versions = {
            name: reg.model_version(name)
            for name in reg.MODEL_NAMES
            if reg.model_version(name) is not None
        }
        assert versions, "registry has no rankable model"
        # Compare (major, minor) only — the family string must not decide order.
        newest = max(versions, key=lambda n: versions[n][1:])
        assert reg.MODEL_NAMES[0] == newest, (
            f"MODEL_REGISTRY must list the newest model FIRST — found "
            f"{reg.MODEL_NAMES[0]!r} first but {newest!r} is newer. "
            f"settings.py auto-resets default_model to available_models[0], so a "
            f"newest-last order silently downgrades the default."
        )

    def test_default_config_default_model_is_flagship(self):
        assert DEFAULT_CONFIG["default_model"] == reg.FLAGSHIP_MODEL

    def test_default_config_available_models_is_flagship_first(self):
        assert DEFAULT_CONFIG["available_models"][0] == reg.FLAGSHIP_MODEL

    def test_default_model_is_a_member_of_available_models(self):
        """settings.py returns 400 when default_model is not in available_models."""
        assert DEFAULT_CONFIG["default_model"] in DEFAULT_CONFIG["available_models"]

    def test_auto_reset_would_pick_the_flagship(self):
        """Simulates settings.py's `updates["default_model"] = new_models[0]`."""
        new_models = DEFAULT_CONFIG["available_models"]
        assert new_models[0] == reg.FLAGSHIP_MODEL, (
            "the settings auto-reset picks available_models[0]; a non-flagship "
            "here means a config update silently downgrades the default model"
        )


class TestNoDuplicateTables:
    """Every consumer derives from the authority instead of a private copy.

    Teeth: re-adding a literal model table to config.py or
    app_config_manager.DEFAULT_CONFIG turns these RED as soon as the registry
    gains an entry the copy lacks — which is exactly how the original drift
    went unnoticed.
    """

    def test_config_map_is_the_registry(self):
        assert ANTHROPIC_TO_BEDROCK_MODEL_MAP == reg.MODEL_REGISTRY

    def test_default_config_map_matches_registry(self):
        assert DEFAULT_CONFIG["bedrock_model_map"] == reg.MODEL_REGISTRY

    def test_default_config_available_matches_registry(self):
        assert DEFAULT_CONFIG["available_models"] == reg.MODEL_NAMES

    def test_default_config_map_is_a_copy_not_the_authority(self):
        """A caller mutating its config must not corrupt the registry."""
        assert DEFAULT_CONFIG["bedrock_model_map"] is not reg.MODEL_REGISTRY
        assert DEFAULT_CONFIG["available_models"] is not reg.MODEL_NAMES

    def test_flagship_resolves_to_a_real_bedrock_id(self):
        """The original defect: the flagship passed through UNCHANGED as an
        invalid Bedrock id because every hardcoded map lacked its entry."""
        resolved = get_bedrock_model_id(reg.FLAGSHIP_MODEL)
        assert resolved != reg.FLAGSHIP_MODEL, "flagship passed through unmapped"
        assert resolved.startswith("us.anthropic."), resolved

    @pytest.mark.parametrize("short_name", reg.MODEL_NAMES)
    def test_every_registry_model_resolves(self, short_name):
        assert reg.resolve_bedrock_id(short_name).startswith("us.anthropic.")

    def test_unknown_model_resolves_to_none(self):
        """None (not a guess) so each caller picks its own fail-loud behavior."""
        assert reg.resolve_bedrock_id("totally-unknown-model") is None


class TestNormalization:
    """Prefix/suffix stripping must use LITERAL suffixes, not a character class."""

    @pytest.mark.parametrize("raw,expected", [
        ("us.anthropic.claude-opus-4-6-v1", "claude-opus-4-6"),
        ("us.anthropic.claude-opus-5", "claude-opus-5"),
        ("global.anthropic.claude-opus-5", "claude-opus-5"),
        ("claude-opus-5[1m]", "claude-opus-5"),
        ("claude-opus-4-8", "claude-opus-4-8"),
        ("", ""),
        (None, ""),
    ])
    def test_normalize(self, raw, expected):
        assert reg.normalize_short_name(raw) == expected

    def test_date_suffixed_id_is_not_truncated(self):
        """Teeth: reverting to str.rstrip(":0") turns this RED.

        rstrip is a character-class strip, so it ate the trailing "0" of a
        date-suffixed model id (claude-sonnet-4-20250510 -> ...2025051).
        """
        assert reg.normalize_short_name("claude-sonnet-4-20250510") == "claude-sonnet-4-20250510"


class TestWindowFamilyAgreement:
    """The window resolver and the family predicate can never disagree.

    Teeth: restoring the unconditional 1_000_000 default in
    get_model_context_window turns test_no_contradiction RED for haiku/gpt-4.
    The original defect was silent because the window only drives context
    WARNING percentages — a small model simply never warned until it truly
    overflowed.
    """

    MODELS = [
        "claude-opus-5", "claude-opus-4-8", "claude-opus-4-6", "claude-sonnet-4-6",
        "claude-sonnet-4-20250514", "claude-haiku-3", "claude-haiku-4-5",
        "gpt-4",
        "us.anthropic.claude-opus-5", "us.anthropic.claude-opus-4-6-v1",
    ]
    # Falsy inputs are EXCLUDED from the contradiction matrix on purpose: they
    # mean "not resolved yet" and stand in for the flagship, so the family
    # predicate (which correctly answers False for an empty string) is not the
    # right comparison. They get their own tests below.
    UNRESOLVED = [None, ""]

    # NOTE: the contradiction matrix lives in
    # TestAdversarialRegressions::test_window_and_predicate_NEVER_disagree,
    # which covers ALL inputs (including the falsy ones this class used to
    # exclude) and encodes the deliberate window-vs-predicate asymmetry for an
    # unrecognized id. An earlier duplicate here asserted strict equality for
    # every input, which contradicted that split — two locks disagreeing about
    # the same invariant is worse than one, so it was removed rather than
    # weakened.

    @pytest.mark.parametrize("model", MODELS + UNRESOLVED)
    def test_window_respects_the_l0_routing_floor(self, model):
        """Teeth: lowering DEFAULT_CONTEXT_WINDOW below 64K turns this RED.

        context_directory_loader routes model_context_window < THRESHOLD_USE_L1
        onto the L0 compact-cache branch, so a too-small default would silently
        send every unrecognized model down a different assembly path.
        """
        assert PromptBuilder.get_model_context_window(model) >= THRESHOLD_USE_L1

    def test_registry_default_is_above_the_floor(self):
        assert reg.DEFAULT_CONTEXT_WINDOW >= THRESHOLD_USE_L1

    def test_large_models_get_the_large_window(self):
        assert PromptBuilder.get_model_context_window(reg.FLAGSHIP_MODEL) == reg.LARGE_CONTEXT_WINDOW

    def test_recognized_small_model_gets_the_conservative_window(self):
        """Only a RECOGNIZED-but-small Claude is conservative.

        `gpt-4` is deliberately NOT the example here: an unrecognized id means
        "we cannot judge this model's size", which stands in for the flagship —
        see test_custom_arn_keeps_the_large_window for why conflating the two
        halved a real deployment's context budget.
        """
        assert PromptBuilder.get_model_context_window("claude-haiku-3") == \
            reg.DEFAULT_CONTEXT_WINDOW

    @pytest.mark.parametrize("unresolved", [None, ""])
    def test_unresolved_model_stands_in_for_the_flagship(self, unresolved):
        """``None`` means "not resolved YET", NOT "unknown small model".

        ``session_unit._model_name`` is ``None`` until the SDK options are built,
        while the context-warning path already reads the window. Resolving that
        to the conservative window made a real flagship session report ``warn``
        at 140K tokens — a false alarm on the NORMAL path, caught by
        test_context_warning_bridge.py::test_default_model_uses_1m_window.
        """
        # Assert the CONCRETE value, not `== window(FLAGSHIP)`. That comparison
        # is a tautology: the stand-in rule is *implemented* as "use the
        # flagship", so both sides move together. Mutation-proved — inserting a
        # non-large model at position 0 left the tautological form GREEN.
        assert PromptBuilder.get_model_context_window(unresolved) == reg.LARGE_CONTEXT_WINDOW

    def test_unresolved_model_does_not_false_warn(self):
        """The exact production symptom: 140K on an unresolved model stays 'ok'."""
        warning = PromptBuilder.build_context_warning(140_000, None)
        assert warning is not None
        assert warning["level"] == "ok", (
            "an unresolved model must not be treated as a small-window model — "
            "140K tokens is 14% of the flagship's real window"
        )

    def test_prompt_builder_delegates_and_holds_no_table(self):
        """Teeth: re-adding a per-model window dict turns this RED."""
        assert not hasattr(PromptBuilder, "_MODEL_CONTEXT_WINDOWS")


class TestTemperatureDerivation:
    """Opus >= 4.7 rejects a custom temperature — derived, not an allowlist.

    Teeth: reverting to the {"claude-opus-4-7", "claude-opus-4-8"} set turns
    test_newer_opus_is_not_silently_permitted RED, because a newer opus is
    absent from that set and would read as "supports temperature".
    """

    @pytest.mark.parametrize("model,expected", [
        ("claude-opus-4-6", True),
        ("claude-opus-4-7", False),
        ("claude-opus-4-8", False),
        ("claude-opus-5", False),
        ("claude-opus-6", False),
        ("claude-sonnet-4-6", True),
        ("claude-haiku-3", True),
        ("gpt-4", True),
        (None, True),
    ])
    def test_supports_custom_temperature(self, model, expected):
        assert reg.supports_custom_temperature(model) is expected

    def test_newer_opus_is_not_silently_permitted(self):
        """Any opus at or past the flagship must reject a custom temperature."""
        assert reg.supports_custom_temperature(reg.FLAGSHIP_MODEL) is False

    def test_bedrock_prefixed_form_agrees(self):
        assert reg.supports_custom_temperature("us.anthropic.claude-opus-5") is False

    def test_llm_optimizer_CONSUMES_the_derivation(self, monkeypatch):
        """The teeth: llm_optimizer must actually USE the registry predicate.

        Testing the pure registry function alone is vacuous for this defect —
        verified by mutation: restoring the hardcoded
        ``_NO_TEMPERATURE_MODELS = {"claude-opus-4-7", "claude-opus-4-8"}``
        allowlist in llm_optimizer left the pure-function tests GREEN. This test
        drives the real ``_resolve_bedrock_model()`` with the flagship configured
        and asserts it reports "does not support custom temperature" — which the
        allowlist form gets WRONG, because a newer opus is absent from the set.
        """
        from core import llm_optimizer
        from core.app_config_manager import AppConfigManager

        class _Cfg:
            def get(self, key, default=None):
                if key == "default_model":
                    return reg.FLAGSHIP_MODEL
                if key == "bedrock_model_map":
                    return None  # force registry resolution
                return default

        monkeypatch.setattr(AppConfigManager, "instance", staticmethod(lambda: _Cfg()))
        model_id, supports_temperature = llm_optimizer._resolve_bedrock_model()

        assert model_id == reg.MODEL_REGISTRY[reg.FLAGSHIP_MODEL], (
            "the flagship must resolve via the registry, not an f-string guess"
        )
        assert supports_temperature is False, (
            f"{reg.FLAGSHIP_MODEL} is an opus >= 4.7 and rejects a custom "
            f"temperature — a hardcoded allowlist that predates this model "
            f"reports True here and would send a temperature it rejects"
        )


class TestHiveSeedGeneration:
    """The Hive seed is GENERATED from the registry, never hand-written.

    Teeth: replacing the generator in hive/release.sh with a literal heredoc
    turns test_release_sh_generates_from_registry RED. The seed previously held
    its own copy of the model table, drifted behind the flagship, AND named an
    eval_judge_model absent from its own bedrock_model_map — so every shipped
    Hive silently ran a different judge.
    """

    @property
    def release_sh(self) -> str:
        return (REPO_ROOT / "hive" / "release.sh").read_text(encoding="utf-8")

    def test_release_sh_generates_from_registry(self):
        text = self.release_sh
        assert "model_registry" in text, (
            "release.sh must derive the seed from model_registry, not a literal"
        )
        assert "HIVECFG_GEN" in text, "the generator heredoc is missing"

    def test_release_sh_holds_no_literal_model_table(self):
        """No hardcoded Bedrock id may remain in the seed section."""
        assert "us.anthropic.claude" not in self.release_sh, (
            "a literal Bedrock model id reappeared in release.sh — the shell "
            "must not be a second source of truth for the model table"
        )

    def test_release_sh_preserves_the_json_failfast(self):
        assert "is not valid JSON" in self.release_sh

    def test_generation_is_outside_the_failfast_block(self):
        """An import failure must surface as itself, not as 'invalid JSON'."""
        text = self.release_sh
        gen_at = text.index("HIVECFG_GEN")
        failfast_at = text.index("is not valid JSON")
        assert gen_at < failfast_at

    @staticmethod
    def _extract_generator(text: str) -> str:
        """Return the python body between the two ``HIVECFG_GEN`` heredoc markers.

        Line-based on purpose: matching the terminator plus whatever shell
        follows it (``); then``) would make this test brittle against a harmless
        reformat of the surrounding if-block.
        """
        lines = text.splitlines()
        # Opening line is `... <<'HIVECFG_GEN'` (quoted); the terminator is the
        # bare word on its own line.
        opens = [i for i, ln in enumerate(lines) if "<<'HIVECFG_GEN'" in ln]
        closes = [i for i, ln in enumerate(lines) if ln.strip() == "HIVECFG_GEN"]
        assert len(opens) == 1, f"expected 1 heredoc opener, found {len(opens)}"
        assert len(closes) == 1, f"expected 1 heredoc terminator, found {len(closes)}"
        assert opens[0] < closes[0]
        return "\n".join(lines[opens[0] + 1:closes[0]])

    def test_generated_seed_matches_the_registry(self, tmp_path):
        """Execute the real generator with the SYSTEM python3 and compare."""
        _skip_without_system_python()
        out = tmp_path / "config-hive.json"
        snippet = self._extract_generator(self.release_sh)
        result = subprocess.run(
            [SYSTEM_PY, "-", str(out)],
            input=snippet, cwd=BACKEND_DIR,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        seed = json.loads(out.read_text())
        assert seed["available_models"] == reg.MODEL_NAMES
        assert seed["bedrock_model_map"] == reg.MODEL_REGISTRY
        assert seed["default_model"] == reg.FLAGSHIP_MODEL
        # The defect that shipped to every Hive: a judge model absent from the
        # seed's own map, so eval_runner silently substituted another model.
        assert seed["eval_judge_model"] in seed["bedrock_model_map"]


class TestFrontendFallbackBinding:
    """The frontend's pre-resolve model literal must match the backend flagship.

    The frontend cannot import Python, so this is the ONE case where BINDING is
    correct and derivation is impossible. Same shape as the existing
    shell<->python whitelist guard (test_swarm_workspace_manager.py's
    test_release_sh_whitelist_matches_python_constant).

    Teeth: promoting a new flagship in model_registry without updating
    OnboardingPage.tsx turns this RED — which is exactly the silent drift that
    left the onboarding screen naming a model two generations old.
    """

    ONBOARDING_TSX = REPO_ROOT / "desktop" / "src" / "pages" / "OnboardingPage.tsx"

    def test_onboarding_fallback_matches_backend_flagship(self):
        import re
        text = self.ONBOARDING_TSX.read_text(encoding="utf-8")
        m = re.search(
            r"export const FALLBACK_FLAGSHIP_MODEL\s*=\s*['\"]([^'\"]+)['\"]", text
        )
        assert m, (
            "could not find `export const FALLBACK_FLAGSHIP_MODEL = '...'` in "
            f"{self.ONBOARDING_TSX} — if it was renamed, update this binding test"
        )
        assert m.group(1) == reg.FLAGSHIP_MODEL, (
            f"frontend/backend model drift: OnboardingPage.tsx says "
            f"{m.group(1)!r} but model_registry.FLAGSHIP_MODEL is "
            f"{reg.FLAGSHIP_MODEL!r}. Update the .tsx literal."
        )

    def test_no_other_hardcoded_flagship_in_onboarding(self):
        """No second copy of a model name may hide elsewhere in the file."""
        import re
        text = self.ONBOARDING_TSX.read_text(encoding="utf-8")
        # Strip the sanctioned constant + its docstring-style comment block.
        # `;?` — a prettier config with semi:false drops the semicolon, which
        # would make this strip MISS and report the sanctioned constant as
        # stray (false RED). Backticks are in the quote class because a
        # template literal would otherwise evade detection (false GREEN).
        text_wo_const = re.sub(
            r"export const FALLBACK_FLAGSHIP_MODEL\s*=\s*['\"`][^'\"`]+['\"`];?", "", text
        )
        stray = re.findall(r"['\"`]claude-(?:opus|sonnet|haiku)-[\w.-]+['\"`]", text_wo_const)
        assert not stray, f"stray hardcoded model literal(s) in OnboardingPage.tsx: {stray}"


class TestAdversarialRegressions:
    """Locks for the defects the Gate-2 adversarial review found.

    Each of these was a REAL bug in the first cut of this change; the mutation
    that reintroduces it is named per test.
    """

    def test_date_snapshot_is_not_parsed_as_a_minor_version(self):
        """A yyyymmdd suffix must not become a version number.

        `int("20250514")` succeeds, so the original `except ValueError` branch
        was unreachable and `claude-opus-4-20250514` parsed as `(4, 20250514)` —
        outranking every real 4.x and flipping its temperature verdict away from
        what the previous allowlist gave.

        Teeth: removing the `_DATE_SUFFIX_MIN_LEN` length guard turns this RED.
        """
        assert reg._opus_version("claude-opus-4-20250514") == (4, 0)
        assert reg._opus_version("claude-opus-4-8") == (4, 8)
        assert reg._opus_version("claude-opus-5") == (5, 0)
        # The behavioural consequence: a date-snapshot opus-4 keeps custom
        # temperature, exactly as the deleted allowlist did.
        assert reg.supports_custom_temperature("claude-opus-4-20250514") is True

    @pytest.mark.parametrize("model", [
        None, "", "[1m]", "-v1", ":0", "us.anthropic.",
        "claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6",
        "claude-haiku-3", "claude-haiku-4-5", "gpt-4",
        "arn:aws:bedrock:us-east-1:123456789012:custom-model/x",
        "claude-sonnet-4-20250510", "claude-opus-4-20250514",
    ])
    def test_window_and_predicate_NEVER_disagree(self, model):
        """The change's headline claim, now actually enforced for ALL inputs.

        The first cut special-cased falsy input inside `context_window_for`
        while `is_large_context_model` still returned False for it, so the pair
        contradicted on every unresolved model — and the original matrix test
        EXCLUDED falsy inputs, which is what hid it (a self-granted carve-out).
        No input is excluded here.

        Teeth: moving the stand-in rule back out of the predicate turns this RED.
        """
        window = reg.context_window_for(model)
        is_large = reg.is_large_context_model(model)
        base = reg.normalize_short_name(model)
        recognized = bool(base) and reg._is_recognized_claude(base)
        if recognized:
            # For a model we can actually judge, the two MUST agree exactly.
            assert (window == reg.LARGE_CONTEXT_WINDOW) == is_large, (
                f"{model!r}: window={window} but is_large={is_large}"
            )
        else:
            # Unresolved / unrecognized: the predicate is strictly False (the
            # CLI must not be told to open a window the model may not have),
            # while the assumed window stands in for the default model. This is
            # a DELIBERATE asymmetry, so assert BOTH halves rather than skip.
            assert is_large is False, (
                f"{model!r}: is_large must be False for an unproven model — "
                f"resolve_model would otherwise append [1m] to it"
            )
            assert window == reg.context_window_for(reg.FLAGSHIP_MODEL), (
                f"{model!r}: an unresolved/unrecognized model must assume the "
                f"default model's window, got {window}"
            )

    def test_custom_arn_keeps_the_large_window(self):
        """An unrecognized id stands in for the flagship, not for a small model.

        config.py documents custom-ARN passthrough, so an ARN reaches this
        function as the live model name. Treating it as "recognized and small"
        halved the context budget (100K -> 50K), shrank the resume budget
        (150K -> 60K) and made a 150K-token turn report `warn`.

        Teeth: making `_is_recognized_claude` return True for everything (or
        dropping the unrecognized branch) turns this RED.
        """
        arn = "arn:aws:bedrock:us-east-1:123456789012:custom-model/x"
        assert reg.context_window_for(arn) == reg.LARGE_CONTEXT_WINDOW
        assert PromptBuilder.get_model_context_window(arn) == reg.LARGE_CONTEXT_WINDOW
        warning = PromptBuilder.build_context_warning(150_000, arn)
        assert warning is not None and warning["level"] == "ok"

    def test_recognized_small_claude_still_gets_the_conservative_window(self):
        """The unrecognized carve-out must not swallow the haiku case too."""
        assert reg.context_window_for("claude-haiku-3") == reg.DEFAULT_CONTEXT_WINDOW
        assert reg.is_large_context_model("claude-haiku-3") is False

    def test_one_default_judge_model_across_all_consumers(self):
        """Three files each named their own "default judge" — one of which the
        Hive seed contradicted. They must all read the same registry constant.

        Teeth: re-hardcoding a literal in any of the three turns this RED.
        """
        from core.app_config_manager import DEFAULT_CONFIG as DC
        sys.path.insert(0, str(BACKEND_DIR / "scripts"))
        from scripts import eval_runner
        assert DC["eval_judge_model"] == reg.DEFAULT_JUDGE_MODEL
        assert eval_runner.FALLBACK_JUDGE_MODEL == reg.DEFAULT_JUDGE_MODEL
        # And it must be resolvable — an unresolvable default is the original bug.
        assert reg.resolve_bedrock_id(reg.DEFAULT_JUDGE_MODEL) is not None

    def test_release_sh_uses_the_system_interpreter(self):
        """A venv/conda python3 on the build machine would MASK a stdlib-only
        violation, since it has the third-party packages installed.

        Teeth: reverting release.sh to bare `python3` turns this RED.
        """
        text = (REPO_ROOT / "hive" / "release.sh").read_text(encoding="utf-8")
        assert 'SEED_PY="/usr/bin/python3"' in text, (
            "release.sh must prefer the SYSTEM interpreter for seed generation"
        )
        assert '"${SEED_PY}" - "${SEED}/config-hive.json"' in text

    def test_no_dead_window_constant_in_prompt_builder(self):
        """`_DEFAULT_CONTEXT_WINDOW` became unreferenced once the method
        delegated; a leftover constant is a second source of truth with no
        reader (adversarial LOW)."""
        assert not hasattr(PromptBuilder, "_DEFAULT_CONTEXT_WINDOW")


class TestSecondPassRegressions:
    """Locks for the CRITICAL/HIGH issues the SECOND adversarial pass found.

    The first pass's fixes introduced these; each is mutation-named.
    """

    def test_public_map_is_a_copy_not_the_authority(self):
        """Teeth: `= MODEL_REGISTRY` (an alias) turns this RED.

        routers/agents.py reaches this dict, so an alias let a caller corrupt
        the authority for the whole process.
        """
        assert ANTHROPIC_TO_BEDROCK_MODEL_MAP == reg.MODEL_REGISTRY
        assert ANTHROPIC_TO_BEDROCK_MODEL_MAP is not reg.MODEL_REGISTRY

    def test_judge_passthrough_prefixes_match_the_validator(self):
        """The PUT validator and the runtime resolver must accept the SAME set.

        They diverged: settings.py deliberately allowed `arn:` while
        _get_judge_model passed through only us./anthropic., so an ARN judge
        model was ACCEPTED at PUT and then silently substituted at runtime —
        the original defect, re-created through the sanctioned path.

        Teeth: narrowing either prefix set turns this RED.
        """
        sys.path.insert(0, str(BACKEND_DIR / "scripts"))
        from scripts.eval_runner import _get_judge_model
        from unittest.mock import patch
        arn = "arn:aws:bedrock:us-east-1:123456789012:custom-model/x"
        cfg = json.dumps({"eval_judge_model": arn, "bedrock_model_map": {}})
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=cfg):
            assert _get_judge_model() == arn, (
                "an ARN judge model must pass through unchanged — settings.py "
                "accepts it, so substituting it here silently ignores the config"
            )

    def test_judge_default_is_not_the_flagship(self):
        """The judge must be a DIFFERENT tier from production.

        Its docstring calls this load-bearing (a judge that drifts in lockstep
        with the agent cancels out regressions), but nothing enforced it —
        setting DEFAULT_JUDGE_MODEL to the flagship left every test green.
        """
        assert reg.DEFAULT_JUDGE_MODEL != reg.FLAGSHIP_MODEL, (
            "the self-eval judge must not be the production flagship, or a "
            "regression in both cancels out and the eval reports false health"
        )
        assert reg.resolve_bedrock_id(reg.DEFAULT_JUDGE_MODEL) is not None

    def test_legacy_claude_names_are_recognized_as_small(self):
        """`claude-2` / `claude-instant-1` are real legacy ids, not unknowns.

        Teeth: dropping the "claude-" catch-all from _is_recognized_claude
        turns this RED (they would stand in for the flagship and claim 1M).
        """
        for legacy in ("claude-2", "claude-instant-1", "claude-3-opus"):
            assert reg._is_recognized_claude(legacy) is True, legacy
            assert reg.context_window_for(legacy) == reg.DEFAULT_CONTEXT_WINDOW, legacy
            assert reg.is_large_context_model(legacy) is False, legacy
