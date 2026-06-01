"""Forecast Report — Data Fetching.

Forecast API calls + baseline table queries.
Depends on bundled client.py + queries.py (SDK from data-proxy).
"""

import sys
from client import DataProxyClient, q_baseline
from report_validator import validate_forecast


def _sf(val, default=0.0):
    if val is None or val == "" or val == "null":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def find_latest_cycle(client: DataProxyClient, year: int = 2026) -> dict | None:
    """Find the latest forecast cycle for the given year (via Athena SQL)."""
    try:
        r = client.forecast_cycles(year=year)
        cycles = r.get("forecastCycles", [])
        if not cycles:
            return None
        # Already sorted DESC by cycle_id
        return cycles[0]
    except Exception as e:
        print(f"  [warn] Failed to fetch forecast cycles: {e}", file=sys.stderr)
        return None


def fetch_forecast_view(client: DataProxyClient, cycle_id: str,
                        hierarchy_id: str = "GCR") -> dict:
    """Fetch forecast view (monthly data) via Athena SQL.

    Returns: {"rows": [...], "row_count": N} with columns:
    hierarchy_id, fcst_type, region, m01..m12
    """
    try:
        return client.forecast_view(cycle_id, hierarchy_id)
    except Exception as e:
        print(f"  [warn] Forecast view failed: {e}", file=sys.stderr)
        return {"rows": [], "row_count": 0}


def fetch_forecast_baseline(client: DataProxyClient, cycle_id: str,
                            hierarchy_id: str = "GCR") -> dict:
    """Fetch forecast baseline via Athena SQL.

    Returns: {"rows": [...]} with account-level baseline data.
    """
    try:
        return client.forecast_baseline(cycle_id, hierarchy_id)
    except Exception as e:
        print(f"  [warn] Forecast baseline failed: {e}", file=sys.stderr)
        return {"rows": [], "row_count": 0}


def fetch_top_opportunities(client: DataProxyClient, cycle_id: str,
                            limit: int = 15) -> list:
    """Top opportunities from baseline_opportunity table by total PRR."""
    try:
        total_prr = " + ".join(f"COALESCE(m{i:02d}_prr, 0)" for i in range(1, 13))
        sql = f"""
            SELECT opportunity_id, opportunity_name, account_name, territory_owner,
                   stage, win_probability, mrr,
                   ({total_prr}) AS total_prr
            FROM baseline_opportunity
            WHERE cycle_id = '{cycle_id.replace(chr(39), chr(39)+chr(39))}'
            ORDER BY ({total_prr}) DESC
            LIMIT {limit}
        """
        r = q_baseline(client, sql)
        return r.get("rows", [])
    except Exception as e:
        print(f"  [warn] Top opportunities query failed: {e}", file=sys.stderr)
        return []


def fetch_top_risks(client: DataProxyClient, cycle_id: str,
                    limit: int = 15) -> list:
    """Top risks from baseline_risk table by total impact."""
    try:
        total_impact = " + ".join(
            f"COALESCE(m{i:02d}_risk_impact, 0)" for i in range(1, 13))
        sql = f"""
            SELECT risk_id, risk_name, account_name, territory_owner,
                   risk_status, probability,
                   ({total_impact}) AS total_impact
            FROM baseline_risk
            WHERE cycle_id = '{cycle_id.replace(chr(39), chr(39)+chr(39))}'
            ORDER BY ({total_impact}) DESC
            LIMIT {limit}
        """
        r = q_baseline(client, sql)
        return r.get("rows", [])
    except Exception as e:
        print(f"  [warn] Top risks query failed: {e}", file=sys.stderr)
        return []


