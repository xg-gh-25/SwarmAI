"""Unit tests for the generic dict-based Settings API router.

Tests cover the generic pass-through contract introduced by the
generic-settings-pipeline spec:

- **GET /api/settings** — returns all DEFAULT_CONFIG keys minus SECRET_KEYS,
  plus credential status fields; no Pydantic response model.
- **PUT /api/settings** — partial dict merge via WRITABLE_KEYS whitelist;
  unknown and secret keys silently discarded.
- **Validation** — ``default_model`` must be in ``available_models``;
  auto-reset when list changes; ``anthropic_base_url`` empty-string clearing.
- **Extensibility** — monkey-patching a new key into DEFAULT_CONFIG makes it
  available via GET and PUT with zero router changes.

Fixture strategy: each test gets a standalone FastAPI app with ONLY the
settings router mounted (no ``from main import app``).  A ``tmp_path``-backed
``AppConfigManager`` is injected via ``set_config_manager()``.  Both
credential probes are mocked globally to avoid real AWS / env-var side effects.
"""

import pytest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.settings import router, set_config_manager
from core.app_config_manager import AppConfigManager, DEFAULT_CONFIG, SECRET_KEYS


# ---------------------------------------------------------------------------
# Expected response keys (used across multiple test classes)
# ---------------------------------------------------------------------------

_EXPECTED_CONFIG_KEYS = {
    k for k in DEFAULT_CONFIG if k not in SECRET_KEYS
}
_CREDENTIAL_STATUS_KEYS = {"aws_credentials_configured", "anthropic_api_key_configured"}
_ALL_EXPECTED_KEYS = _EXPECTED_CONFIG_KEYS | _CREDENTIAL_STATUS_KEYS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_probes():
    """Mock credential probes globally — default both to False.

    Individual tests can override via nested ``patch`` context managers
    when they need True values.
    """
    with patch("routers.settings._probe_aws_credentials", return_value=False), \
         patch("routers.settings._probe_anthropic_api_key", return_value=False):
        yield


@pytest.fixture()
def _isolated_config(tmp_path):
    """Provide a fresh AppConfigManager backed by a temp dir.

    Injects the manager into the settings router via ``set_config_manager``
    and tears it down after the test.
    """
    cfg_path = tmp_path / "config.json"
    mgr = AppConfigManager(config_path=cfg_path)
    mgr.load()
    set_config_manager(mgr)
    yield mgr
    set_config_manager(None)


@pytest.fixture()
def client(_isolated_config) -> TestClient:
    """Standalone TestClient with ONLY the settings router mounted."""
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")
    with TestClient(app) as tc:
        yield tc


# ===================================================================
# Task 4.2 — Generic GET contract
# ===================================================================


