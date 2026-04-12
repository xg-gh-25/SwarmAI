"""Tests for s_customer-ai-research skill — researcher module."""
import pytest
import json
import sys
import os
from datetime import datetime, timedelta

# Add skill to path
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_DIR)

from researcher import (
    load_cache,
    save_cache,
    is_stale,
    build_search_queries,
    parse_research_result,
    compile_report,
)


class TestCacheStale:
    """Cache staleness detection."""

    def test_nonexistent_file_is_stale(self, tmp_path):
        assert is_stale(str(tmp_path / "nonexistent.json")) is True

    def test_fresh_cache_is_not_stale(self, tmp_path):
        cache_file = tmp_path / "test.json"
        data = {"_cached_at": datetime.now().isoformat(), "company": "TestCo"}
        cache_file.write_text(json.dumps(data))
        assert is_stale(str(cache_file), max_age_days=30) is False

    def test_old_cache_is_stale(self, tmp_path):
        cache_file = tmp_path / "test.json"
        old_date = (datetime.now() - timedelta(days=31)).isoformat()
        data = {"_cached_at": old_date, "company": "TestCo"}
        cache_file.write_text(json.dumps(data))
        assert is_stale(str(cache_file), max_age_days=30) is True

    def test_missing_timestamp_is_stale(self, tmp_path):
        cache_file = tmp_path / "test.json"
        cache_file.write_text(json.dumps({"company": "TestCo"}))
        assert is_stale(str(cache_file)) is True

    def test_invalid_json_is_stale(self, tmp_path):
        cache_file = tmp_path / "test.json"
        cache_file.write_text("not json")
        assert is_stale(str(cache_file)) is True

    def test_custom_max_age(self, tmp_path):
        cache_file = tmp_path / "test.json"
        # 5 days old
        old_date = (datetime.now() - timedelta(days=5)).isoformat()
        data = {"_cached_at": old_date}
        cache_file.write_text(json.dumps(data))
        assert is_stale(str(cache_file), max_age_days=3) is True
        assert is_stale(str(cache_file), max_age_days=7) is False


