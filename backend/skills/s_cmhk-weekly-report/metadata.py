"""Report metadata extraction and HTML embedding.

Extracts structured metadata from report data for OpenSearch storage
and Portal/chat integration. Embeds metadata as JSON in HTML reports.
"""

import json
from utils import wow_pct


def extract_report_metadata(data, weeks, six_weeks, scope_label, scope_is_gcr,
                            template_name, total_movers_data=None):
    """Extract report_metadata dict from report data.

    Args:
        data: dict from fetch_detailed_data() with keys:
              overall, l4_rows, acct_rows_by_rev, acct_rows_by_usg, movers
        weeks: anchor/week dict with cw_start, cw_end, pw_start, pw_end, cw_days
        six_weeks: list of 6 week dicts from compute_6_weeks()
        scope_label: display name ("GCR" or BU name)
        scope_is_gcr: True if GCR overall
        template_name: "ceo_lite" or "detailed"
        total_movers_data: dict with total_usg/total_rev accel/decel (optional)

    Returns:
        dict matching the report_metadata schema
    """
    o = data["overall"]
    cw_days = weeks["cw_days"]

    # --- data_window ---
    data_window = {
        "cw_start": weeks["cw_start"],
        "cw_end": weeks["cw_end"],
        "pw_start": weeks["pw_start"],
        "pw_end": weeks["pw_end"],
        "cw_days": cw_days,
    }

    # --- kpi ---
    usage_per_day = o["total_cw_usg"] / cw_days if cw_days > 0 else 0
    revenue_per_day = o["total_cw_rev"] / cw_days if cw_days > 0 else 0
    usage_wow = wow_pct(o["total_cw_usg"], o["total_pw_usg"])
    revenue_wow = wow_pct(o["total_cw_rev"], o["total_pw_rev"])
    genai_usg_mix = (o["genai_cw_usg"] / o["total_cw_usg"] * 100
                     if o["total_cw_usg"] > 0 else 0)
    genai_rev_mix = (o["genai_cw_rev"] / o["total_cw_rev"] * 100
                     if o["total_cw_rev"] > 0 else 0)

    kpi = {
        "usage_per_day": round(usage_per_day, 2),
        "usage_wow_pct": round(usage_wow, 1),
        "revenue_per_day": round(revenue_per_day, 2),
        "revenue_wow_pct": round(revenue_wow, 1),
        "genai_usage_mix_pct": round(genai_usg_mix, 1),
        "genai_revenue_mix_pct": round(genai_rev_mix, 1),
    }

    # --- top_accelerators / top_decelerators (max 3 each) ---
    # Prefer total_rev movers; fallback to core_rev from data["movers"]
    rev_accel = []
    rev_decel = []
    if total_movers_data and "total_rev" in total_movers_data:
        rev_accel = total_movers_data["total_rev"].get("accel", [])
        rev_decel = total_movers_data["total_rev"].get("decel", [])
    elif "core_rev" in data.get("movers", {}):
        rev_accel = data["movers"]["core_rev"].get("accel", [])
        rev_decel = data["movers"]["core_rev"].get("decel", [])

    def _format_movers(movers, max_n=3):
        result = []
        for m in movers[:max_n]:
            result.append({
                "account": m["name"],
                "metric": "revenue",
                "delta": round(m["delta"], 2),
                "wow_pct": round(m["pct"], 1),
            })
        return result

    top_accelerators = _format_movers(rev_accel, 3)
    top_decelerators = _format_movers(rev_decel, 3)

    # --- sub_segments ---
    sub_segments = [r["name"] for r in data.get("l4_rows", [])]

    # --- highlights (1-2 sentences, rule-based) ---
    highlights = _generate_highlights(usage_wow, revenue_wow, genai_usg_mix,
                                      rev_accel, rev_decel)

    # --- suggested_questions (exactly 3) ---
    suggested_questions = _generate_questions(
        scope_label, usage_wow, revenue_wow, genai_usg_mix,
        rev_decel, sub_segments)

    return {
        "data_window": data_window,
        "kpi": kpi,
        "top_accelerators": top_accelerators,
        "top_decelerators": top_decelerators,
        "highlights": highlights,
        "suggested_questions": suggested_questions,
        "template": template_name,
        "sub_segments": sub_segments,
    }