def fetch_genai_bedrock_split(client: DataProxyClient, cycle_id: str,
                              hierarchy_id: str = "GCR") -> dict:
    """Split GenAI forecast into Bedrock vs Non-Bedrock using revenue data.

    Queries fact_estimated_revenue with genai_flag='GENAI', grouped by whether
    genai_product_group_gcr starts with 'Bedrock' (captures Bedrock, AgentCore,
    Knowledge Bases).

    Returns: {"bedrock": [monthly...], "non_bedrock": [monthly...]}
    Each entry: {"month": "2026-01", "revenue": float}
    """
    try:
        # Use current month range: last 12 months of ar_month_start_date
        sql = f"""
            SELECT ar_month_start_date,
                   CASE WHEN genai_product_group_gcr LIKE 'Bedrock%'
                        OR genai_product_group_gcr LIKE 'Amazon Bedrock%'
                        THEN 'Bedrock' ELSE 'Non-Bedrock' END AS bedrock_flag,
                   SUM(total_sales_revenue) AS revenue
            FROM fact_estimated_revenue
            WHERE fbr_flag = 'Y'
              AND sh_l1 = 'GCR'
              AND genai_flag = 'GENAI'
              AND ar_month_start_date >= DATE '2025-01-01'
            GROUP BY ar_month_start_date,
                     CASE WHEN genai_product_group_gcr LIKE 'Bedrock%'
                          OR genai_product_group_gcr LIKE 'Amazon Bedrock%'
                          THEN 'Bedrock' ELSE 'Non-Bedrock' END
            ORDER BY ar_month_start_date
        """
        r = client.athena(sql)
        rows = r.get("rows", [])

        bedrock = []
        non_bedrock = []
        for row in rows:
            entry = {
                "month": row.get("ar_month_start_date", ""),
                "revenue": _sf(row.get("revenue")),
            }
            if row.get("bedrock_flag") == "Bedrock":
                bedrock.append(entry)
            else:
                non_bedrock.append(entry)

        return {"bedrock": bedrock, "non_bedrock": non_bedrock}
    except Exception as e:
        print(f"  [warn] GenAI Bedrock split query failed: {e}", file=sys.stderr)
        return {"bedrock": [], "non_bedrock": []}


def fetch_all_data(client: DataProxyClient, cycle_id: str,
                   scope: str = "CMHK") -> dict:
    """Fetch all forecast data for a given cycle and scope.

    Args:
        client: DataProxyClient
        cycle_id: e.g. "fcst_2026_04" or "latest"
        scope: "CMHK" or BU name (mapped to hierarchy_id)
    """
    # Resolve "latest"
    if cycle_id == "latest":
        cycle = find_latest_cycle(client)
        if not cycle:
            raise RuntimeError("No published forecast cycle found")
        cycle_id = cycle["cycleId"]
        print(f"  Latest cycle: {cycle_id} (month {cycle['month']})")

    # Map scope to hierarchy_id
    hierarchy_id = "GCR" if scope == "CMHK" else f"GCR/{scope}"

    # 1. Forecast view (monthly data for CORE + GenAI)
    print(f"  Fetching forecast view ({cycle_id}, {hierarchy_id})...")
    view = fetch_forecast_view(client, cycle_id, hierarchy_id)

    # 2. Forecast baseline (target, YTD, gap)
    print(f"  Fetching forecast baseline...")
    baseline = fetch_forecast_baseline(client, cycle_id, hierarchy_id)

    # 3. Top opportunities (optional)
    print(f"  Fetching top opportunities...")
    opportunities = fetch_top_opportunities(client, cycle_id)

    # 4. Top risks (optional)
    print(f"  Fetching top risks...")
    risks = fetch_top_risks(client, cycle_id)

    result = {
        "cycle_id": cycle_id,
        "scope": scope,
        "hierarchy_id": hierarchy_id,
        "view": view,
        "baseline": baseline,
        "opportunities": opportunities,
        "risks": risks,
    }

    # ── Validate (non-blocking) ──
    if baseline:
        _val_data = {
            "baseline": _sf(baseline.get("baseline")),
            "fy_target": _sf(baseline.get("fy_target")),
            "fy_attain_pct": _sf(baseline.get("fy_attain_pct")),
            "gap": _sf(baseline.get("gap")),
        }
        validation = validate_forecast(_val_data)
        if not validation.passed:
            print(f"  ⚠️  Validation: {validation.summary}", file=sys.stderr)
        else:
            print(f"  ✓ Validation: {validation.summary}")
        result["_validation"] = {"passed": validation.passed,
                                  "score": f"{sum(1 for v in validation.verdicts if v.passed)}/{len(validation.verdicts)}",
                                  "failures": [f"{v.field}: {v.detail}" for v in validation.failures[:3]]}

    return result