class TestCacheSaveLoad:
    """Cache save and load round-trip."""

    def test_save_creates_file(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        path = save_cache("TestCompany", {"ai_products": "GPT-4"}, cache_dir)
        assert os.path.exists(path)

    def test_save_load_roundtrip(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        original = {"ai_products": "Agent Platform", "maturity": "High"}
        save_cache("TestCo", original, cache_dir)
        loaded = load_cache("TestCo", cache_dir)
        assert loaded is not None
        assert loaded["ai_products"] == "Agent Platform"
        assert loaded["maturity"] == "High"

    def test_load_returns_none_for_missing(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        os.makedirs(cache_dir, exist_ok=True)
        result = load_cache("NonExistent", cache_dir)
        assert result is None

    def test_save_adds_timestamp(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        save_cache("TimestampCo", {"data": 1}, cache_dir)
        loaded = load_cache("TimestampCo", cache_dir)
        assert "_cached_at" in loaded
        # Should be parseable as ISO datetime
        datetime.fromisoformat(loaded["_cached_at"])

    def test_save_handles_chinese_names(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        path = save_cache("理想汽车", {"ai_products": "智能驾驶"}, cache_dir)
        assert os.path.exists(path)
        loaded = load_cache("理想汽车", cache_dir)
        assert loaded is not None
        assert loaded["ai_products"] == "智能驾驶"

    def test_load_stale_returns_none(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        # Save, then manually set old timestamp
        path = save_cache("OldCo", {"data": 1}, cache_dir)
        with open(path) as f:
            data = json.load(f)
        data["_cached_at"] = (datetime.now() - timedelta(days=60)).isoformat()
        with open(path, "w") as f:
            json.dump(data, f)
        result = load_cache("OldCo", cache_dir, max_age_days=30)
        assert result is None


class TestBuildSearchQueries:
    """Query generation."""

    def test_generates_queries(self):
        queries = build_search_queries("理想汽车")
        assert isinstance(queries, list)
        assert len(queries) >= 5

    def test_queries_contain_company_name(self):
        queries = build_search_queries("TP-LINK")
        for q in queries:
            assert "TP-LINK" in q

    def test_includes_chinese_and_english(self):
        queries = build_search_queries("比亚迪")
        has_chinese = any("场景" in q or "智能体" in q for q in queries)
        has_english = any("agent" in q.lower() for q in queries)
        assert has_chinese
        assert has_english

    def test_industry_adds_extra_queries(self):
        without = build_search_queries("TestCo")
        with_industry = build_search_queries("TestCo", industry="automotive AI agent")
        assert len(with_industry) > len(without)

    def test_industry_queries_contain_industry(self):
        queries = build_search_queries("TestCo", industry="smart home IoT")
        industry_queries = [q for q in queries if "smart home IoT" in q]
        assert len(industry_queries) >= 1


class TestParseResearchResult:
    """Result parsing from raw text."""

    def test_empty_text_returns_defaults(self):
        result = parse_research_result("", "TestCo")
        assert result["company"] == "TestCo"
        assert result["maturity"] == "Unknown"

    def test_detects_ai_products(self):
        raw = "The company launched its AI Agent platform in 2025.\nNew copilot features."
        result = parse_research_result(raw, "TestCo")
        assert result["ai_products"] != ""

    def test_detects_agent_scenarios(self):
        raw = "Company uses autonomous agents for workflow automation."
        result = parse_research_result(raw, "TestCo")
        assert result["agent_scenarios"] != ""

    def test_detects_openclaw(self):
        raw = "The company adopted OpenClaw framework for its agent platform."
        result = parse_research_result(raw, "TestCo")
        assert result["openclaw_usage"] == "Detected"

    def test_no_openclaw(self):
        raw = "The company uses a custom framework."
        result = parse_research_result(raw, "TestCo")
        assert result["openclaw_usage"] == "Not detected"

    def test_detects_cloud_provider_aws(self):
        raw = "Running on AWS with Bedrock foundation models."
        result = parse_research_result(raw, "TestCo")
        assert "aws" in result["cloud_provider"]

    def test_detects_multiple_clouds(self):
        raw = "Uses AWS Bedrock and also Azure OpenAI for different workloads."
        result = parse_research_result(raw, "TestCo")
        assert "aws" in result["cloud_provider"]
        assert "azure" in result["cloud_provider"]

    def test_maturity_high(self):
        raw = "The AI agent system is deployed in production at scale."
        result = parse_research_result(raw, "TestCo")
        assert result["maturity"] == "High"

    def test_maturity_medium(self):
        raw = "Currently running a pilot program for AI agents."
        result = parse_research_result(raw, "TestCo")
        assert result["maturity"] == "Medium"

    def test_maturity_low(self):
        raw = "The company is planning to evaluate AI agent solutions."
        result = parse_research_result(raw, "TestCo")
        assert result["maturity"] == "Low"

    def test_chinese_content(self):
        raw = "该公司已上线智能体平台，基于阿里云通义大模型。"
        result = parse_research_result(raw, "测试公司")
        assert result["company"] == "测试公司"
        assert result["maturity"] == "High"  # 已上线
        assert "alibaba" in result["cloud_provider"]


class TestCompileReport:
    """Report compilation."""

    def test_creates_output_files(self, tmp_path):
        results = [
            {"company": "TestCo", "ai_products": "Agent Platform",
             "agent_scenarios": "Automation", "openclaw_usage": "Detected",
             "cloud_provider": "aws", "maturity": "High"},
        ]
        output = compile_report(results, str(tmp_path / "output"))
        assert os.path.exists(output["json"])
        assert os.path.exists(output["markdown"])

    def test_json_contains_all_results(self, tmp_path):
        results = [
            {"company": "A", "maturity": "High"},
            {"company": "B", "maturity": "Low"},
        ]
        output = compile_report(results, str(tmp_path / "output"))
        with open(output["json"]) as f:
            loaded = json.load(f)
        assert len(loaded) == 2

    def test_markdown_has_table(self, tmp_path):
        results = [
            {"company": "TestCo", "ai_products": "X", "agent_scenarios": "Y",
             "openclaw_usage": "No", "cloud_provider": "aws", "maturity": "High"},
        ]
        output = compile_report(results, str(tmp_path / "output"))
        with open(output["markdown"]) as f:
            md = f.read()
        assert "| #" in md
        assert "TestCo" in md
        assert "Maturity Distribution" in md

    def test_empty_results(self, tmp_path):
        output = compile_report([], str(tmp_path / "output"))
        assert os.path.exists(output["json"])
        assert os.path.exists(output["markdown"])

    def test_creates_output_directory(self, tmp_path):
        out_dir = str(tmp_path / "new" / "nested" / "dir")
        compile_report([], out_dir)
        assert os.path.isdir(out_dir)