class TestGenericGETContract:
    """Tests for GET /api/settings generic dict response."""

    def test_response_contains_all_expected_keys(self, client: TestClient):
        """Response has every DEFAULT_CONFIG key (minus secrets) plus credential status."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == _ALL_EXPECTED_KEYS

    def test_no_secret_keys_in_response(self, client: TestClient):
        """No SECRET_KEYS appear in the GET response."""
        resp = client.get("/api/settings")
        data = resp.json()
        for secret in SECRET_KEYS:
            assert secret not in data, f"Secret key '{secret}' leaked into response"

    def test_credential_status_reflects_mocked_probes_false(self, client: TestClient):
        """Credential status fields are False when probes return False (default mock)."""
        resp = client.get("/api/settings")
        data = resp.json()
        assert data["aws_credentials_configured"] is False
        assert data["anthropic_api_key_configured"] is False

    def test_credential_status_reflects_mocked_probes_true(self, client: TestClient):
        """Credential status fields are True when probes return True."""
        with patch("routers.settings._probe_aws_credentials", return_value=True), \
             patch("routers.settings._probe_anthropic_api_key", return_value=True):
            resp = client.get("/api/settings")
            data = resp.json()
            assert data["aws_credentials_configured"] is True
            assert data["anthropic_api_key_configured"] is True

    def test_get_reflects_prior_put(self, client: TestClient):
        """GET returns values written by a prior PUT."""
        client.put("/api/settings", json={"aws_region": "eu-west-1"})
        resp = client.get("/api/settings")
        assert resp.json()["aws_region"] == "eu-west-1"


# ===================================================================
# Task 4.3 — Generic PUT contract
# ===================================================================


class TestGenericPUTContract:
    """Tests for PUT /api/settings generic dict merge."""

    def test_partial_update_preserves_other_defaults(self, client: TestClient):
        """Updating one field leaves all other defaults intact."""
        resp = client.put("/api/settings", json={"aws_region": "ap-southeast-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["aws_region"] == "ap-southeast-1"
        # Other fields unchanged from DEFAULT_CONFIG
        assert data["use_bedrock"] == DEFAULT_CONFIG["use_bedrock"]
        assert data["default_model"] == DEFAULT_CONFIG["default_model"]

    def test_empty_body_is_noop(self, client: TestClient):
        """PUT with empty body {} is a no-op returning current config."""
        resp = client.put("/api/settings", json={})
        assert resp.status_code == 200
        data = resp.json()
        # All keys present, values match defaults
        assert set(data.keys()) == _ALL_EXPECTED_KEYS
        assert data["aws_region"] == DEFAULT_CONFIG["aws_region"]

    def test_unknown_keys_silently_discarded(self, client: TestClient):
        """Keys not in DEFAULT_CONFIG are silently ignored, not persisted."""
        resp = client.put("/api/settings", json={
            "aws_region": "us-west-2",
            "totally_unknown_key": "should_vanish",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "totally_unknown_key" not in data
        assert data["aws_region"] == "us-west-2"

    def test_secret_keys_silently_discarded(self, client: TestClient):
        """SECRET_KEYS in PUT body are silently discarded."""
        resp = client.put("/api/settings", json={
            "aws_region": "us-west-2",
            "aws_access_key_id": "AKIA_SHOULD_BE_IGNORED",
            "anthropic_api_key": "sk-secret",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "aws_access_key_id" not in data
        assert "anthropic_api_key" not in data
        assert data["aws_region"] == "us-west-2"

    def test_anthropic_base_url_empty_string_clears_to_none(self, client: TestClient):
        """Empty string for anthropic_base_url clears it to None."""
        # Set a value first
        client.put("/api/settings", json={"anthropic_base_url": "https://proxy.example.com"})
        # Clear it
        resp = client.put("/api/settings", json={"anthropic_base_url": ""})
        assert resp.status_code == 200
        assert resp.json()["anthropic_base_url"] is None

    def test_default_model_not_in_available_models_returns_400(self, client: TestClient):
        """400 when default_model is not in available_models."""
        resp = client.put("/api/settings", json={
            "available_models": ["model-a", "model-b"],
            "default_model": "model-c",
        })
        assert resp.status_code == 400
        assert "default_model" in resp.json()["detail"].lower()

    def test_auto_reset_default_model_when_available_models_changes(
        self, client: TestClient,
    ):
        """default_model auto-resets to first model when no longer in list."""
        # Set initial state
        client.put("/api/settings", json={
            "available_models": ["model-a", "model-b"],
            "default_model": "model-b",
        })
        # Change available_models so model-b is gone
        resp = client.put("/api/settings", json={
            "available_models": ["model-x", "model-y"],
        })
        assert resp.status_code == 200
        assert resp.json()["default_model"] == "model-x"

    def test_default_model_preserved_when_still_in_new_available_models(
        self, client: TestClient,
    ):
        """default_model is NOT reset when it's still in the new list."""
        client.put("/api/settings", json={
            "available_models": ["model-a", "model-b"],
            "default_model": "model-b",
        })
        resp = client.put("/api/settings", json={
            "available_models": ["model-b", "model-c"],
        })
        assert resp.status_code == 200
        assert resp.json()["default_model"] == "model-b"

    def test_wrong_type_returns_422(self, client: TestClient):
        """422 when a value has the wrong type (e.g. string for a bool field)."""
        resp = client.put("/api/settings", json={"use_bedrock": "not_a_bool"})
        assert resp.status_code == 422
        assert "use_bedrock" in resp.json()["detail"]


# ===================================================================
# Task 4.4 — New DEFAULT_CONFIG keys work without code changes
# ===================================================================


class TestExtensibility:
    """Verify that adding a key to DEFAULT_CONFIG makes it available
    via GET and PUT with zero router changes."""

    def test_new_default_config_key_appears_in_get_and_put(
        self, client: TestClient, _isolated_config, monkeypatch,
    ):
        """Monkey-patching a new key into DEFAULT_CONFIG and WRITABLE_KEYS
        makes it visible in GET and persistable via PUT."""
        import routers.settings as settings_mod
        import core.app_config_manager as acm_mod

        # Patch DEFAULT_CONFIG with a new key
        patched_defaults = {**DEFAULT_CONFIG, "brand_new_setting": 42}
        monkeypatch.setattr(acm_mod, "DEFAULT_CONFIG", patched_defaults)
        monkeypatch.setattr(settings_mod, "DEFAULT_CONFIG", patched_defaults)

        # Patch WRITABLE_KEYS to include the new key
        patched_writable = frozenset(patched_defaults.keys()) - SECRET_KEYS
        monkeypatch.setattr(settings_mod, "WRITABLE_KEYS", patched_writable)

        # GET should include the new key with its default value
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "brand_new_setting" in data
        assert data["brand_new_setting"] == 42

        # PUT should accept and persist the new key
        resp = client.put("/api/settings", json={"brand_new_setting": 99})
        assert resp.status_code == 200
        assert resp.json()["brand_new_setting"] == 99

        # Subsequent GET reflects the updated value
        resp = client.get("/api/settings")
        assert resp.json()["brand_new_setting"] == 99


