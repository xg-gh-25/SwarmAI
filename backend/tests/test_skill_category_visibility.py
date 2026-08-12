"""Tests for skill category + visibility derivation and run-mode-keyed internal filtering.

Covers the Capabilities-domain backend contract (run_b5d98151):
- Every skill resolves to a KNOWN category or the Utilities fallback (coverage — no skill vanishes).
- visibility is "internal" for the internal-prefix set, "public" otherwise.
- Frontmatter `category:`/`visibility:` override the derived values.
- GET /api/skills is run-mode-keyed (Gate-1 adopted, backend-primary, fail-closed):
  internal skills are served ONLY in local-desktop run modes (daemon/subprocess/dev),
  and OMITTED for hive/unknown (fail-safe → public-only).

Key invariant (the security one): a non-owner (hive/unknown) session NEVER receives an
internal skill in the /api/skills payload.
"""
import pytest

from core.skill_registry import derive_category, derive_visibility


class TestDeriveCategory:
    def test_known_skill_maps_to_its_category(self):
        # folder_name carries the s_ prefix; derivation strips it before lookup
        assert derive_category("s_deep-research") == "Research"
        assert derive_category("s_narrative-writing") == "Writing"

    def test_unmapped_skill_falls_to_utilities(self):
        assert derive_category("s_some-brand-new-skill-xyz") == "Utilities"

    def test_internal_prefix_maps_to_internal_category(self):
        assert derive_category("s_cmhk-weekly-report") == "Internal"
        assert derive_category("s_ivt-seed-users") == "Internal"
        assert derive_category("s_internal-brazil") == "Internal"

    def test_frontmatter_override_wins(self):
        assert derive_category("s_deep-research", frontmatter_category="Content") == "Content"

    def test_none_or_empty_never_crashes(self):
        assert derive_category("") == "Utilities"


class TestDeriveVisibility:
    def test_public_by_default(self):
        assert derive_visibility("s_deep-research") == "public"

    def test_internal_prefixes(self):
        assert derive_visibility("s_cmhk-account-360") == "internal"
        assert derive_visibility("s_ivt-oppty-gap-report") == "internal"
        assert derive_visibility("s_internal-crux-cr") == "internal"
        assert derive_visibility("s_meddpicc-scorecard") == "internal"

    def test_frontmatter_override_wins(self):
        assert derive_visibility("s_deep-research", frontmatter_visibility="internal") == "internal"
        assert derive_visibility("s_cmhk-weekly-report", frontmatter_visibility="public") == "public"

    def test_missing_never_crashes(self):
        assert derive_visibility("") == "public"


class TestEndpointContract:
    """GET /api/skills returns the new fields and is run-mode-keyed (backend-primary)."""

    def test_list_returns_category_and_visibility(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        for item in data:
            assert "category" in item and item["category"]
            assert item["visibility"] in ("public", "internal")

    def test_owner_desktop_mode_includes_internal(self, client, monkeypatch):
        # daemon = local desktop owner → internal skills are served
        monkeypatch.setenv("SWARMAI_MODE", "daemon")
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        names = [i["folder_name"] for i in resp.json()]
        internal = [n for n in names if n.startswith(("s_cmhk-", "s_ivt-", "s_internal-"))]
        assert internal, "owner desktop must see internal skills"

    def test_hive_mode_omits_internal(self, client, monkeypatch):
        # hive = remote multi-viewer → internal skills MUST be omitted (fail-closed)
        monkeypatch.setenv("SWARMAI_MODE", "hive")
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        for item in resp.json():
            assert item["visibility"] == "public", (
                f"{item['folder_name']} leaked internal skill on hive"
            )

    def test_unknown_mode_fails_closed_to_public(self, client, monkeypatch):
        monkeypatch.setenv("SWARMAI_MODE", "something-weird")
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        assert all(i["visibility"] == "public" for i in resp.json())

    def _an_internal_folder(self, client, monkeypatch) -> str:
        # Discover a real internal skill folder name via the owner listing.
        monkeypatch.setenv("SWARMAI_MODE", "daemon")
        internal = [i["folder_name"] for i in client.get("/api/skills").json()
                    if i["visibility"] == "internal"]
        assert internal, "expected at least one internal skill in the registry"
        return internal[0]

    def test_detail_endpoint_omits_internal_for_non_owner(self, client, monkeypatch):
        # LEAK GUARD: GET /api/skills/{folder} must NOT serve internal content to a
        # non-owner (the detail endpoint is a SEPARATE leak surface from the list).
        folder = self._an_internal_folder(client, monkeypatch)
        monkeypatch.setenv("SWARMAI_MODE", "hive")
        resp = client.get(f"/api/skills/{folder}")
        assert resp.status_code == 404, "internal skill detail must 404 for non-owner (not leak content)"

    def test_detail_endpoint_serves_internal_for_owner(self, client, monkeypatch):
        folder = self._an_internal_folder(client, monkeypatch)
        monkeypatch.setenv("SWARMAI_MODE", "daemon")
        resp = client.get(f"/api/skills/{folder}")
        assert resp.status_code == 200
        assert resp.json()["visibility"] == "internal"

    def test_rescan_omits_internal_for_non_owner(self, client, monkeypatch):
        # LEAK GUARD: the rescan list path must apply the SAME filter as list.
        monkeypatch.setenv("SWARMAI_MODE", "hive")
        resp = client.post("/api/skills/rescan")
        assert resp.status_code == 200
        assert all(i["visibility"] == "public" for i in resp.json()), (
            "rescan leaked internal skills to a non-owner"
        )


class TestCreateInternalNameGuard:
    """Creating a skill with an internal-derived name is rejected uniformly (400),
    closing the 409-enumeration leak + preventing a self-invisible user skill."""

    def test_create_internal_prefix_rejected_400(self, client):
        resp = client.post("/api/skills", json={
            "folder_name": "cmhk-my-custom",
            "name": "cmhk-my-custom",
            "description": "x",
            "content": "# x",
        })
        assert resp.status_code == 400, "internal-prefix create must be rejected"
        assert "already exists" not in resp.text.lower(), "must NOT leak existence (enumeration)"

    def test_public_name_passes_the_internal_guard(self):
        # Unit-level (no HTTP create → no on-disk side effect): a public name does
        # NOT trip _reject_internal_folder_name; an internal-prefix name does.
        from routers.skills import _reject_internal_folder_name
        from fastapi import HTTPException
        _reject_internal_folder_name("my-ordinary-skill-xyz")  # no raise
        with pytest.raises(HTTPException) as exc:
            _reject_internal_folder_name("cmhk-probe")
        assert exc.value.status_code == 400


class TestFullRegistryCoverage:
    """No skill vanishes — every real skill resolves to a non-empty category."""

    def test_every_skill_resolves_to_a_category(self):
        import asyncio
        from core.skill_manager import skill_manager

        # asyncio.run (fresh loop) — get_event_loop() raises on Py3.12 when a prior
        # test cleared the thread-default loop (test_community_api set_event_loop(None)).
        cache = asyncio.run(skill_manager.get_cache())
        assert len(cache) > 0, "expected a non-empty skill cache"
        for folder_name in cache:
            cat = derive_category(folder_name)
            assert cat and isinstance(cat, str), f"{folder_name} resolved to empty category"
