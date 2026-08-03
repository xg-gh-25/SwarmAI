"""Tests for Pollinate structural validator.

Verifies:
- AC8: pollinate_validator.py exists with >= 5 structural invariant checks
"""
from pathlib import Path



def _make_valid_content_dir(tmp_path: Path) -> Path:
    """Create a content dir that passes all checks."""
    content_dir = tmp_path / "content" / "test-topic"
    content_dir.mkdir(parents=True)

    # Platform matrix
    (content_dir / "platform_matrix.md").write_text(
        "## Platform Matrix\n| Platform | Format | Dimensions |\n"
        "| 小红书 | PNG | 1080x1440 |\n| LinkedIn | PNG | 1080x1080 |\n"
    )

    # QR code
    (content_dir / "qr-swarmai.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # GitHub link in delivery text
    (content_dir / "delivery.md").write_text(
        "## Delivery\nGitHub: https://github.com/xg-gh-25/SwarmAI\n"
    )

    # 2 variants per format
    tracks_dir = content_dir / "tracks" / "poster"
    tracks_dir.mkdir(parents=True)
    (tracks_dir / "variant-a.html").write_text("<html>A</html>")
    (tracks_dir / "variant-b.html").write_text("<html>B</html>")

    # Output files with correct extensions
    (tracks_dir / "variant-a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    (tracks_dir / "variant-b.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    return content_dir


class TestPollinateValidatorExists:
    """AC8: pollinate_validator.py exists with >= 5 checks."""

    def test_import(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery
        assert callable(validate_delivery)

    def test_returns_structured_result(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        result = validate_delivery(str(content_dir))

        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert "checks_passed" in result
        assert "checks_total" in result
        assert result["checks_total"] >= 5


class TestAllChecksPassing:
    """Full valid content dir passes all checks."""

    def test_valid_dir_passes(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        result = validate_delivery(str(content_dir))
        assert result["valid"] is True
        assert result["errors"] == []


class TestIndividualChecks:
    """Each check detects its specific missing element."""

    def test_missing_platform_matrix(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "platform_matrix.md").unlink()
        result = validate_delivery(str(content_dir))
        assert any("platform" in e.lower() for e in result["errors"])

    def test_missing_qr_code(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "qr-swarmai.png").unlink()
        result = validate_delivery(str(content_dir))
        assert any("qr" in e.lower() for e in result["errors"])

    def test_missing_github_link(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        (content_dir / "delivery.md").write_text("## Delivery\nNo link here\n")
        result = validate_delivery(str(content_dir))
        assert any("github" in e.lower() for e in result["errors"])

    def test_missing_variants(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
        from pollinate_validator import validate_delivery

        content_dir = _make_valid_content_dir(tmp_path)
        # Remove one variant — should have < 2
        tracks = content_dir / "tracks" / "poster"
        (tracks / "variant-b.html").unlink()
        (tracks / "variant-b.png").unlink()
        result = validate_delivery(str(content_dir))
        assert any("variant" in e.lower() for e in result["errors"])


def _import_validator():
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts"))
    import pollinate_validator
    return pollinate_validator


def _make_multitrack_dir(tmp_path: Path, tracks: dict) -> Path:
    """Create a content dir with the base valid scaffold + arbitrary track dirs.

    `tracks` maps a tracks/ subdir name → list of (filename, content) pairs.
    Reuses _make_valid_content_dir's poster scaffold then adds the extra dirs.
    """
    content_dir = _make_valid_content_dir(tmp_path)
    for dirname, files in tracks.items():
        td = content_dir / "tracks" / dirname
        td.mkdir(parents=True, exist_ok=True)
        for fname, body in files:
            (td / fname).write_text(body)
    return content_dir


class TestCheck7BrandConsistency:
    """Check 7 (RP-X1): all tracks share the same --accent, else FAIL. SKIP if <2."""

    def test_consistent_accent_passes(self):
        v = _import_validator()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {
                "deck": [("out.html", "<style>--accent: #2b6cee;</style>")],
                "html-deck": [("out.html", "<style>--accent:#2b6cee;</style>")],
            })
            r = v.check_brand_consistency(root)
            assert r["status"] == "PASS", r

    def test_inconsistent_accent_warns_not_blocks(self):
        # Gate-2 MEDIUM: brand-accent is a POLICY heuristic (intentional theming
        # is legal), so divergence WARNs — it must NOT hard-block delivery.
        v = _import_validator()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {
                "deck": [("out.html", "<style>--accent: #2b6cee;</style>")],
                "html-deck": [("out.html", "<style>--accent: #ff0000;</style>")],
            })
            r = v.check_brand_consistency(root)
            assert r["status"] == "WARN", r
            # WARN must NOT flip valid=False; it surfaces as a warning
            full = v.validate_delivery(str(root))
            assert full["valid"] is True, full
            assert any("brand-token" in w for w in full["warnings"])

    def test_single_track_skips(self):
        v = _import_validator()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {
                "deck": [("out.html", "<style>--accent: #2b6cee;</style>")],
            })
            # poster (from scaffold) has no --accent → only 1 track with accent → SKIP
            r = v.check_brand_consistency(root)
            assert r["status"] == "SKIP", r


class TestCheck8ProducedSubset:
    """Check 8 (AC2-b): produced tracks ⊆ confirmed_tracks. SKIP if no discovery.json."""

    def test_subset_passes_with_token_mapping(self):
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            # confirmed uses TOKENS (html_deck, one_pager); produced uses DIR names
            # (html-deck, pdf) — must pass via _TOKEN_TO_DIR mapping, not raw ==
            root = _make_multitrack_dir(Path(d), {
                "html-deck": [("a.html", "x")],
                "pdf": [("a.pdf", "x")],
            })
            (root / "discovery.json").write_text(_json.dumps(
                {"confirmed_tracks": ["poster", "html_deck", "one_pager"]}))
            r = v.check_produced_subset(root)
            assert r["status"] == "PASS", r

    def test_unconfirmed_track_fails(self):
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {"video": [("a.mp4", "x")]})
            (root / "discovery.json").write_text(_json.dumps(
                {"confirmed_tracks": ["poster"]}))  # video NOT confirmed
            r = v.check_produced_subset(root)
            assert r["status"] == "FAIL", r
            assert "video" in r["detail"]
            full = v.validate_delivery(str(root))
            assert full["valid"] is False

    def test_no_discovery_skips(self):
        v = _import_validator()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {"video": [("a.mp4", "x")]})
            # no discovery.json → legacy/fast-path → must not block
            r = v.check_produced_subset(root)
            assert r["status"] == "SKIP", r


class TestCheck9TrackSetDrift:
    """Check 9 (AC2-a): production_tracks == confirmed_tracks. SKIP unless BOTH present."""

    def test_matching_sets_pass(self):
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_valid_content_dir(Path(d))
            (root / "discovery.json").write_text(_json.dumps(
                {"confirmed_tracks": ["poster", "narrative"]}))
            (root / "strategy.json").write_text(_json.dumps(
                {"production_tracks": ["narrative", "poster"]}))  # order-independent
            r = v.check_track_set_drift(root)
            assert r["status"] == "PASS", r

    def test_drift_fails(self):
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_valid_content_dir(Path(d))
            (root / "discovery.json").write_text(_json.dumps(
                {"confirmed_tracks": ["poster"]}))
            (root / "strategy.json").write_text(_json.dumps(
                {"production_tracks": ["poster", "narrative"]}))  # drift
            r = v.check_track_set_drift(root)
            assert r["status"] == "FAIL", r
            full = v.validate_delivery(str(root))
            assert full["valid"] is False

    def test_only_one_json_skips(self):
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_valid_content_dir(Path(d))
            # only strategy.json (legacy content, no discovery.json) → SKIP
            (root / "strategy.json").write_text(_json.dumps(
                {"production_tracks": ["poster", "narrative"]}))
            r = v.check_track_set_drift(root)
            assert r["status"] == "SKIP", r


class TestCheckCountUpdated:
    """The 3 new invariants bumped checks_total 6 → 9."""

    def test_checks_total_is_nine(self, tmp_path):
        v = _import_validator()
        result = v.validate_delivery(str(_make_valid_content_dir(tmp_path)))
        assert result["checks_total"] == 9
        # base scaffold has no accent/discovery/strategy → checks 7-9 SKIP → still valid
        assert result["valid"] is True


class TestGate2Regressions:
    """Regressions for the 4 Gate-2 findings (run_be232a07) — each was a real
    false-block or silent-bypass the happy-path tests missed."""

    def test_CRITICAL_non_dict_json_does_not_crash(self):
        # Top-level non-dict JSON (bare list) must SKIP, NOT raise AttributeError
        # (which artifact_cli's bare except would swallow → silent full-gate bypass).
        v = _import_validator()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {"video": [("a.mp4", "x")]})
            (root / "discovery.json").write_text('["poster"]')  # valid JSON, not a dict
            # must not raise
            r = v.check_produced_subset(root)
            assert r["status"] == "SKIP", r
            # and the whole validator must still run (not crash out)
            full = v.validate_delivery(str(root))
            assert "valid" in full

    def test_HIGH_empty_confirmed_list_skips_not_blocks(self):
        # confirmed_tracks: [] must be treated as absent → SKIP, not "zero allowed".
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {"poster": [("a.png", "x")]})
            (root / "discovery.json").write_text(_json.dumps({"confirmed_tracks": []}))
            r = v.check_produced_subset(root)
            assert r["status"] == "SKIP", r

    def test_HIGH_non_track_subdir_ignored(self):
        # A non-track subdir (_scratch/staging) under tracks/ must NOT be flagged
        # as an unconfirmed track.
        v = _import_validator()
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as d:
            root = _make_multitrack_dir(Path(d), {
                "poster": [("a.png", "x")],
                "_scratch": [("tmp.txt", "x")],   # non-track artifact dir
            })
            (root / "discovery.json").write_text(_json.dumps({"confirmed_tracks": ["poster"]}))
            r = v.check_produced_subset(root)
            assert r["status"] == "PASS", r  # _scratch ignored, poster confirmed
