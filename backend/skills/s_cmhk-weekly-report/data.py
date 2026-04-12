"""Athena data fetching — all queries, no HTML."""

from datetime import datetime, timedelta, timezone

from utils import wow_pct, wow_str, CST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _month_filter_for_range(start_str, end_str):
    """Build SQL month partition filter for a date range."""
    s = datetime.strptime(start_str, "%Y-%m-%d")
    e = datetime.strptime(end_str, "%Y-%m-%d")
    months = set()
    d = s
    while d <= e:
        months.add(f"DATE '{d.strftime('%Y-%m')}-01'")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return ", ".join(sorted(months))


def compute_6_weeks(anchor_str):
    """Compute 6 weekly windows ending at anchor_str (inclusive)."""
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d")
    weeks = []
    for i in range(5, -1, -1):
        end = anchor - timedelta(days=7 * i)
        start = end - timedelta(days=6)
        _, iso_week, _ = end.isocalendar()
        weeks.append({
            "label": f"W{iso_week}",
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        })
    return weeks


def find_anchor_and_weeks(proxy):
    """Find the data anchor and define CW/PW windows.

    Returns dict with cw_start, cw_end, pw_start, pw_end (str),
    month_filter (SQL fragment), and cw_days/pw_days.
    """
    today = datetime.now(timezone.utc).date()
    today_dt = datetime(today.year, today.month, today.day)

    # Search up to 4 weeks back for data (handles data lag)
    anchor = None
    for weeks_back in range(4):
        candidate_thu = today_dt - timedelta(days=(today_dt.weekday() - 3) % 7 or 7) - timedelta(weeks=weeks_back)
        candidate_wed = candidate_thu - timedelta(days=1)

        check = proxy.athena(
            f"""SELECT
                MAX(CASE WHEN ar_date = DATE '{candidate_thu.strftime('%Y-%m-%d')}' THEN 1 ELSE 0 END) as has_thu,
                MAX(CASE WHEN ar_date = DATE '{candidate_wed.strftime('%Y-%m-%d')}' THEN 1 ELSE 0 END) as has_wed
            FROM fact_estimated_revenue
            WHERE ar_date IN (DATE '{candidate_thu.strftime('%Y-%m-%d')}', DATE '{candidate_wed.strftime('%Y-%m-%d')}')
              AND fbr_flag = 'Y'""",
            database="rl_quicksight_reporting", region="cn-north-1"
        )
        has_thu = int(check["rows"][0]["has_thu"] or 0) == 1
        has_wed = int(check["rows"][0]["has_wed"] or 0) == 1

        if has_thu:
            anchor = candidate_thu
            break
        elif has_wed:
            anchor = candidate_wed
            break
        print(f"  No data for {candidate_thu.strftime('%Y-%m-%d')} (Thu) or {candidate_wed.strftime('%Y-%m-%d')} (Wed), trying previous week...")

    if anchor is None:
        raise ValueError("No data found in the last 4 weeks")

    cw_end = anchor
    cw_start = anchor - timedelta(days=6)
    pw_end = anchor - timedelta(days=7)
    pw_start = anchor - timedelta(days=13)

    # Partition months
    months = set()
    for d in [cw_start, cw_end, pw_start, pw_end]:
        months.add(f"DATE '{d.strftime('%Y-%m')}-01'")
    month_filter = ", ".join(sorted(months))

    # Day counts for data coverage warning
    day_counts = proxy.athena(
        f"""SELECT
            COUNT(DISTINCT CASE WHEN ar_date BETWEEN DATE '{cw_start.strftime('%Y-%m-%d')}' AND DATE '{cw_end.strftime('%Y-%m-%d')}' THEN ar_date END) as cw_days,
            COUNT(DISTINCT CASE WHEN ar_date BETWEEN DATE '{pw_start.strftime('%Y-%m-%d')}' AND DATE '{pw_end.strftime('%Y-%m-%d')}' THEN ar_date END) as pw_days
        FROM fact_estimated_revenue
        WHERE ar_date BETWEEN DATE '{pw_start.strftime('%Y-%m-%d')}' AND DATE '{cw_end.strftime('%Y-%m-%d')}'
          AND fbr_flag = 'Y'""",
        database="rl_quicksight_reporting", region="cn-north-1"
    )

    return {
        "cw_start": cw_start.strftime("%Y-%m-%d"),
        "cw_end": cw_end.strftime("%Y-%m-%d"),
        "pw_start": pw_start.strftime("%Y-%m-%d"),
        "pw_end": pw_end.strftime("%Y-%m-%d"),
        "month_filter": month_filter,
        "cw_days": int(day_counts["rows"][0]["cw_days"]),
        "pw_days": int(day_counts["rows"][0]["pw_days"]),
    }


