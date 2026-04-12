"""Tests for s_gtm-report-gen skill -- generator module."""
import pytest
import json
import sys
import os

# Add skill to path
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)

from generator import load_data, load_product_knowledge, generate


def _make_summary_data() -> dict:
    """Create minimal valid summary data for testing."""
    return {
        "bu_name": "AUTO & MFG",
        "product": "agentcore",
        "total_accounts": 10,
        "total_ttm": 1000000,
        "total_genai": 50000,
        "total_bedrock": 20000,
        "auto_count": 4,
        "mfg_count": 6,
        "top_accounts": [
            {"name": "TestCo", "ttm": 500000, "genai": 25000, "bedrock": 10000,
             "category": "消费电子"}
        ],
        "bedrock_accounts": [
            {"name": "TestCo", "bedrock": 10000, "genai": 25000, "ttm": 500000,
             "category": "消费电子"}
        ],
        "categories": {
            "消费电子": {
                "count": 5, "ttm": 600000, "genai": 30000, "bedrock": 15000,
                "industry": "MFG", "xxl": [], "xl": ["TestCo"], "accounts": ["TestCo"],
            }
        },
        "plays": [],
        "scenarios": [],
        "competitive": [],
    }


class TestLoadData:
    """Data loading from JSON file."""

    def test_loads_valid_json(self, tmp_path):
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps({"bu_name": "TEST", "total_accounts": 5}))
        result = load_data(str(data_file))
        assert result["bu_name"] == "TEST"
        assert result["total_accounts"] == 5

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_data("/nonexistent/path/data.json")

    def test_raises_on_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")
        with pytest.raises(json.JSONDecodeError):
            load_data(str(bad_file))


class TestLoadProductKnowledge:
    """Product knowledge loading."""

    def test_loads_agentcore_from_sibling(self):
        # Should find agentcore.md in s_industry-gtm-analysis/knowledge
        components = load_product_knowledge("agentcore")
        assert isinstance(components, list)
        assert len(components) > 0

    def test_returns_empty_for_unknown_product(self):
        components = load_product_knowledge("nonexistent_product_xyz")
        assert components == []

    def test_components_have_required_fields(self):
        components = load_product_knowledge("agentcore")
        if components:
            c = components[0]
            assert "name" in c
            assert "desc" in c
            assert "use_case" in c


class TestGenerate:
    """Report generation dispatch."""

    def test_generates_rob_summary(self, tmp_path):
        data = _make_summary_data()
        outputs = generate(data, template="rob", output_dir=str(tmp_path))
        assert "rob" in outputs
        assert os.path.exists(outputs["rob"])
        with open(outputs["rob"]) as f:
            html = f.read()
        assert "<!DOCTYPE html>" in html

    def test_generates_gm_detailed(self, tmp_path):
        data = _make_summary_data()
        outputs = generate(data, template="gm", output_dir=str(tmp_path))
        assert "gm" in outputs
        assert os.path.exists(outputs["gm"])
        with open(outputs["gm"]) as f:
            html = f.read()
        assert "<!DOCTYPE html>" in html

    def test_generates_all_templates(self, tmp_path):
        data = _make_summary_data()
        outputs = generate(data, template="all", output_dir=str(tmp_path))
        assert "rob" in outputs
        assert "gm" in outputs
        # Excel only generated if accounts key present
        assert "excel" not in outputs  # no accounts in summary data

    def test_generates_excel_with_accounts(self, tmp_path):
        data = _make_summary_data()
        data["accounts"] = [
            {"short": "TestCo", "name": "Test Company", "website": "test.com",
             "size": "XL", "owner": "Owner", "category": "消费电子",
             "ttm": 500000, "genai": 25000, "bedrock": 10000,
             "products": "Test", "ai_scenarios": "AI test",
             "openclaw": "No", "maturity": "Medium", "gtm": "",
             "sfdc_url": "", "industry": "MFG"}
        ]
        outputs = generate(data, template="excel", output_dir=str(tmp_path))
        assert "excel" in outputs
        assert os.path.exists(outputs["excel"])

    def test_skips_excel_without_accounts(self, tmp_path):
        data = _make_summary_data()
        outputs = generate(data, template="excel", output_dir=str(tmp_path))
        assert "excel" not in outputs

    def test_creates_output_directory(self, tmp_path):
        data = _make_summary_data()
        out = str(tmp_path / "new" / "dir")
        generate(data, template="rob", output_dir=out)
        assert os.path.isdir(out)

    def test_product_override(self, tmp_path):
        data = _make_summary_data()
        generate(data, template="rob", product="bedrock", output_dir=str(tmp_path))
        with open(str(tmp_path / "rob_summary.html")) as f:
            html = f.read()
        assert "bedrock" in html.lower()


class TestSkillMd:
    """SKILL.md validation."""

    def test_skill_md_exists(self):
        skill_md = os.path.join(SKILL_DIR, "SKILL.md")
        assert os.path.exists(skill_md)

    def test_skill_md_has_trigger(self):
        skill_md = os.path.join(SKILL_DIR, "SKILL.md")
        with open(skill_md) as f:
            content = f.read()
        assert "GTM" in content or "gtm" in content
        assert "TRIGGER" in content
