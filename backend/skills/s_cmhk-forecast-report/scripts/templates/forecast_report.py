"""Forecast Report HTML Template — 4-tab layout with waterfall.

CSS-only (no Chart.js). Colors from TECH.md standard.
Tabs: Overall / CORE / GenAI / Breakdown.
"""

import html as _html
import sys
from datetime import datetime
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent
_SCRIPTS_DIR = _TEMPLATES_DIR.parent
if str(_TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(_TEMPLATES_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from insights_renderer import render_insights_section, INSIGHTS_CSS
except ImportError:
    def render_insights_section(insights):
        return ""
    INSIGHTS_CSS = ""
try:
    from swarm_branding import swarm_header_html, swarm_footer_html, SWARM_BADGE_CSS
except ImportError:
    def swarm_header_html(size=24): return ""
    def swarm_footer_html(): return ""
    SWARM_BADGE_CSS = ""

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _safe(val, fmt="s", default="—"):
    if val is None or val == "" or val == "null":
        return default
    if fmt == "$":
        try:
            v = float(val)
            if abs(v) >= 1_000_000_000:
                return f"${v / 1_000_000_000:,.2f}B"
            elif abs(v) >= 1_000_000:
                return f"${v / 1_000_000:,.1f}M"
            elif abs(v) >= 1_000:
                return f"${v / 1_000:,.1f}K"
            return f"${v:,.0f}"
        except (ValueError, TypeError):
            return default
    if fmt == "%":
        try:
            return f"{float(val):+.1f}%"
        except (ValueError, TypeError):
            return default
    if fmt == "pct":
        try:
            return f"{float(val) * 100:.1f}%"
        except (ValueError, TypeError):
            return default
    if fmt == "n":
        try:
            return f"{int(float(val)):,}"
        except (ValueError, TypeError):
            return default
    return _html.escape(str(val))


def _sf(val, default=0.0):
    if val is None or val == "" or val == "null":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _kpi_card(title, value, subtitle="", color_class=""):
    return f"""<div class="kpi-card">
      <div class="kpi-title">{title}</div>
      <div class="kpi-value {color_class}">{value}</div>
      <div class="kpi-subtitle">{subtitle}</div>
    </div>"""


def _attn_color(attn):
    """Color for attainment: green >= 1.0, red < 0.9, else muted."""
    try:
        v = float(attn)
        return "green" if v >= 1.0 else "red" if v < 0.9 else "muted"
    except (ValueError, TypeError):
        return "muted"


# ── Waterfall ─────────────────────────────────────────────────────


def _render_waterfall(baseline):
    """CSS-only waterfall: Target → YTD → ROY Forecast → Gap."""
    target = _sf(baseline.get("fyTargetRev"))
    ytd = _sf(baseline.get("ytdFbr"))
    roy = _sf(baseline.get("royRevenue"))
    gap = _sf(baseline.get("fcstGap"))
    risk = _sf(baseline.get("royRisk"))
    opp = _sf(baseline.get("royOpp"))
    forecast = ytd + roy

    if target <= 0:
        return '<div class="section"><p class="muted">No target data available</p></div>'

    # Normalize to percentage of target for bar widths
    max_val = max(target, forecast) or 1
    t_pct = target / max_val * 100
    y_pct = ytd / max_val * 100
    r_pct = roy / max_val * 100
    g_pct = abs(gap) / max_val * 100
    gap_color = "var(--green)" if gap >= 0 else "var(--red)"
    gap_label = f"+{_safe(gap, '$')}" if gap >= 0 else _safe(gap, "$")

    bars = f"""
    <div class="wf-row">
      <span class="wf-label">FY Target</span>
      <div class="wf-bar-wrap">
        <div class="wf-bar" style="width:{t_pct:.0f}%;background:var(--accent)">{_safe(target, "$")}</div>
      </div>
    </div>
    <div class="wf-row">
      <span class="wf-label">YTD Revenue</span>
      <div class="wf-bar-wrap">
        <div class="wf-bar" style="width:{y_pct:.0f}%;background:#34d399">{_safe(ytd, "$")}</div>
      </div>
    </div>
    <div class="wf-row">
      <span class="wf-label">ROY Forecast</span>
      <div class="wf-bar-wrap">
        <div class="wf-bar" style="width:{r_pct:.0f}%;background:#818cf8">{_safe(roy, "$")}</div>
      </div>
    </div>
    <div class="wf-row">
      <span class="wf-label">Total Forecast</span>
      <div class="wf-bar-wrap">
        <div class="wf-bar" style="width:{(y_pct + r_pct):.0f}%;background:linear-gradient(90deg,#34d399 {y_pct/(y_pct+r_pct+0.01)*100:.0f}%,#818cf8 0%)">{_safe(forecast, "$")}</div>
      </div>
    </div>
    <div class="wf-row">
      <span class="wf-label">Gap</span>
      <div class="wf-bar-wrap">
        <div class="wf-bar" style="width:{g_pct:.0f}%;background:{gap_color}">{gap_label}</div>
      </div>
    </div>"""

    attainment = (forecast / target * 100) if target else 0
    attn_color = "green" if attainment >= 100 else "red" if attainment < 90 else "accent"

    return f"""<div class="section">
      <h3>Forecast Waterfall</h3>
      <div class="waterfall">{bars}</div>
      <div class="wf-summary">
        Attainment: <strong class="{attn_color}">{attainment:.1f}%</strong>
        &nbsp;|&nbsp; Risk: {_safe(risk, "$")}
        &nbsp;|&nbsp; Opportunities: {_safe(opp, "$")}
      </div>
    </div>"""


# ── Monthly Trajectory ────────────────────────────────────────────


def _render_monthly_table(monthly_data, label=""):
    """Monthly trajectory table: Month / FBR / Forecast / Target / Attn / YoY."""
    if not monthly_data:
        return '<div class="section"><p class="muted">No monthly data</p></div>'

    rows = ""
    for m in monthly_data:
        month_idx = int(m.get("month", 0)) - 1
        month_name = MONTH_NAMES[month_idx] if 0 <= month_idx < 12 else f"M{m.get('month')}"
        fbr = _sf(m.get("fbr"))
        fcst = _sf(m.get("fcst"))
        target = _sf(m.get("target"))
        attn = _sf(m.get("attn"))
        yoy = _sf(m.get("yoy"))
        # FBR is actual if > 0, otherwise forecast is the plan
        actual_or_plan = _safe(fbr, "$") if fbr > 0 else _safe(fcst, "$")
        attn_cls = _attn_color(attn)
        rows += (
            f'<tr><td><strong>{month_name}</strong></td>'
            f'<td class="num">{_safe(target, "$")}</td>'
            f'<td class="num">{actual_or_plan}</td>'
            f'<td class="num {attn_cls}">{_safe(attn, "pct")}</td>'
            f'<td class="num">{_safe(yoy, "pct")}</td></tr>'
        )

    title = f"Monthly Trajectory — {label}" if label else "Monthly Trajectory"
    return f"""<div class="section">
      <h3>{title}</h3>
      <table><thead><tr><th>Month</th><th>Target</th><th>Actual / Fcst</th><th>Attainment</th><th>YoY</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </div>"""


# ── Tab Renderers ─────────────────────────────────────────────────


def _render_tab1_overall(data):
    """Tab 1: Overall — KPI cards + waterfall + monthly trajectory."""
    baseline = data.get("baseline", {})
    view = data.get("view", {})

    target = _sf(baseline.get("fyTargetRev"))
    ytd = _sf(baseline.get("ytdFbr"))
    roy = _sf(baseline.get("royRevenue"))
    gap = _sf(baseline.get("fcstGap"))
    forecast = ytd + roy
    attainment = (forecast / target * 100) if target else 0

    cards = f"""<div class="kpi-grid">
    {_kpi_card("FY Target", _safe(target, "$"))}
    {_kpi_card("YTD Revenue", _safe(ytd, "$"))}
    {_kpi_card("Forecast (YTD+ROY)", _safe(forecast, "$"),
               f'Attainment: {attainment:.1f}%',
               "green" if attainment >= 100 else "red" if attainment < 90 else "")}
    {_kpi_card("Forecast Gap", _safe(gap, "$"), "",
               "green" if gap >= 0 else "red")}
    </div>"""

    waterfall = _render_waterfall(baseline)

    # Combined monthly from core + genai
    core_monthly = view.get("core", {}).get("monthlyData", [])
    genai_monthly = view.get("genai", {}).get("monthlyData", [])
    # Merge: sum fbr/fcst/target per month
    combined = []
    for i in range(12):
        c = core_monthly[i] if i < len(core_monthly) else {}
        g = genai_monthly[i] if i < len(genai_monthly) else {}
        combined.append({
            "month": i + 1,
            "fbr": _sf(c.get("fbr")) + _sf(g.get("fbr")),
            "fcst": _sf(c.get("fcst")) + _sf(g.get("fcst")),
            "target": _sf(c.get("target")) + _sf(g.get("target")),
            "attn": None,  # Recalculate
            "yoy": None,
        })
        t = combined[-1]["target"]
        f_or_a = combined[-1]["fbr"] if combined[-1]["fbr"] > 0 else combined[-1]["fcst"]
        combined[-1]["attn"] = (f_or_a / t) if t else None
        c_yoy = _sf(c.get("yoy"))
        g_yoy = _sf(g.get("yoy"))
        if c_yoy or g_yoy:
            combined[-1]["yoy"] = c_yoy  # Approximate with core yoy

    trajectory = _render_monthly_table(combined, "Overall (CORE + GenAI)")

    return f"{cards}\n{waterfall}\n{trajectory}"


def _render_tab2_core(data):
    """Tab 2: CORE monthly data."""
    view = data.get("view", {})
    monthly = view.get("core", {}).get("monthlyData", [])
    return _render_monthly_table(monthly, "CORE")


def _render_genai_breakdown(data):
    """Render Bedrock vs Non-Bedrock trajectory sub-section within GenAI tab."""
    split = data.get("genai_bedrock_split", {})
    bedrock = split.get("bedrock", [])
    non_bedrock = split.get("non_bedrock", [])

    if not bedrock and not non_bedrock:
        return '<div class="section"><p class="muted">No Bedrock/Non-Bedrock split data available</p></div>'

    # Build a merged table: Month | Bedrock Rev | Non-Bedrock Rev | Bedrock %
    # Align by month
    months_map = {}
    for entry in bedrock:
        m = entry.get("month", "")
        months_map.setdefault(m, {"bedrock": 0, "non_bedrock": 0})
        months_map[m]["bedrock"] = _sf(entry.get("revenue"))
    for entry in non_bedrock:
        m = entry.get("month", "")
        months_map.setdefault(m, {"bedrock": 0, "non_bedrock": 0})
        months_map[m]["non_bedrock"] = _sf(entry.get("revenue"))

    sorted_months = sorted(months_map.keys())
    if not sorted_months:
        return '<div class="section"><p class="muted">No Bedrock/Non-Bedrock split data available</p></div>'

    rows = ""
    for m in sorted_months:
        br = months_map[m]["bedrock"]
        nbr = months_map[m]["non_bedrock"]
        total = br + nbr
        br_pct = (br / total * 100) if total > 0 else 0
        # Format month for display (e.g. "2026-01-01" -> "Jan 2026")
        month_display = m
        if len(m) >= 7:
            try:
                month_idx = int(m[5:7]) - 1
                year = m[:4]
                month_display = f"{MONTH_NAMES[month_idx]} {year}"
            except (ValueError, IndexError):
                pass
        rows += (
            f'<tr><td><strong>{month_display}</strong></td>'
            f'<td class="num">{_safe(br, "$")}</td>'
            f'<td class="num">{_safe(nbr, "$")}</td>'
            f'<td class="num">{_safe(total, "$")}</td>'
            f'<td class="num"><span class="bedrock-pct">{br_pct:.0f}%</span></td></tr>'
        )

    return f"""<div class="section">
      <h3>GenAI Breakdown — Bedrock vs Non-Bedrock</h3>
      <table><thead><tr><th>Month</th><th>Bedrock</th><th>Non-Bedrock</th><th>Total GenAI</th><th>Bedrock %</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </div>"""


def _render_tab3_genai(data):
    """Tab 3: GenAI monthly data + Bedrock/Non-Bedrock breakdown."""
    view = data.get("view", {})
    monthly = view.get("genai", {}).get("monthlyData", [])
    trajectory = _render_monthly_table(monthly, "GenAI")
    breakdown = _render_genai_breakdown(data)
    return f"{trajectory}\n{breakdown}"


def _render_tab4_breakdown(data):
    """Tab 4: Top opportunities + risks."""
    opps = data.get("opportunities", [])
    risks = data.get("risks", [])

    parts = []

    if opps:
        rows = "".join(
            f'<tr><td>{(o.get("opportunity_name") or "")[:45]}</td>'
            f'<td>{(o.get("account_name") or "")[:30]}</td>'
            f'<td>{o.get("stage") or ""}</td>'
            f'<td class="num">{_safe(o.get("win_probability"), "pct")}</td>'
            f'<td class="num">{_safe(o.get("total_prr"), "$")}</td></tr>'
            for o in opps[:15]
        )
        parts.append(f"""<div class="section">
      <h3>Top Opportunities (by ROY Contribution)</h3>
      <table><thead><tr><th>Opportunity</th><th>Account</th><th>Stage</th><th>Win%</th><th>ROY PRR</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </div>""")

    if risks:
        rows = "".join(
            f'<tr><td>{(r.get("risk_name") or "")[:45]}</td>'
            f'<td>{(r.get("account_name") or "")[:30]}</td>'
            f'<td>{r.get("risk_status") or ""}</td>'
            f'<td class="num">{_safe(r.get("probability"), "pct")}</td>'
            f'<td class="num">{_safe(r.get("total_impact"), "$")}</td></tr>'
            for r in risks[:15]
        )
        parts.append(f"""<div class="section">
      <h3>Top Risks (by ROY Impact)</h3>
      <table><thead><tr><th>Risk</th><th>Account</th><th>Status</th><th>Prob</th><th>ROY Impact</th></tr></thead>
      <tbody>{rows}</tbody></table>
    </div>""")

    if not parts:
        return '<div class="section"><p class="muted">No opportunity/risk detail available (baseline tables may be empty for this cycle)</p></div>'

    return "\n".join(parts)


# ── CSS ───────────────────────────────────────────────────────────

CSS = """
:root {
  --bg: #f8fafc; --surface: #ffffff; --card: #ffffff; --card-border: #e2e8f0;
  --text: #1e293b; --text-muted: #64748b; --text-light: #94a3b8;
  --accent: #0284c7; --accent2: #6366f1;
  --green: #16a34a; --red: #dc2626; --orange: #ea580c;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05);
  --radius: 10px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:var(--bg); color:var(--text); line-height:1.6; font-size:14px; }
.container { max-width:1140px; margin:0 auto; padding:24px; }

.header { background:linear-gradient(135deg, #6d28d9, var(--accent2));
          color:#fff; padding:28px 36px; border-radius:var(--radius); margin-bottom:24px;
          box-shadow:var(--shadow-md); }
.header h1 { font-size:22px; font-weight:700; }
.header .subtitle { opacity:0.8; font-size:12px; margin-top:6px; }

/* Tabs */
input[name="tabs"] { display:none; }
.tab-group { display:flex; gap:4px; background:var(--surface); border-radius:var(--radius);
             padding:4px; margin-bottom:24px; box-shadow:var(--shadow-sm);
             border:1px solid var(--card-border); }
.tab-group label { flex:1; text-align:center; padding:10px 16px; cursor:pointer;
                    color:var(--text-muted); font-weight:500; font-size:13px;
                    border-radius:8px; transition:all 0.15s; }
.tab-group label:hover { color:var(--text); background:rgba(109,40,217,0.04); }
#tab1:checked ~ .tab-group label[for="tab1"],
#tab2:checked ~ .tab-group label[for="tab2"],
#tab3:checked ~ .tab-group label[for="tab3"],
#tab4:checked ~ .tab-group label[for="tab4"] {
  color:#fff; background:var(--accent2); box-shadow:var(--shadow-sm); font-weight:600;
}
.tab-panel-1, .tab-panel-2, .tab-panel-3, .tab-panel-4 { display:none; }
#tab1:checked ~ .tabs .tab-panel-1,
#tab2:checked ~ .tabs .tab-panel-2,
#tab3:checked ~ .tabs .tab-panel-3,
#tab4:checked ~ .tabs .tab-panel-4 { display:block; animation:fadeIn 0.2s ease; }
@keyframes fadeIn { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }

/* KPI */
.kpi-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:16px; margin-bottom:28px; }
@media(max-width:900px){ .kpi-grid{grid-template-columns:repeat(2,1fr)} }
.kpi-card { background:var(--card); border:1px solid var(--card-border);
            border-radius:var(--radius); padding:20px; box-shadow:var(--shadow-sm); }
.kpi-card:hover { box-shadow:var(--shadow-md); }
.kpi-title { font-size:11px; color:var(--text-light); text-transform:uppercase;
             letter-spacing:0.8px; margin-bottom:8px; font-weight:600; }
.kpi-value { font-size:28px; font-weight:800; line-height:1.1; font-variant-numeric:tabular-nums; }
.kpi-subtitle { font-size:12px; color:var(--text-muted); margin-top:6px; }
.kpi-value.green { color:var(--green); }
.kpi-value.red { color:var(--red); }

/* Waterfall */
.waterfall { display:flex; flex-direction:column; gap:12px; }
.wf-row { display:flex; align-items:center; gap:14px; }
.wf-label { width:130px; font-size:12px; text-align:right; color:var(--text-muted);
            font-weight:600; flex-shrink:0; }
.wf-bar-wrap { flex:1; height:32px; background:#f1f5f9; border-radius:6px; overflow:hidden; }
.wf-bar { height:100%; border-radius:6px; display:flex; align-items:center;
          justify-content:center; color:#fff; font-size:11px; font-weight:700;
          min-width:40px; transition:width 0.4s ease; }
.wf-summary { margin-top:14px; font-size:13px; color:var(--text-muted); }
.wf-summary .green { color:var(--green); }
.wf-summary .red { color:var(--red); }
.wf-summary .accent { color:var(--accent2); }

/* Section */
.section { margin-bottom:28px; background:var(--surface); border-radius:var(--radius);
           padding:20px 24px; border:1px solid var(--card-border); box-shadow:var(--shadow-sm); }
.section h3 { font-size:14px; font-weight:700; margin-bottom:16px; text-transform:uppercase;
              letter-spacing:0.3px; padding-bottom:10px; border-bottom:2px solid var(--card-border); }

/* Table */
table { width:100%; border-collapse:separate; border-spacing:0; font-size:13px; }
th { text-align:left; padding:10px 14px; background:#f1f5f9; font-weight:700;
     font-size:11px; text-transform:uppercase; letter-spacing:0.5px; color:var(--text-muted);
     border-bottom:2px solid var(--card-border); }
th:first-child { border-radius:8px 0 0 0; }
th:last-child { border-radius:0 8px 0 0; }
td { padding:10px 14px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
tr:hover td { background:rgba(99,102,241,0.03); }
tr:last-child td { border-bottom:none; }
.num { text-align:right; font-variant-numeric:tabular-nums; font-weight:500; }

.muted { color:var(--text-muted); }
.green { color:var(--green); }
.red { color:var(--red); }
.bedrock-pct { background:linear-gradient(135deg,#6d28d9,#818cf8); color:#fff;
               padding:2px 8px; border-radius:10px; font-size:11px; font-weight:700; }
.footer { text-align:center; color:var(--text-light); font-size:11px;
          margin-top:36px; padding-top:16px; border-top:1px solid var(--card-border); }
"""


# ── Main Render ───────────────────────────────────────────────────


def render(data: dict, insights: dict = None) -> str:
    """Render 4-tab forecast report HTML."""
    cycle_id = data.get("cycle_id", "")
    scope = data.get("scope", "CMHK")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    title = f"CMHK Forecast Report — {scope}"
    subtitle = f"Cycle: {cycle_id} | Generated: {generated}"

    tab1 = _render_tab1_overall(data)
    tab2 = _render_tab2_core(data)
    tab3 = _render_tab3_genai(data)
    tab4 = _render_tab4_breakdown(data)

    # Per-tab insights: each tab has its own independently-designed insights.
    # insights dict keys = tab names, values = full insights for that tab.
    _TAB_NAMES = ["overall", "core", "genai", "breakdown"]
    tab_insights = {}
    has_insights = False
    if insights:
        is_per_tab = any(k in insights for k in _TAB_NAMES)
        if is_per_tab:
            for tab_name in _TAB_NAMES:
                tab_data = insights.get(tab_name)
                if tab_data:
                    tab_insights[tab_name] = render_insights_section(tab_data)
                    has_insights = True
        else:
            # Legacy flat format
            rendered = render_insights_section(insights)
            if rendered:
                has_insights = True
                for tab_name in _TAB_NAMES:
                    tab_insights[tab_name] = rendered
    insights_css = (INSIGHTS_CSS if has_insights else "") + "\n" + SWARM_BADGE_CSS
    swarm_icon = swarm_header_html(28)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{CSS}\n{insights_css}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div style="display:flex;align-items:center;gap:10px;"><span>{swarm_icon}</span><h1 style="margin:0;">{title}</h1></div>
    <div class="subtitle">{subtitle}</div>
  </div>

  <input type="radio" name="tabs" id="tab1" checked>
  <input type="radio" name="tabs" id="tab2">
  <input type="radio" name="tabs" id="tab3">
  <input type="radio" name="tabs" id="tab4">

  <div class="tab-group">
    <label for="tab1">&#127919; Overall</label>
    <label for="tab2">&#9881; CORE</label>
    <label for="tab3">&#129302; GenAI</label>
    <label for="tab4">&#128200; Breakdown</label>
  </div>

  <div class="tabs">
    <div class="tab-panel-1">{tab1}{tab_insights.get("overall", "")}</div>
    <div class="tab-panel-2">{tab2}{tab_insights.get("core", "")}</div>
    <div class="tab-panel-3">{tab3}{tab_insights.get("genai", "")}</div>
    <div class="tab-panel-4">{tab4}{tab_insights.get("breakdown", "")}</div>
  </div>

  <div class="footer">
    CMHK Forecast Report | {cycle_id} | Generated by Swarm | {generated}
  </div>
</div>
</body>
</html>"""