def find_natural_weeks(proxy):
    """Find CW/PW using Sun-Sat natural weeks (for mid-week reports).

    CW = most recent complete Sun-Sat week.
    Example: if today is Wed Apr 2, CW = Sun Mar 23 ~ Sat Mar 29.

    Sun-Sat ensures 7-day completeness: data has ~2-3 day lag,
    so by Wednesday the full Sun-Sat week is available.
    All weeks (CW, PW, 6W trend) are always 7 days for fair WoW comparison.

    Returns same dict format as find_anchor_and_weeks().
    """
    today = datetime.now(timezone.utc).date()
    today_dt = datetime(today.year, today.month, today.day)

    # Find the most recent Saturday before today
    # today.weekday(): Mon=0, Tue=1, ..., Sat=5, Sun=6
    dow = today_dt.weekday()
    if dow == 5:
        # Today is Saturday → last complete week ended last Sat
        last_sat = today_dt - timedelta(days=7)
    elif dow == 6:
        # Today is Sunday → last complete week ended yesterday (Sat)
        last_sat = today_dt - timedelta(days=1)
    else:
        # Mon-Fri → last Sat = today - (dow + 2)
        last_sat = today_dt - timedelta(days=dow + 2)

    cw_end = last_sat                           # Saturday
    cw_start = last_sat - timedelta(days=6)      # Sunday
    pw_end = cw_start - timedelta(days=1)        # Previous Saturday
    pw_start = pw_end - timedelta(days=6)        # Previous Sunday

    # Partition months
    months = set()
    for d in [cw_start, cw_end, pw_start, pw_end]:
        months.add(f"DATE '{d.strftime('%Y-%m')}-01'")
    month_filter = ", ".join(sorted(months))

    # Day counts
    day_counts = proxy.athena(
        f"""SELECT
            COUNT(DISTINCT CASE WHEN ar_date BETWEEN DATE '{cw_start.strftime('%Y-%m-%d')}' AND DATE '{cw_end.strftime('%Y-%m-%d')}' THEN ar_date END) as cw_days,
            COUNT(DISTINCT CASE WHEN ar_date BETWEEN DATE '{pw_start.strftime('%Y-%m-%d')}' AND DATE '{pw_end.strftime('%Y-%m-%d')}' THEN ar_date END) as pw_days
        FROM fact_estimated_revenue
        WHERE ar_date BETWEEN DATE '{pw_start.strftime('%Y-%m-%d')}' AND DATE '{cw_end.strftime('%Y-%m-%d')}'
          AND fbr_flag = 'Y'""",
        database="rl_quicksight_reporting", region="cn-north-1"
    )

    return {
        "cw_start": cw_start.strftime("%Y-%m-%d"),
        "cw_end": cw_end.strftime("%Y-%m-%d"),
        "pw_start": pw_start.strftime("%Y-%m-%d"),
        "pw_end": pw_end.strftime("%Y-%m-%d"),
        "month_filter": month_filter,
        "cw_days": int(day_counts["rows"][0]["cw_days"]),
        "pw_days": int(day_counts["rows"][0]["pw_days"]),
    }


def compute_6_natural_weeks(cw_end_str):
    """Compute 6 Sun-Sat weeks ending at cw_end (Saturday).

    All 6 weeks are always 7 days (Sun-Sat) for fair WoW comparison.
    """
    cw_end = datetime.strptime(cw_end_str, "%Y-%m-%d")
    weeks = []
    for i in range(5, -1, -1):
        end = cw_end - timedelta(days=7 * i)
        start = end - timedelta(days=6)
        _, iso_week, _ = end.isocalendar()
        weeks.append({
            "label": f"W{iso_week}",
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        })
    return weeks


def fetch_last_refresh(proxy, cw_start_str):
    """Fetch the data refresh timestamp."""
    refresh = proxy.athena(
        f"SELECT MAX(data_refresh_time) as last_refresh "
        f"FROM fact_estimated_revenue WHERE ar_month_start_date = DATE '{cw_start_str[:7]}-01'",
        database="rl_quicksight_reporting", region="cn-north-1", max_wait=30
    )
    raw = refresh["rows"][0]["last_refresh"] if refresh["rows"] else "Unknown"
    if raw and raw != "Unknown":
        try:
            lr_dt = datetime.strptime(raw.split(".")[0], "%Y-%m-%d %H:%M:%S")
            lr_dt = lr_dt.replace(tzinfo=timezone.utc).astimezone(CST)
            return lr_dt.strftime("%Y-%m-%d %H:%M CST (UTC+8)")
        except Exception:
            return raw
    return raw