# ===================================================================
# eval_judge_model validation — the gate whose ABSENCE was the root cause
# ===================================================================


class TestEvalJudgeModelValidation:
    """``eval_judge_model`` must be rejected at the boundary when unresolvable.

    Provenance: every sibling field here was validated (``default_model``,
    ``thinking_mode``, ``thinking_effort``) but this one was not, so an
    unresolvable value was silently persisted. Downstream, ``eval_runner``
    substituted a different model — meaning the CONFIGURED judge never ran and
    nothing said so. Patching the stored value alone would have guaranteed a
    re-drift; this gate is the actual root fix.

    Teeth: deleting the validation block in ``routers/settings.py`` turns
    ``test_unresolvable_short_name_rejected`` RED (it would return 200).
    """

    def test_unresolvable_short_name_rejected(self, client):
        resp = client.put("/api/settings", json={"eval_judge_model": "totally-bogus-model"})
        assert resp.status_code == 400, (
            "an unresolvable eval_judge_model must be rejected at the PUT "
            f"boundary, got {resp.status_code}: {resp.text}"
        )
        assert "eval_judge_model" in resp.text

    def test_registry_short_name_accepted(self, client):
        from model_registry import MODEL_NAMES
        resp = client.put("/api/settings", json={"eval_judge_model": MODEL_NAMES[-1]})
        assert resp.status_code == 200, resp.text

    def test_full_bedrock_id_accepted(self, client):
        """A full us.anthropic.* id is passed through by the resolver."""
        resp = client.put(
            "/api/settings",
            json={"eval_judge_model": "us.anthropic.claude-opus-4-6-v1"},
        )
        assert resp.status_code == 200, resp.text

    def test_short_name_present_only_in_config_map_accepted(self, client):
        """A deployment may map a model the registry does not know."""
        resp = client.put("/api/settings", json={
            "eval_judge_model": "custom-tier-model",
            "bedrock_model_map": {"custom-tier-model": "us.anthropic.custom-tier"},
        })
        assert resp.status_code == 200, resp.text

    def test_the_historical_bad_value_is_now_rejected(self, client):
        """The exact value that sat unresolvable in the live config."""
        resp = client.put(
            "/api/settings",
            json={"eval_judge_model": "claude-sonnet-4-20250514"},
        )
        assert resp.status_code == 400, (
            "this is the value that silently swapped the judge model in "
            "production — it must not be accepted again"
        )

    def test_empty_value_is_not_rejected(self, client):
        """Falsy means 'unset' — the resolver falls back; not a 400."""
        resp = client.put("/api/settings", json={"eval_judge_model": ""})
        assert resp.status_code == 200, resp.text

    def test_shrinking_the_map_orphans_nothing(self, client):
        """MIRROR GAP (adversarial MEDIUM, proven live): a PUT that shrinks
        bedrock_model_map without naming eval_judge_model used to return 200 and
        leave the STORED judge model unresolvable.

        The stored judge must be one that ONLY the map can resolve — a registry
        member stays resolvable after the map shrinks (correctly not a 400), so
        using one would make this test vacuous.

        Teeth: keying the validation on `"eval_judge_model" in updates` instead
        of the effective post-merge state turns this GREEN again (a 200).
        """
        seed = client.put("/api/settings", json={
            "eval_judge_model": "custom-only-model",
            "bedrock_model_map": {"custom-only-model": "us.anthropic.custom-only"},
        })
        assert seed.status_code == 200, seed.text
        # Now shrink the map so the STORED judge is no longer resolvable.
        resp = client.put("/api/settings", json={
            "bedrock_model_map": {"some-other-model": "us.anthropic.some-other"},
        })
        assert resp.status_code == 400, (
            "shrinking bedrock_model_map must not silently orphan the stored "
            f"eval_judge_model, got {resp.status_code}: {resp.text}"
        )

    def test_arn_judge_model_is_accepted(self, client):
        """config.py documents ARN passthrough — rejecting one would 400 a
        legitimate custom-model deployment (adversarial MEDIUM)."""
        resp = client.put("/api/settings", json={
            "eval_judge_model": "arn:aws:bedrock:us-east-1:123456789012:custom-model/x",
        })
        assert resp.status_code == 200, resp.text

    def test_global_prefixed_id_is_accepted(self, client):
        """model_registry._ID_PREFIXES supports global.anthropic.* too."""
        resp = client.put("/api/settings", json={
            "eval_judge_model": "global.anthropic.claude-opus-4-6-v1",
        })
        assert resp.status_code == 200, resp.text


