#!/usr/bin/env python3
"""
Customer AI Research — caching and structuring module.

The actual web search is performed by the agent (Tavily/WebFetch).
This module handles: cache management, query building, result parsing,
and report compilation.

Usage:
    from researcher import load_cache, save_cache, build_search_queries, compile_report
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(SKILL_DIR, "knowledge", "cache")
DEFAULT_MAX_AGE_DAYS = 30


def is_stale(cache_path: str, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
    """
    Check if a cache file is stale (older than max_age_days).

    Args:
        cache_path: path to the cache JSON file
        max_age_days: maximum age in days before cache is considered stale

    Returns:
        True if the file doesn't exist or is older than max_age_days.
    """
    if not os.path.exists(cache_path):
        return True
    try:
        with open(cache_path) as f:
            data = json.load(f)
        cached_date = data.get("_cached_at", "")
        if not cached_date:
            return True
        cached_dt = datetime.fromisoformat(cached_date)
        return (datetime.now() - cached_dt) > timedelta(days=max_age_days)
    except (json.JSONDecodeError, ValueError, OSError):
        return True


def _cache_filename(customer: str, date_str: str = None) -> str:
    """Build a safe cache filename from customer name and date."""
    safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', customer.strip())
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return f"{safe_name}_{date_str}.json"


def _find_latest_cache(customer: str, cache_dir: str) -> Optional[str]:
    """Find the most recent cache file for a customer."""
    safe_name = re.sub(r'[^\w\u4e00-\u9fff-]', '_', customer.strip())
    pattern = f"{safe_name}_"
    if not os.path.isdir(cache_dir):
        return None
    matches = [
        os.path.join(cache_dir, f)
        for f in os.listdir(cache_dir)
        if f.startswith(pattern) and f.endswith(".json")
    ]
    if not matches:
        return None
    # Sort by modification time, newest first
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0]


def load_cache(customer: str, cache_dir: str = DEFAULT_CACHE_DIR,
               max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Optional[dict]:
    """
    Load cached research result for a customer.

    Args:
        customer: customer name
        cache_dir: directory containing cache files
        max_age_days: maximum cache age in days

    Returns:
        Cached result dict, or None if not found or stale.
    """
    cache_path = _find_latest_cache(customer, cache_dir)
    if cache_path is None:
        return None
    if is_stale(cache_path, max_age_days):
        return None
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(customer: str, data: dict,
               cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    """
    Save research result to cache with timestamp.

    Args:
        customer: customer name
        data: research result dict
        cache_dir: directory for cache files

    Returns:
        Path to the saved cache file.
    """
    os.makedirs(cache_dir, exist_ok=True)
    now = datetime.now()
    data["_cached_at"] = now.isoformat()
    data["_customer"] = customer
    filename = _cache_filename(customer, now.strftime("%Y-%m-%d"))
    path = os.path.join(cache_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def build_search_queries(customer_name: str,
                         industry: str = "") -> list[str]:
    """
    Generate search query strings for a customer.

    Args:
        customer_name: company name
        industry: optional industry focus (e.g., "automotive AI agent")

    Returns:
        List of search query strings (Chinese + English).
    """
    queries = [
        f"{customer_name} AI Agent 场景 2025 2026",
        f"{customer_name} 智能体 产品 agentic",
        f"{customer_name} OpenClaw 龙虾",
        f"{customer_name} AI agent product 2025 2026",
        f"{customer_name} agentic AI cloud platform",
    ]
    if industry:
        queries.extend([
            f"中国{industry} AI Agent 场景 2025",
            f"{industry} AI agent China companies",
        ])
    return queries


def parse_research_result(raw_text: str, customer_name: str) -> dict:
    """
    Extract structured fields from raw search result text.

    Looks for patterns related to AI products, agent scenarios,
    OpenClaw usage, cloud providers, and maturity level.

    Args:
        raw_text: concatenated raw text from web search results
        customer_name: the company being researched

    Returns:
        Structured dict with: company, ai_products, agent_scenarios,
        openclaw_usage, cloud_provider, maturity.
    """
    result = {
        "company": customer_name,
        "ai_products": "",
        "agent_scenarios": "",
        "openclaw_usage": "",
        "cloud_provider": "",
        "maturity": "Unknown",
    }

    if not raw_text:
        return result

    text_lower = raw_text.lower()

    # Extract AI products — look for product names near AI/agent keywords
    ai_keywords = ["ai", "agent", "智能体", "大模型", "llm", "gpt", "copilot",
                    "agentic", "机器人", "自动驾驶", "智能"]
    ai_lines = []
    for line in raw_text.split("\n"):
        if any(kw in line.lower() for kw in ai_keywords):
            ai_lines.append(line.strip())
    if ai_lines:
        result["ai_products"] = "; ".join(ai_lines[:5])

    # Extract agent scenarios
    agent_keywords = ["agent", "智能体", "agentic", "自主", "autonomous",
                      "workflow", "工作流"]
    agent_lines = []
    for line in raw_text.split("\n"):
        if any(kw in line.lower() for kw in agent_keywords):
            agent_lines.append(line.strip())
    if agent_lines:
        result["agent_scenarios"] = "; ".join(agent_lines[:5])

    # Check OpenClaw / agentic platform usage
    openclaw_keywords = ["openclaw", "龙虾", "open claw"]
    if any(kw in text_lower for kw in openclaw_keywords):
        result["openclaw_usage"] = "Detected"
    else:
        result["openclaw_usage"] = "Not detected"

    # Detect cloud provider
    cloud_map = {
        "aws": ["aws", "amazon web services", "bedrock", "sagemaker", "亚马逊云"],
        "azure": ["azure", "microsoft cloud", "openai api", "微软云"],
        "gcp": ["google cloud", "gcp", "vertex ai", "谷歌云"],
        "alibaba": ["阿里云", "alibaba cloud", "通义", "dashscope"],
        "huawei": ["华为云", "huawei cloud", "盘古"],
        "tencent": ["腾讯云", "tencent cloud"],
    }
    detected = []
    for provider, keywords in cloud_map.items():
        if any(kw in text_lower for kw in keywords):
            detected.append(provider)
    result["cloud_provider"] = ", ".join(detected) if detected else "Unknown"

    # Estimate maturity
    high_signals = ["production", "已上线", "规模化", "deployed", "大规模应用"]
    medium_signals = ["poc", "试点", "pilot", "prototype", "测试", "探索"]
    low_signals = ["planning", "计划", "筹备", "评估"]

    if any(s in text_lower for s in high_signals):
        result["maturity"] = "High"
    elif any(s in text_lower for s in medium_signals):
        result["maturity"] = "Medium"
    elif any(s in text_lower for s in low_signals):
        result["maturity"] = "Low"

    return result


def compile_report(results: list[dict], output_path: str) -> dict:
    """
    Generate Markdown summary and JSON output from research results.

    Args:
        results: list of structured result dicts (from parse_research_result)
        output_path: directory for output files

    Returns:
        Dict with paths: {"json": ..., "markdown": ...}
    """
    os.makedirs(output_path, exist_ok=True)

    # Save consolidated JSON
    json_path = os.path.join(output_path, "research_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Generate Markdown summary
    md_lines = [
        "# Customer AI Research Summary",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total customers: {len(results)}",
        "",
        "| # | Company | AI Products | Agent Scenarios | OpenClaw | Cloud | Maturity |",
        "|---|---------|-------------|-----------------|----------|-------|----------|",
    ]

    for i, r in enumerate(results, 1):
        # Truncate long fields for the table
        products = (r.get("ai_products", "") or "")[:60]
        scenarios = (r.get("agent_scenarios", "") or "")[:60]
        if len(r.get("ai_products", "")) > 60:
            products += "..."
        if len(r.get("agent_scenarios", "")) > 60:
            scenarios += "..."

        md_lines.append(
            f"| {i} "
            f"| {r.get('company', '')} "
            f"| {products} "
            f"| {scenarios} "
            f"| {r.get('openclaw_usage', '')} "
            f"| {r.get('cloud_provider', '')} "
            f"| {r.get('maturity', '')} |"
        )

    # Summary stats
    maturity_counts = {}
    cloud_counts = {}
    for r in results:
        m = r.get("maturity", "Unknown")
        maturity_counts[m] = maturity_counts.get(m, 0) + 1
        for cloud in (r.get("cloud_provider", "") or "").split(", "):
            cloud = cloud.strip()
            if cloud and cloud != "Unknown":
                cloud_counts[cloud] = cloud_counts.get(cloud, 0) + 1

    md_lines.extend([
        "",
        "## Maturity Distribution",
        "",
    ])
    for m, count in sorted(maturity_counts.items()):
        md_lines.append(f"- **{m}**: {count} customers")

    if cloud_counts:
        md_lines.extend([
            "",
            "## Cloud Provider Distribution",
            "",
        ])
        for cloud, count in sorted(cloud_counts.items(), key=lambda x: -x[1]):
            md_lines.append(f"- **{cloud}**: {count} customers")

    md_path = os.path.join(output_path, "research_summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    return {"json": json_path, "markdown": md_path}