def fetch_all_bu_names(proxy, weeks):
    """Fetch distinct sh_l3 BU names from current data."""
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    result = proxy.athena(
        f"""SELECT DISTINCT t.sh_l3 as sh_l3
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
        ORDER BY sh_l3""",
        database="rl_quicksight_reporting", region="cn-north-1", max_wait=30
    )
    return [r["sh_l3"] for r in result["rows"]]


def fetch_gcr_data(proxy, weeks):
    """Fetch data for GCR overall report (breakdown by sh_l3)."""
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    pw_s, pw_e = weeks["pw_start"], weeks["pw_end"]

    print("  Fetching sh_l3 breakdown...")
    breakdown = proxy.athena(
        f"""SELECT t.sh_l3 as sh_l3,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END as week_label,
            SUM(r.total_sales_revenue) as revenue,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usage_rev
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
        GROUP BY t.sh_l3,
                 CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END
        ORDER BY t.sh_l3""",
        database="rl_quicksight_reporting", region="cn-north-1"
    )

    last_refresh = fetch_last_refresh(proxy, cw_s)

    cw_rev = {}
    cw_usage = {}
    pw_rev = {}
    pw_usage = {}
    for row in breakdown["rows"]:
        seg = row["sh_l3"]
        rev = float(row["revenue"])
        usage = float(row["usage_rev"])
        if row["week_label"] == "CW":
            cw_rev[seg] = rev
            cw_usage[seg] = usage
        else:
            pw_rev[seg] = rev
            pw_usage[seg] = usage

    all_segs = sorted(set(list(cw_rev.keys()) + list(pw_rev.keys())))
    rows = []
    for seg in all_segs:
        cr = cw_rev.get(seg, 0)
        pr = pw_rev.get(seg, 0)
        cu = cw_usage.get(seg, 0)
        pu = pw_usage.get(seg, 0)
        rows.append({
            "name": seg, "cw_rev": cr, "pw_rev": pr, "rev_wow": wow_pct(cr, pr),
            "cw_usage": cu, "pw_usage": pu, "usage_wow": wow_pct(cu, pu),
        })
    rows.sort(key=lambda x: x["cw_rev"], reverse=True)

    total_cw_rev = sum(r["cw_rev"] for r in rows)
    total_pw_rev = sum(r["pw_rev"] for r in rows)
    total_cw_usage = sum(r["cw_usage"] for r in rows)
    total_pw_usage = sum(r["pw_usage"] for r in rows)

    return {
        "rows": rows,
        "totals": {
            "cw_rev": total_cw_rev, "pw_rev": total_pw_rev,
            "rev_wow": wow_pct(total_cw_rev, total_pw_rev),
            "cw_usage": total_cw_usage, "pw_usage": total_pw_usage,
            "usage_wow": wow_pct(total_cw_usage, total_pw_usage),
        },
        "last_refresh": last_refresh,
    }


def fetch_movers(proxy, bu, weeks, genai_flag, metric, top_n=5):
    """Fetch top accelerators and decelerators for a metric.

    Args:
        bu: BU name (sh_l3) to filter by. None = GCR scope (no sh_l3 filter).
        genai_flag: 'CORE' or 'GENAI'
        metric: 'revenue' or 'usage'
    """
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    pw_s, pw_e = weeks["pw_start"], weeks["pw_end"]

    bu_filter = ""
    if bu is not None:
        bu_escaped = bu.replace("'", "''")
        bu_filter = f"AND t.sh_l3 = '{bu_escaped}'"

    if metric == "revenue":
        val_expr = "r.total_sales_revenue"
    else:
        val_expr = "CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END"

    base_sql = f"""
        SELECT r.sfdc_account_name,
            SUM(CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN {val_expr} ELSE 0 END) as cw_val,
            SUM(CASE WHEN r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{pw_e}' THEN {val_expr} ELSE 0 END) as pw_val
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
          AND r.genai_flag = '{genai_flag}'
          AND r.sfdc_account_name IS NOT NULL
          AND TRIM(r.sfdc_account_name) <> ''
          AND LOWER(TRIM(r.sfdc_account_name)) <> 'unknown'
        GROUP BY r.sfdc_account_name
    """

    delta_expr = (
        f"(SUM(CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN {val_expr} ELSE 0 END)"
        f" - SUM(CASE WHEN r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{pw_e}' THEN {val_expr} ELSE 0 END))"
    )

    accel = proxy.athena(
        base_sql + f" ORDER BY {delta_expr} DESC LIMIT {top_n}",
        database="rl_quicksight_reporting", region="cn-north-1", max_wait=60
    )
    decel = proxy.athena(
        base_sql + f" ORDER BY {delta_expr} ASC LIMIT {top_n}",
        database="rl_quicksight_reporting", region="cn-north-1", max_wait=60
    )

    def parse_rows(rows):
        result = []
        for row in rows:
            cw = float(row["cw_val"])
            pw = float(row["pw_val"])
            delta = cw - pw
            pct = wow_pct(cw, pw)
            result.append({"name": row["sfdc_account_name"], "cw": cw, "pw": pw, "delta": delta, "pct": pct})
        return result

    accels = [r for r in parse_rows(accel["rows"]) if r["delta"] > 0]
    decels = [r for r in parse_rows(decel["rows"]) if r["delta"] < 0]
    return accels, decels


