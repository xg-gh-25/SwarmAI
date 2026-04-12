"""
Data layer for industry GTM analysis.
Queries DataProxy Athena for revenue data, builds SFDC URLs.
"""
import requests
import json
import os

PROXY_URL = os.environ.get("DATA_PROXY_URL", "https://thuedsrtpc.execute-api.cn-northwest-1.amazonaws.com.cn/prod/")
API_KEY = os.environ.get("DATA_PROXY_KEY", "")
SFDC_BASE = "https://aws-crm.lightning.force.com/lightning/r/Account/"


def build_sfdc_url(account_id: str) -> str:
    """Build SFDC account URL from 18-char ID."""
    return f"{SFDC_BASE}{account_id}/view"


def _athena(sql: str, db: str = "rl_quicksight_reporting",
            region: str = "cn-north-1", max_wait: int = 120) -> dict:
    """Execute Athena query via DataProxy."""
    resp = requests.post(
        PROXY_URL,
        headers={"Content-Type": "application/json", "x-api-key": API_KEY},
        json={"action": "athena", "query": sql, "database": db,
              "region": region, "max_wait": max_wait},
        timeout=180,
    )
    return resp.json()


def query_revenue(bu_name: str, tshirt_min: str = "L") -> list[dict]:
    """
    Query TTM revenue breakdown for accounts in a BU.

    Args:
        bu_name: sh_l3 BU name (e.g., "AUTO & MFG", "FSI-DNB")
        tshirt_min: minimum T-shirt size filter (applied post-query since
                    T-shirt is in Sentral not Athena)

    Returns:
        List of dicts with: sfdc_account_name, sfdc_account_18id,
        ttm_revenue, genai_ttm, bedrock_ttm, mtd_revenue
    """
    sql = f"""
        SELECT
            sfdc_account_name,
            sfdc_account_18id,
            sfdc_account_tier,
            SUM(CASE WHEN CAST(is_past12month AS VARCHAR) = '1'
                THEN total_sales_revenue ELSE 0 END) as ttm_revenue,
            SUM(CASE WHEN CAST(is_ytd AS VARCHAR) = '1'
                THEN total_sales_revenue ELSE 0 END) as ytd_revenue,
            SUM(CASE WHEN genai_flag = 'GENAI'
                AND CAST(is_past12month AS VARCHAR) = '1'
                THEN total_sales_revenue ELSE 0 END) as genai_ttm,
            SUM(CASE WHEN is_sso_bedrock_gcr = 'Bedrock'
                AND CAST(is_past12month AS VARCHAR) = '1'
                THEN total_sales_revenue ELSE 0 END) as bedrock_ttm,
            SUM(CASE WHEN CAST(is_currentmonth AS VARCHAR) = '1'
                THEN total_sales_revenue ELSE 0 END) as mtd_revenue
        FROM fact_estimated_revenue
        WHERE sh_l3 = '{bu_name}'
        GROUP BY sfdc_account_name, sfdc_account_18id, sfdc_account_tier
        HAVING SUM(CASE WHEN CAST(is_past12month AS VARCHAR) = '1'
                   THEN total_sales_revenue ELSE 0 END) > 0
        ORDER BY ttm_revenue DESC
    """
    result = _athena(sql, max_wait=180)

    if "error" in result:
        raise RuntimeError(f"Athena query failed: {result['error']}")

    rows = result.get("rows", [])
    # Normalize numeric fields
    for row in rows:
        for field in ["ttm_revenue", "genai_ttm", "bedrock_ttm", "ytd_revenue", "mtd_revenue"]:
            row[field] = float(row.get(field, 0) or 0)

    return rows


def merge_accounts_with_revenue(accounts: list[dict], revenue: list[dict]) -> list[dict]:
    """
    Merge Sentral account list with Athena revenue data.

    Args:
        accounts: list from Sentral (id, name, website, size, owner, geo)
        revenue: list from query_revenue()

    Returns:
        Merged list with revenue fields added to each account.
    """
    # Build revenue lookup by sfdc_account_18id and by name
    rev_by_id = {r["sfdc_account_18id"]: r for r in revenue if r.get("sfdc_account_18id")}
    rev_by_name = {r["sfdc_account_name"].strip().lower(): r
                   for r in revenue if r.get("sfdc_account_name")}

    for acc in accounts:
        acc_id = acc.get("id", "")
        rev = rev_by_id.get(acc_id)
        if not rev:
            name = acc.get("name", "").strip().lower()
            rev = rev_by_name.get(name)

        if rev:
            acc["ttm"] = rev["ttm_revenue"]
            acc["genai"] = rev["genai_ttm"]
            acc["bedrock"] = rev["bedrock_ttm"]
        else:
            acc["ttm"] = 0
            acc["genai"] = 0
            acc["bedrock"] = 0

        acc["sfdc_url"] = build_sfdc_url(acc_id) if acc_id else ""

    return accounts