class TestAvailableModelsInvariant:
    """``available_models`` may not be emptied — ``default_model`` would orphan.

    Teeth (adversarial MEDIUM, proven live): the auto-reset guard read
    `if new_models and ...`, so an empty list fell through as "no change" and
    returned 200 with default_model pointing at a model no longer offered.
    """

    def test_empty_available_models_rejected(self, client):
        resp = client.put("/api/settings", json={"available_models": []})
        assert resp.status_code == 400, (
            f"emptying available_models must be rejected, got {resp.status_code}"
        )

    def test_shrinking_available_models_auto_resets_default(self, client):
        from model_registry import MODEL_NAMES
        keep = MODEL_NAMES[1:]  # drop the flagship, which is the stored default
        resp = client.put("/api/settings", json={"available_models": keep})
        assert resp.status_code == 200, resp.text
        assert resp.json()["default_model"] == keep[0]


class TestLegacyConfigUpgradePath:
    """A PRE-EXISTING unresolvable judge model must not brick unrelated PUTs.

    CRITICAL (second adversarial pass, proven live): validating the effective
    post-merge state unconditionally meant a user upgrading with a legacy
    `eval_judge_model` got a 400 on ANY setting change — including a field they
    never touched — so the Settings UI could not save at all until they
    hand-edited config.json. Every Hive shipped before the seed fix was in
    exactly that state.

    The invariant still holds (an orphan never survives), but it is reached by
    SELF-HEALING on an unrelated PUT and by a hard 400 only when the PUT
    actually touches eval_judge_model or bedrock_model_map.
    """

    @pytest.fixture()
    def legacy_client(self, tmp_path) -> TestClient:
        """A client whose stored config holds an unresolvable judge model."""
        import json as _json
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(_json.dumps({
            **{k: v for k, v in DEFAULT_CONFIG.items() if k not in SECRET_KEYS},
            # The exact value the previously-shipped Hive seed wrote.
            "eval_judge_model": "claude-sonnet-4-20250514",
        }))
        mgr = AppConfigManager(config_path=cfg_path)
        mgr.load()
        set_config_manager(mgr)
        app = FastAPI()
        app.include_router(router, prefix="/api/settings")
        with TestClient(app) as tc:
            yield tc
        set_config_manager(None)

    def test_unrelated_put_succeeds_on_a_legacy_config(self, legacy_client):
        """Teeth: dropping the `_judge_fields_touched` guard turns this RED."""
        resp = legacy_client.put("/api/settings", json={"thinking_effort": "medium"})
        assert resp.status_code == 200, (
            "an unrelated PUT must not be rejected because of a PRE-EXISTING "
            f"orphan judge model, got {resp.status_code}: {resp.text}"
        )

    def test_unrelated_put_self_heals_the_orphan(self, legacy_client):
        """The orphan is repaired, not merely tolerated."""
        from model_registry import DEFAULT_JUDGE_MODEL
        resp = legacy_client.put("/api/settings", json={"thinking_effort": "medium"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["eval_judge_model"] == DEFAULT_JUDGE_MODEL

    def test_explicitly_setting_a_bad_judge_still_400s(self, legacy_client):
        """Self-healing must not weaken the gate when the field IS touched."""
        resp = legacy_client.put(
            "/api/settings", json={"eval_judge_model": "still-bogus"}
        )
        assert resp.status_code == 400, resp.text


class TestEmptyAvailableModelsCannotBeBypassed:
    """HIGH (second pass, proven live): the empty-list guard sat inside the
    auto-reset branch, gated on `"default_model" not in updates` — so
    co-sending both keys skipped it and persisted an unusable config.

    Teeth: moving the check back inside that branch turns this RED.
    """

    def test_empty_list_alone_is_rejected(self, client):
        resp = client.put("/api/settings", json={"available_models": []})
        assert resp.status_code == 400, resp.text

    def test_empty_list_WITH_default_model_is_also_rejected(self, client):
        from model_registry import FLAGSHIP_MODEL
        resp = client.put("/api/settings", json={
            "available_models": [],
            "default_model": FLAGSHIP_MODEL,
        })
        assert resp.status_code == 400, (
            "co-sending default_model must not bypass the empty-list guard, "
            f"got {resp.status_code}: {resp.text}"
        )