def fetch_detailed_data(proxy, bu_name, weeks, scope_is_gcr=False):
    """Fetch all data needed for v2 tabbed report (CORE/GenAI split per segment and account).

    Args:
        bu_name: BU name (sh_l3) to filter by, or None for GCR scope.
        weeks: anchor/week dict from find_anchor_and_weeks.
        scope_is_gcr: if True, skip sh_l3 filter and break down by sh_l3 instead of sh_l4.
    """
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    pw_s, pw_e = weeks["pw_start"], weeks["pw_end"]

    if scope_is_gcr:
        bu_filter = ""
        segment_col = "t.sh_l3"
    else:
        bu_escaped = bu_name.replace("'", "''")
        bu_filter = f"AND t.sh_l3 = '{bu_escaped}'"
        segment_col = "t.sh_l4"

    # Q1: Overall by genai_flag
    print("  [v2] Fetching overall by CORE/GenAI...")
    q1 = proxy.athena(f"""
        SELECT r.genai_flag,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END as week_label,
            SUM(r.total_sales_revenue) as revenue,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usage_rev
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
        GROUP BY r.genai_flag,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=30)

    # Q2: segment breakdown with genai_flag split
    print(f"  [v2] Fetching {segment_col} breakdown with CORE/GenAI...")
    q2 = proxy.athena(f"""
        SELECT {segment_col} as segment, r.genai_flag,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END as week_label,
            SUM(r.total_sales_revenue) as revenue,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usage_rev
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
        GROUP BY {segment_col}, r.genai_flag,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END
        ORDER BY {segment_col}
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=30)

    # Q3: Top 10 accounts — 2-step approach to avoid 9999-row truncation bug.
    # Step 1: Get top 10 names by revenue and usage (no genai split, with LIMIT).
    print("  [v2] Fetching top account names (no genai split)...")
    _name_filter = ("AND r.sfdc_account_name IS NOT NULL "
                     "AND TRIM(r.sfdc_account_name) <> '' "
                     "AND LOWER(TRIM(r.sfdc_account_name)) <> 'unknown'")
    q_top_rev = proxy.athena(f"""
        SELECT r.sfdc_account_name, SUM(r.total_sales_revenue) as total
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
          {_name_filter}
        GROUP BY r.sfdc_account_name ORDER BY total DESC LIMIT 10
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)

    q_top_usg = proxy.athena(f"""
        SELECT r.sfdc_account_name,
            SUM(CASE WHEN r.biz_charge_type_group='Net Usage' THEN r.total_sales_revenue ELSE 0 END) as total
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
          {_name_filter}
        GROUP BY r.sfdc_account_name ORDER BY total DESC LIMIT 10
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)

    top10_names_rev = [r["sfdc_account_name"] for r in q_top_rev["rows"]]
    top10_names_usg = [r["sfdc_account_name"] for r in q_top_usg["rows"]]
    all_top_names = list(set(top10_names_rev + top10_names_usg))
    names_sql = ", ".join([f"'{n.replace(chr(39), chr(39)+chr(39))}'" for n in all_top_names])

    # Step 2: Get genai split for CW and PW for those accounts only.
    print(f"  [v2] Fetching genai split for {len(all_top_names)} accounts (CW+PW)...")
    q3_cw = proxy.athena(f"""
        SELECT r.sfdc_account_name, r.genai_flag,
            SUM(r.total_sales_revenue) as rev,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usg
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
          AND r.sfdc_account_name IN ({names_sql})
        GROUP BY r.sfdc_account_name, r.genai_flag
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)

    q3_pw = proxy.athena(f"""
        SELECT r.sfdc_account_name, r.genai_flag,
            SUM(r.total_sales_revenue) as rev,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usg
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{pw_e}'
          AND r.fbr_flag = 'Y'
          {bu_filter}
          AND r.sfdc_account_name IN ({names_sql})
        GROUP BY r.sfdc_account_name, r.genai_flag
    """, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)

    # Parse CW account data
    acct_totals = {}
    for row in q3_cw["rows"]:
        name = row["sfdc_account_name"]
        if name not in acct_totals:
            acct_totals[name] = {"cw_rev": 0, "cw_usage": 0,
                                 "core_cw_rev": 0, "core_cw_usage": 0,
                                 "genai_cw_rev": 0, "genai_cw_usage": 0}
        rev = float(row["rev"])
        usg = float(row["usg"])
        acct_totals[name]["cw_rev"] += rev
        acct_totals[name]["cw_usage"] += usg
        if row["genai_flag"] == "CORE":
            acct_totals[name]["core_cw_rev"] += rev
            acct_totals[name]["core_cw_usage"] += usg
        else:
            acct_totals[name]["genai_cw_rev"] += rev
            acct_totals[name]["genai_cw_usage"] += usg

    # Parse PW account data
    acct_pw = {}
    for row in q3_pw["rows"]:
        name = row["sfdc_account_name"]
        if name not in acct_pw:
            acct_pw[name] = {"pw_rev": 0, "pw_usage": 0,
                             "core_pw_rev": 0, "core_pw_usage": 0,
                             "genai_pw_rev": 0, "genai_pw_usage": 0}
        rev = float(row["rev"])
        usg = float(row["usg"])
        acct_pw[name]["pw_rev"] += rev
        acct_pw[name]["pw_usage"] += usg
        if row["genai_flag"] == "CORE":
            acct_pw[name]["core_pw_rev"] += rev
            acct_pw[name]["core_pw_usage"] += usg
        else:
            acct_pw[name]["genai_pw_rev"] += rev
            acct_pw[name]["genai_pw_usage"] += usg

    # Q4-Q7: Movers
    mover_bu = None if scope_is_gcr else bu_name
    scope_tag = "GCR" if scope_is_gcr else bu_name
    print(f"  [v2] Fetching CORE Revenue movers ({scope_tag})...")
    core_rev_accel, core_rev_decel = fetch_movers(proxy, mover_bu, weeks, "CORE", "revenue")
    print(f"  [v2] Fetching CORE Usage movers ({scope_tag})...")
    core_usg_accel, core_usg_decel = fetch_movers(proxy, mover_bu, weeks, "CORE", "usage")
    print(f"  [v2] Fetching GenAI Revenue movers ({scope_tag})...")
    genai_rev_accel, genai_rev_decel = fetch_movers(proxy, mover_bu, weeks, "GENAI", "revenue")
    print(f"  [v2] Fetching GenAI Usage movers ({scope_tag})...")
    genai_usg_accel, genai_usg_decel = fetch_movers(proxy, mover_bu, weeks, "GENAI", "usage")

    last_refresh = fetch_last_refresh(proxy, cw_s)

    # Parse overall
    parsed = {}
    for row in q1["rows"]:
        gf = row["genai_flag"]
        wl = row["week_label"]
        if gf not in parsed:
            parsed[gf] = {}
        parsed[gf][wl] = {"rev": float(row["revenue"]), "usage": float(row["usage_rev"])}

    core_cw_rev = parsed.get("CORE", {}).get("CW", {}).get("rev", 0)
    core_pw_rev = parsed.get("CORE", {}).get("PW", {}).get("rev", 0)
    core_cw_usg = parsed.get("CORE", {}).get("CW", {}).get("usage", 0)
    core_pw_usg = parsed.get("CORE", {}).get("PW", {}).get("usage", 0)
    genai_cw_rev = parsed.get("GENAI", {}).get("CW", {}).get("rev", 0)
    genai_pw_rev = parsed.get("GENAI", {}).get("PW", {}).get("rev", 0)
    genai_cw_usg = parsed.get("GENAI", {}).get("CW", {}).get("usage", 0)
    genai_pw_usg = parsed.get("GENAI", {}).get("PW", {}).get("usage", 0)

    total_cw_rev = core_cw_rev + genai_cw_rev
    total_pw_rev = core_pw_rev + genai_pw_rev
    total_cw_usg = core_cw_usg + genai_cw_usg
    total_pw_usg = core_pw_usg + genai_pw_usg

    # Parse segment breakdown with genai split
    l4_data = {}
    for row in q2["rows"]:
        l4 = row["segment"]
        gf = row["genai_flag"]
        wl = row["week_label"]
        if l4 not in l4_data:
            l4_data[l4] = {}
        key = f"{gf}_{wl}"
        l4_data[l4][key] = {"rev": float(row["revenue"]), "usage": float(row["usage_rev"])}

    l4_rows = []
    for l4 in sorted(l4_data.keys()):
        d = l4_data[l4]
        r = {
            "name": l4,
            "core_cw_rev": d.get("CORE_CW", {}).get("rev", 0),
            "core_pw_rev": d.get("CORE_PW", {}).get("rev", 0),
            "core_cw_usg": d.get("CORE_CW", {}).get("usage", 0),
            "core_pw_usg": d.get("CORE_PW", {}).get("usage", 0),
            "genai_cw_rev": d.get("GENAI_CW", {}).get("rev", 0),
            "genai_pw_rev": d.get("GENAI_PW", {}).get("rev", 0),
            "genai_cw_usg": d.get("GENAI_CW", {}).get("usage", 0),
            "genai_pw_usg": d.get("GENAI_PW", {}).get("usage", 0),
        }
        r["cw_rev"] = r["core_cw_rev"] + r["genai_cw_rev"]
        r["pw_rev"] = r["core_pw_rev"] + r["genai_pw_rev"]
        r["cw_usg"] = r["core_cw_usg"] + r["genai_cw_usg"]
        r["pw_usg"] = r["core_pw_usg"] + r["genai_pw_usg"]
        l4_rows.append(r)
    l4_rows.sort(key=lambda x: x["cw_rev"], reverse=True)

    # Build account rows
    def _build_acct_rows(top_names):
        rows = []
        for name in top_names:
            cw = acct_totals.get(name, {})
            pw = acct_pw.get(name, {})
            rows.append({
                "name": name,
                "core_cw_rev": cw.get("core_cw_rev", 0), "core_cw_usg": cw.get("core_cw_usage", 0),
                "genai_cw_rev": cw.get("genai_cw_rev", 0), "genai_cw_usg": cw.get("genai_cw_usage", 0),
                "cw_rev": cw.get("cw_rev", 0), "cw_usg": cw.get("cw_usage", 0),
                "core_pw_rev": pw.get("core_pw_rev", 0), "core_pw_usg": pw.get("core_pw_usage", 0),
                "genai_pw_rev": pw.get("genai_pw_rev", 0), "genai_pw_usg": pw.get("genai_pw_usage", 0),
                "pw_rev": pw.get("pw_rev", 0), "pw_usg": pw.get("pw_usage", 0),
            })
        return rows

    acct_rows_by_rev = _build_acct_rows(top10_names_rev)
    acct_rows_by_usg = _build_acct_rows(top10_names_usg)

    return {
        "overall": {
            "core_cw_rev": core_cw_rev, "core_pw_rev": core_pw_rev,
            "core_cw_usg": core_cw_usg, "core_pw_usg": core_pw_usg,
            "genai_cw_rev": genai_cw_rev, "genai_pw_rev": genai_pw_rev,
            "genai_cw_usg": genai_cw_usg, "genai_pw_usg": genai_pw_usg,
            "total_cw_rev": total_cw_rev, "total_pw_rev": total_pw_rev,
            "total_cw_usg": total_cw_usg, "total_pw_usg": total_pw_usg,
        },
        "l4_rows": l4_rows,
        "acct_rows_by_rev": acct_rows_by_rev,
        "acct_rows_by_usg": acct_rows_by_usg,
        "movers": {
            "core_rev": {"accel": core_rev_accel, "decel": core_rev_decel},
            "core_usg": {"accel": core_usg_accel, "decel": core_usg_decel},
            "genai_rev": {"accel": genai_rev_accel, "decel": genai_rev_decel},
            "genai_usg": {"accel": genai_usg_accel, "decel": genai_usg_decel},
        },
        "last_refresh": last_refresh,
    }


def fetch_summary_data(proxy, bu_name, weeks):
    """Fetch data for a BU but structured like GCR data (breakdown by sh_l4)."""
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    pw_s, pw_e = weeks["pw_start"], weeks["pw_end"]
    bu_escaped = bu_name.replace("'", "''")

    breakdown = proxy.athena(
        f"""SELECT t.sh_l4 as segment,
            CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END as week_label,
            SUM(r.total_sales_revenue) as revenue,
            SUM(CASE WHEN r.biz_charge_type_group = 'Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usage_rev
        FROM fact_estimated_revenue r
        JOIN sales_share.dim_territory t ON r.territory = t.territory
        WHERE r.ar_month_start_date IN ({mf})
          AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}'
          AND r.fbr_flag = 'Y'
          AND t.sh_l3 = '{bu_escaped}'
        GROUP BY t.sh_l4,
                 CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN 'CW' ELSE 'PW' END
        ORDER BY t.sh_l4""",
        database="rl_quicksight_reporting", region="cn-north-1"
    )

    last_refresh = fetch_last_refresh(proxy, cw_s)

    cw_rev, cw_usage, pw_rev, pw_usage = {}, {}, {}, {}
    for row in breakdown["rows"]:
        seg = row["segment"]
        rev = float(row["revenue"])
        usage = float(row["usage_rev"])
        if row["week_label"] == "CW":
            cw_rev[seg] = rev; cw_usage[seg] = usage
        else:
            pw_rev[seg] = rev; pw_usage[seg] = usage

    all_segs = sorted(set(list(cw_rev.keys()) + list(pw_rev.keys())))
    rows = []
    for seg in all_segs:
        cr = cw_rev.get(seg, 0); pr = pw_rev.get(seg, 0)
        cu = cw_usage.get(seg, 0); pu = pw_usage.get(seg, 0)
        rows.append({"name": seg, "cw_rev": cr, "pw_rev": pr, "rev_wow": wow_pct(cr, pr),
                      "cw_usage": cu, "pw_usage": pu, "usage_wow": wow_pct(cu, pu)})
    rows.sort(key=lambda x: x["cw_rev"], reverse=True)

    total_cw_rev = sum(r["cw_rev"] for r in rows)
    total_pw_rev = sum(r["pw_rev"] for r in rows)
    total_cw_usage = sum(r["cw_usage"] for r in rows)
    total_pw_usage = sum(r["pw_usage"] for r in rows)

    return {
        "rows": rows,
        "totals": {
            "cw_rev": total_cw_rev, "pw_rev": total_pw_rev,
            "rev_wow": wow_pct(total_cw_rev, total_pw_rev),
            "cw_usage": total_cw_usage, "pw_usage": total_pw_usage,
            "usage_wow": wow_pct(total_cw_usage, total_pw_usage),
        },
        "last_refresh": last_refresh,
    }


# ---------------------------------------------------------------------------
# 6-Week Trend Data Functions
# ---------------------------------------------------------------------------

def fetch_6w_segment_data_split(proxy, six_weeks, scope_is_gcr=True, bu_name=None):
    """Fetch 6-week trend data by segment with CORE/GenAI split."""
    cases = []
    for i, w in enumerate(six_weeks):
        cases.append(f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{w['start']}' AND DATE '{w['end']}' THEN r.total_sales_revenue ELSE 0 END) as rev_w{i}")
        cases.append(f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{w['start']}' AND DATE '{w['end']}' AND r.biz_charge_type_group='Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usg_w{i}")
    seg_col = "t.sh_l3" if scope_is_gcr else "t.sh_l4"
    bf = f"AND t.sh_l3='{bu_name.replace(chr(39),chr(39)+chr(39))}'" if not scope_is_gcr and bu_name else ""
    mf = _month_filter_for_range(six_weeks[0]["start"], six_weeks[-1]["end"])
    sql = (f"SELECT {seg_col} as segment, r.genai_flag, {', '.join(cases)} "
           f"FROM fact_estimated_revenue r "
           f"JOIN sales_share.dim_territory t ON r.territory = t.territory "
           f"WHERE r.ar_month_start_date IN ({mf}) "
           f"AND r.ar_date BETWEEN DATE '{six_weeks[0]['start']}' AND DATE '{six_weeks[-1]['end']}' "
           f"AND r.fbr_flag='Y' {bf} "
           f"GROUP BY {seg_col}, r.genai_flag ORDER BY {seg_col}")
    print("  [6W] Fetching segment 6-week trend (CORE/GenAI split)...")
    result = proxy.athena(sql, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)
    raw = {}
    for row in result["rows"]:
        seg, gf = row["segment"], row["genai_flag"]
        raw.setdefault(seg, {})[gf] = [{"rev": float(row.get(f"rev_w{i}", 0) or 0),
                                         "usage": float(row.get(f"usg_w{i}", 0) or 0)} for i in range(6)]
    data = {}
    for seg in raw:
        core = raw[seg].get("CORE", [{"rev": 0, "usage": 0}] * 6)
        genai = raw[seg].get("GENAI", [{"rev": 0, "usage": 0}] * 6)
        data[seg] = [{"rev": core[i]["rev"] + genai[i]["rev"],
                       "usage": core[i]["usage"] + genai[i]["usage"],
                       "core_rev": core[i]["rev"], "core_usage": core[i]["usage"],
                       "genai_rev": genai[i]["rev"], "genai_usage": genai[i]["usage"]} for i in range(6)]
    return data


def fetch_6w_account_data_split(proxy, six_weeks, account_names, scope_is_gcr=True, bu_name=None):
    """Fetch 6-week trend data by account with CORE/GenAI split."""
    if not account_names:
        return {}
    cases = []
    for i, w in enumerate(six_weeks):
        cases.append(f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{w['start']}' AND DATE '{w['end']}' THEN r.total_sales_revenue ELSE 0 END) as rev_w{i}")
        cases.append(f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{w['start']}' AND DATE '{w['end']}' AND r.biz_charge_type_group='Net Usage' THEN r.total_sales_revenue ELSE 0 END) as usg_w{i}")
    bf = f"AND t.sh_l3='{bu_name.replace(chr(39),chr(39)+chr(39))}'" if not scope_is_gcr and bu_name else ""
    names_sql = ", ".join([f"'{n.replace(chr(39),chr(39)+chr(39))}'" for n in account_names])
    mf = _month_filter_for_range(six_weeks[0]["start"], six_weeks[-1]["end"])
    sql = (f"SELECT r.sfdc_account_name, r.genai_flag, {', '.join(cases)} "
           f"FROM fact_estimated_revenue r "
           f"JOIN sales_share.dim_territory t ON r.territory = t.territory "
           f"WHERE r.ar_month_start_date IN ({mf}) "
           f"AND r.ar_date BETWEEN DATE '{six_weeks[0]['start']}' AND DATE '{six_weeks[-1]['end']}' "
           f"AND r.fbr_flag='Y' {bf} AND r.sfdc_account_name IN ({names_sql}) "
           f"GROUP BY r.sfdc_account_name, r.genai_flag")
    print("  [6W] Fetching account 6-week trend (CORE/GenAI split)...")
    result = proxy.athena(sql, database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)
    raw = {}
    for row in result["rows"]:
        name, gf = row["sfdc_account_name"], row["genai_flag"]
        raw.setdefault(name, {})[gf] = [{"rev": float(row.get(f"rev_w{i}", 0) or 0),
                                          "usage": float(row.get(f"usg_w{i}", 0) or 0)} for i in range(6)]
    data = {}
    for name in raw:
        core = raw[name].get("CORE", [{"rev": 0, "usage": 0}] * 6)
        genai = raw[name].get("GENAI", [{"rev": 0, "usage": 0}] * 6)
        data[name] = [{"rev": core[i]["rev"] + genai[i]["rev"],
                        "usage": core[i]["usage"] + genai[i]["usage"],
                        "core_rev": core[i]["rev"], "core_usage": core[i]["usage"],
                        "genai_rev": genai[i]["rev"], "genai_usage": genai[i]["usage"]} for i in range(6)]
    return data


def fetch_total_movers(proxy, bu, weeks, metric, top_n=5):
    """Fetch top accelerators/decelerators WITHOUT genai_flag filter (total)."""
    mf = weeks["month_filter"]
    cw_s, cw_e = weeks["cw_start"], weeks["cw_end"]
    pw_s, pw_e = weeks["pw_start"], weeks["pw_end"]
    bf = f"AND t.sh_l3='{bu.replace(chr(39),chr(39)+chr(39))}'" if bu else ""
    val = "r.total_sales_revenue" if metric == "revenue" else "CASE WHEN r.biz_charge_type_group='Net Usage' THEN r.total_sales_revenue ELSE 0 END"
    base = (f"SELECT r.sfdc_account_name, "
            f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN {val} ELSE 0 END) as cw_val, "
            f"SUM(CASE WHEN r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{pw_e}' THEN {val} ELSE 0 END) as pw_val "
            f"FROM fact_estimated_revenue r "
            f"JOIN sales_share.dim_territory t ON r.territory = t.territory "
            f"WHERE r.ar_month_start_date IN ({mf}) AND r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{cw_e}' "
            f"AND r.fbr_flag='Y' {bf} "
            f"AND r.sfdc_account_name IS NOT NULL AND TRIM(r.sfdc_account_name)<>'' "
            f"AND LOWER(TRIM(r.sfdc_account_name))<>'unknown' "
            f"GROUP BY r.sfdc_account_name")
    delta = (f"(SUM(CASE WHEN r.ar_date BETWEEN DATE '{cw_s}' AND DATE '{cw_e}' THEN {val} ELSE 0 END)"
             f"-SUM(CASE WHEN r.ar_date BETWEEN DATE '{pw_s}' AND DATE '{pw_e}' THEN {val} ELSE 0 END))")
    ac = proxy.athena(base + f" ORDER BY {delta} DESC LIMIT {top_n}",
                      database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)
    dc = proxy.athena(base + f" ORDER BY {delta} ASC LIMIT {top_n}",
                      database="rl_quicksight_reporting", region="cn-north-1", max_wait=60)

    def pr(rows):
        return [{"name": r["sfdc_account_name"],
                 "cw": float(r["cw_val"]), "pw": float(r["pw_val"]),
                 "delta": float(r["cw_val"]) - float(r["pw_val"]),
                 "pct": wow_pct(float(r["cw_val"]), float(r["pw_val"]))} for r in rows]
    return ([r for r in pr(ac["rows"]) if r["delta"] > 0],
            [r for r in pr(dc["rows"]) if r["delta"] < 0])