def _generate_highlights(usage_wow, revenue_wow, genai_mix_pct,
                         rev_accel, rev_decel):
    """Generate 1-2 rule-based highlight sentences."""
    highlights = []

    # Usage WoW
    if usage_wow > 5:
        highlights.append(f"Usage 显著增长 +{usage_wow:.1f}%")
    elif usage_wow < -5:
        highlights.append(f"Usage 明显下降 {usage_wow:.1f}%")

    # Revenue WoW
    if revenue_wow > 5:
        highlights.append(f"Revenue 显著增长 +{revenue_wow:.1f}%")
    elif revenue_wow < -5:
        highlights.append(f"Revenue 明显下降 {revenue_wow:.1f}%")

    # GenAI mix
    if genai_mix_pct > 20:
        highlights.append(f"GenAI 渗透率 {genai_mix_pct:.1f}%")

    # Top mover (largest abs delta)
    all_movers = rev_accel[:1] + rev_decel[:1]
    if all_movers:
        top = max(all_movers, key=lambda m: abs(m["delta"]))
        if abs(top["delta"]) > 50000:
            sign = "+" if top["delta"] > 0 else ""
            highlights.append(
                f"Top mover: {top['name']} ({sign}{top['delta']:,.0f})")

    # Ensure at least 1, at most 2
    if not highlights:
        if abs(usage_wow) > abs(revenue_wow):
            highlights.append(f"Usage WoW {usage_wow:+.1f}%")
        else:
            highlights.append(f"Revenue WoW {revenue_wow:+.1f}%")

    return highlights[:2]


def _generate_questions(scope, usage_wow, revenue_wow, genai_mix_pct,
                        rev_decel, sub_segments):
    """Generate exactly 3 suggested questions based on data patterns."""
    candidates = []

    # Usage WoW significant
    if usage_wow > 3:
        candidates.append(
            f"{scope} 本周 Usage 增长 {usage_wow:.1f}% 的主要驱动力是什么？")
    elif usage_wow < -3:
        candidates.append(
            f"{scope} 本周 Usage 下降 {usage_wow:.1f}% 的原因是什么？")

    # Revenue WoW significant
    if abs(revenue_wow) > 3:
        candidates.append(
            f"{scope} Revenue 变化 {revenue_wow:+.1f}% 背后的原因？")

    # Top decelerator
    if rev_decel:
        top_decel = rev_decel[0]
        candidates.append(
            f"{top_decel['name']} 的 Revenue 为什么下降了？")

    # GenAI mix
    if genai_mix_pct > 15:
        candidates.append(
            f"{scope} 的 GenAI 渗透率目前 {genai_mix_pct:.1f}%，趋势如何？")

    # Sub segments
    if sub_segments:
        candidates.append(
            f"{scope} 下面哪个子单元表现最好？")

    # Default fallback
    candidates.append(f"最近 6 周 {scope} 的整体趋势如何？")

    # Deduplicate and pick exactly 3
    seen = set()
    unique = []
    for q in candidates:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique[:3]


def embed_metadata_in_html(html, metadata):
    """Inject report metadata as JSON script tag before </body>.

    Args:
        html: the complete HTML string
        metadata: dict to serialize as JSON

    Returns:
        HTML string with metadata embedded
    """
    json_str = json.dumps(metadata, ensure_ascii=False, indent=2)
    script_tag = (
        '\n<script type="application/json" id="report-metadata">\n'
        f'{json_str}\n'
        '</script>\n'
    )

    # Insert before </body>
    if '</body>' in html:
        html = html.replace('</body>', script_tag + '</body>', 1)
    elif '</html>' in html:
        html = html.replace('</html>', script_tag + '</html>', 1)
    else:
        html += script_tag

    return html
