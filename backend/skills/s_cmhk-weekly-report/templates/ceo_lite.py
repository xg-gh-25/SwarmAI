"""CEO Lite template — EMAIL-SAFE version (Outlook compatible).

All styles fully inlined. No <style>, no CSS classes, no flex/grid.
Pure nested-table layout. Sparklines rendered as SVG → PNG → base64 <img> tags.
Works in Gmail, Outlook Desktop, Outlook Web, Apple Mail.

Design: white background, minimal chrome, KPI cards with line chart images,
breakdown tables with mini line chart images.
"""

from utils import fmt_money, fmt_delta, wow_pct, wow_str, wow_color, wow_arrow, now_cst
from html_helpers import fmt_week_dates
import math
import base64

try:
    import cairosvg
    _HAS_CAIRO = True
except ImportError:
    _HAS_CAIRO = False


# ── SVG → PNG base64 <img> sparkline helpers ─────────────────────────────

def _svg_to_img(svg_str, width, height):
    """Convert SVG string to base64 PNG <img> tag for email embedding."""
    if not _HAS_CAIRO:
        return f'<span style="color:#999;">[sparkline]</span>'
    png_data = cairosvg.svg2png(bytestring=svg_str.encode(), scale=2)
    b64 = base64.b64encode(png_data).decode()
    return (
        f'<img src="data:image/png;base64,{b64}" '
        f'width="{width}" height="{height}" '
        f'style="vertical-align:middle;border:0;display:inline-block;" />'
    )


def _make_svg(values, color, width, height):
    """Generate SVG line chart string (not rendered directly — converted to PNG)."""
    if not values or all(v == 0 for v in values):
        return None
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    pad = 2
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (width - 2 * pad) / max(len(values) - 1, 1)
        y = pad + (1 - (v - mn) / rng) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(pts)
    fill_pts = polyline + f" {width - pad:.1f},{height - pad:.1f} {pad:.1f},{height - pad:.1f}"
    lx, ly = pts[-1].split(",")
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{fill_pts}" fill="{color}" fill-opacity="0.10" stroke="none"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-opacity="0.5" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx}" cy="{ly}" r="2" fill="{color}" fill-opacity="0.8"/>'
        f'</svg>'
    )


def _spark_img(values, color="#1a1a1a", width=56, height=18):
    """Mini line chart as base64 PNG <img> for table cells."""
    if not values or all(v == 0 for v in values):
        return '<span style="color:#999;">—</span>'
    svg = _make_svg(values, color, width, height)
    return _svg_to_img(svg, width, height)


def _spark_img_big(values, color="#1a1a1a", width=120, height=40):
    """Larger line chart for KPI big cards."""
    if not values or all(v == 0 for v in values):
        return ""
    svg = _make_svg(values, color, width, height)
    return _svg_to_img(svg, width, height)


def _spark_img_sub(values, color="#1a1a1a", width=80, height=26):
    """Medium line chart for sub-KPI cards."""
    if not values or all(v == 0 for v in values):
        return ""
    svg = _make_svg(values, color, width, height)
    return _svg_to_img(svg, width, height)


# ── Color constants ──────────────────────────────────────────────────────

TOTAL_USG_COLOR = "#2563eb"
CORE_USG_COLOR = "#3b82f6"
GENAI_USG_COLOR = "#6366f1"
TOTAL_REV_COLOR = "#d97706"
CORE_REV_COLOR = "#d97706"
GENAI_REV_COLOR = "#dc2626"


# ── WoW display helpers ─────────────────────────────────────────────────

def _wow_style(pct):
    """Return inline style for WoW change. Threshold: abs >= 1% colored, < 1% gray."""
    if pct is None:
        return "color:#888;"
    if pct >= 1.0:
        return "color:#0a7c42;font-weight:600;"
    elif pct <= -1.0:
        return "color:#d13438;font-weight:600;"
    return "color:#999;font-weight:600;"


def _wow_text(pct):
    """Return display text for WoW change with arrow. abs >= 1% gets arrow."""
    if pct is None:
        return "N/A"
    if pct >= 1.0:
        return f"▲ {wow_str(pct)}"
    elif pct <= -1.0:
        return f"▼ {wow_str(pct)}"
    return wow_str(pct)


def _wow_td(pct):
    """Fully inline <td> for WoW%."""
    s = _wow_style(pct)
    return f'<td style="text-align:right;padding:4px 5px;border-bottom:1px solid #eee;font-size:11px;white-space:nowrap;{s}">{_wow_text(pct)}</td>'


def _chg_span(pct):
    """Inline span for change % in KPI cards."""
    s = _wow_style(pct)
    return f'<span style="font-size:11px;{s}">{_wow_text(pct)}</span>'


def _kpi_change_div(pct, delta):
    """Change line for big KPI cards."""
    s = _wow_style(pct)
    return f'<div style="font-size:12px;font-weight:600;margin-top:2px;{s}">{_wow_text(pct)} ({fmt_delta(delta)})</div>'


# ── Main HTML generation ────────────────────────────────────────────────

def generate_ceo_lite_html(bu_name, data, weeks, six_weeks, seg_6w, acct_6w,
                           total_movers, segment_label="BU (sh_l3)"):
    """Generate CEO Lite HTML report — fully email-safe, all inline styles."""
    wf = fmt_week_dates(weeks)
    cw_start_fmt, cw_end_fmt, year = wf["cw_start_fmt"], wf["cw_end_fmt"], wf["year"]
    wk_labels = [w["label"] for w in six_weeks]
    wk_range = f"{six_weeks[0]['start']} ~ {six_weeks[-1]['end']}"
    o = data["overall"]
    l4_rows = data["l4_rows"]
    last_refresh = data["last_refresh"]

    empty6 = [{"rev": 0, "usage": 0, "core_rev": 0, "core_usage": 0,
               "genai_rev": 0, "genai_usage": 0}] * 6

    def _total_6w():
        t = []
        for i in range(6):
            d = {"rev": 0, "usage": 0, "core_rev": 0, "core_usage": 0,
                 "genai_rev": 0, "genai_usage": 0}
            for r in l4_rows:
                s = seg_6w.get(r["name"], empty6)
                for k in d:
                    d[k] += s[i].get(k, 0)
            t.append(d)
        return t

    total_6w = _total_6w()

    # Mix %
    cum = f"{o['core_cw_usg'] / o['total_cw_usg'] * 100:.1f}%" if o['total_cw_usg'] > 0 else "–"
    gum = f"{o['genai_cw_usg'] / o['total_cw_usg'] * 100:.1f}%" if o['total_cw_usg'] > 0 else "–"
    crm = f"{o['core_cw_rev'] / o['total_cw_rev'] * 100:.1f}%" if o['total_cw_rev'] > 0 else "–"
    grm = f"{o['genai_cw_rev'] / o['total_cw_rev'] * 100:.1f}%" if o['total_cw_rev'] > 0 else "–"

    # WoW %
    usg_pct = wow_pct(o["total_cw_usg"], o["total_pw_usg"])
    rev_pct = wow_pct(o["total_cw_rev"], o["total_pw_rev"])
    cu_pct = wow_pct(o["core_cw_usg"], o["core_pw_usg"])
    gu_pct = wow_pct(o["genai_cw_usg"], o["genai_pw_usg"])
    cr_pct = wow_pct(o["core_cw_rev"], o["core_pw_rev"])
    gr_pct = wow_pct(o["genai_cw_rev"], o["genai_pw_rev"])

    usg_delta = o["total_cw_usg"] - o["total_pw_usg"]
    rev_delta = o["total_cw_rev"] - o["total_pw_rev"]

    # Unicode sparklines for KPI cards
    usg_svg = _spark_img_big([d["usage"] for d in total_6w], TOTAL_USG_COLOR)
    rev_svg = _spark_img_big([d["rev"] for d in total_6w], TOTAL_REV_COLOR)
    cu_svg = _spark_img_sub([d["core_usage"] for d in total_6w], CORE_USG_COLOR)
    gu_svg = _spark_img_sub([d["genai_usage"] for d in total_6w], GENAI_USG_COLOR)
    cr_svg = _spark_img_sub([d["core_rev"] for d in total_6w], CORE_REV_COLOR)
    gr_svg = _spark_img_sub([d["genai_rev"] for d in total_6w], GENAI_REV_COLOR)

    # ── Common inline styles ─────────────────────────────────────────
    S_TD = "padding:4px 5px;border-bottom:1px solid #eee;font-size:11px;white-space:nowrap;"
    S_TD_R = f"{S_TD}text-align:right;"
    S_TD_C = f"{S_TD}text-align:center;"
    S_TD_M = f"{S_TD_R}color:#888;"  # muted (PW column)
    S_TD_SEP = "border-left:2px solid #e8eaed;"  # section separator
    S_TH = "background:#f0f4f8;padding:4px 5px;font-weight:600;border-bottom:2px solid #c8d0da;font-size:10px;"
    S_TH_R = f"{S_TH}text-align:right;"
    S_TH_C = f"{S_TH}text-align:center;"
    S_TOT_TD = f"{S_TD}font-weight:700;background:#f0f4f8;"
    S_TOT_TD_R = f"{S_TOT_TD}text-align:right;"
    S_TOT_TD_C = f"{S_TOT_TD}text-align:center;"

    # ── Build Usage table rows ───────────────────────────────────────
    usg_rows = ""
    for r in l4_rows:
        trend = seg_6w.get(r["name"], empty6)
        total_usg = r.get("cw_usg", r["core_cw_usg"] + r["genai_cw_usg"])
        total_pw = r.get("pw_usg", r["core_pw_usg"] + r["genai_pw_usg"])
        tp = wow_pct(total_usg, total_pw)
        cp = wow_pct(r["core_cw_usg"], r["core_pw_usg"])
        gp = wow_pct(r["genai_cw_usg"], r["genai_pw_usg"])
        usg_rows += (
            f'<tr>'
            f'<td style="{S_TD}">{r["name"]}</td>'
            f'<td style="{S_TD_R}">{fmt_money(total_usg)}</td>'
            f'<td style="{S_TD_M}">{fmt_money(total_pw)}</td>'
            f'{_wow_td(tp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["usage"] for d in trend])}</td>'
            f'<td style="{S_TD_R}{S_TD_SEP}">{fmt_money(r["core_cw_usg"])}</td>'
            f'<td style="{S_TD_M}">{fmt_money(r["core_pw_usg"])}</td>'
            f'{_wow_td(cp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["core_usage"] for d in trend], CORE_USG_COLOR)}</td>'
            f'<td style="{S_TD_R}{S_TD_SEP}">{fmt_money(r["genai_cw_usg"])}</td>'
            f'<td style="{S_TD_M}">{fmt_money(r["genai_pw_usg"])}</td>'
            f'{_wow_td(gp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["genai_usage"] for d in trend], GENAI_USG_COLOR)}</td>'
            f'</tr>'
        )

    # Total row
    usg_rows += (
        f'<tr>'
        f'<td style="{S_TOT_TD}border-top:2px solid #333;">TOTAL</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;">{fmt_money(o["total_cw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["total_pw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(usg_pct)}">{_wow_text(usg_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["usage"] for d in total_6w])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{S_TD_SEP}">{fmt_money(o["core_cw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["core_pw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(cu_pct)}">{_wow_text(cu_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["core_usage"] for d in total_6w], CORE_USG_COLOR)}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{S_TD_SEP}">{fmt_money(o["genai_cw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["genai_pw_usg"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(gu_pct)}">{_wow_text(gu_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["genai_usage"] for d in total_6w], GENAI_USG_COLOR)}</td>'
        f'</tr>'
    )

    # ── Build Revenue table rows ─────────────────────────────────────
    rev_rows = ""
    for r in l4_rows:
        trend = seg_6w.get(r["name"], empty6)
        total_rev = r.get("cw_rev", r["core_cw_rev"] + r["genai_cw_rev"])
        total_pw = r.get("pw_rev", r["core_pw_rev"] + r["genai_pw_rev"])
        tp = wow_pct(total_rev, total_pw)
        cp = wow_pct(r["core_cw_rev"], r["core_pw_rev"])
        gp = wow_pct(r["genai_cw_rev"], r["genai_pw_rev"])
        rev_rows += (
            f'<tr>'
            f'<td style="{S_TD}">{r["name"]}</td>'
            f'<td style="{S_TD_R}">{fmt_money(total_rev)}</td>'
            f'<td style="{S_TD_M}">{fmt_money(total_pw)}</td>'
            f'{_wow_td(tp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["rev"] for d in trend])}</td>'
            f'<td style="{S_TD_R}{S_TD_SEP}">{fmt_money(r["core_cw_rev"])}</td>'
            f'<td style="{S_TD_M}">{fmt_money(r["core_pw_rev"])}</td>'
            f'{_wow_td(cp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["core_rev"] for d in trend], CORE_REV_COLOR)}</td>'
            f'<td style="{S_TD_R}{S_TD_SEP}">{fmt_money(r["genai_cw_rev"])}</td>'
            f'<td style="{S_TD_M}">{fmt_money(r["genai_pw_rev"])}</td>'
            f'{_wow_td(gp)}'
            f'<td style="{S_TD_C}{S_TD_SEP}">{_spark_img([d["genai_rev"] for d in trend], GENAI_REV_COLOR)}</td>'
            f'</tr>'
        )

    # Total row
    rev_rows += (
        f'<tr>'
        f'<td style="{S_TOT_TD}border-top:2px solid #333;">TOTAL</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;">{fmt_money(o["total_cw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["total_pw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(rev_pct)}">{_wow_text(rev_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["rev"] for d in total_6w])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{S_TD_SEP}">{fmt_money(o["core_cw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["core_pw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(cr_pct)}">{_wow_text(cr_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["core_rev"] for d in total_6w], CORE_REV_COLOR)}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{S_TD_SEP}">{fmt_money(o["genai_cw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;color:#888;">{fmt_money(o["genai_pw_rev"])}</td>'
        f'<td style="{S_TOT_TD_R}border-top:2px solid #333;{_wow_style(gr_pct)}">{_wow_text(gr_pct)}</td>'
        f'<td style="{S_TOT_TD_C}border-top:2px solid #333;{S_TD_SEP}">{_spark_img([d["genai_rev"] for d in total_6w], GENAI_REV_COLOR)}</td>'
        f'</tr>'
    )

    from datetime import date
    cw_start = date.fromisoformat(weeks["cw_start"])
    wk_num = cw_start.isocalendar()[1]

    # ── Assemble full HTML ───────────────────────────────────────────
    # KPI big card helper
    def _big_kpi(label, value, change_html, prev_val, svg, border_color):
        return (
            f'<td style="vertical-align:top;width:49%;">'
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%"'
            f' style="background:#f8fafc;border-left:4px solid {border_color};">'
            f'<tr>'
            f'<td style="padding:14px 16px;vertical-align:top;">'
            f'<div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:0.8px;font-weight:600;margin-bottom:3px;">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:#1a1a1a;">{value}</div>'
            f'{change_html}'
            f'<div style="font-size:10px;color:#999;margin-top:1px;">prev: {prev_val}</div>'
            f'</td>'
            f'<td style="padding:14px 8px;vertical-align:top;text-align:right;width:130px;">{svg}</td>'
            f'</tr></table>'
            f'</td>'
        )

    # Sub KPI card helper
    def _sub_kpi(label, value, chg_span, mix_text, svg, border_color):
        return (
            f'<td style="vertical-align:top;">'
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%"'
            f' style="background:#f8fafc;border-left:3px solid {border_color};">'
            f'<tr>'
            f'<td style="padding:8px 10px;vertical-align:top;">'
            f'<div style="font-size:9px;color:#888;text-transform:uppercase;letter-spacing:0.6px;font-weight:600;">{label}</div>'
            f'<div style="font-size:15px;font-weight:700;color:#1a1a1a;">{value} {chg_span}</div>'
            f'<div style="font-size:9px;color:#999;">{mix_text}</div>'
            f'</td>'
            f'<td style="padding:8px 4px;vertical-align:top;text-align:right;width:90px;">{svg}</td>'
            f'</tr></table>'
            f'</td>'
        )

    # Table header helpers
    def _usage_header():
        return (
            f'<thead>'
            f'<tr>'
            f'<th rowspan="2" style="{S_TH}vertical-align:bottom;">Segment</th>'
            f'<th colspan="3" style="{S_TH_C}color:#1a1a1a;border-bottom:1px solid #c0c8d4;">Total Usage</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:56px;{S_TD_SEP}">6W</th>'
            f'<th colspan="3" style="{S_TH_C}color:#3b82f6;border-bottom:1px solid #c0c8d4;{S_TD_SEP}">Core Usage</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:50px;{S_TD_SEP}">6W</th>'
            f'<th colspan="3" style="{S_TH_C}color:#6366f1;border-bottom:1px solid #c0c8d4;{S_TD_SEP}">GenAI Usage</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:50px;{S_TD_SEP}">6W</th>'
            f'</tr><tr>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">WoW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #3b82f6;{S_TD_SEP}">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #3b82f6;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #3b82f6;">WoW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #6366f1;{S_TD_SEP}">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #6366f1;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #6366f1;">WoW</th>'
            f'</tr></thead>'
        )

    def _revenue_header():
        return (
            f'<thead>'
            f'<tr>'
            f'<th rowspan="2" style="{S_TH}vertical-align:bottom;">Segment</th>'
            f'<th colspan="3" style="{S_TH_C}color:#1a1a1a;border-bottom:1px solid #c0c8d4;">Total Revenue</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:56px;{S_TD_SEP}">6W</th>'
            f'<th colspan="3" style="{S_TH_C}color:#d97706;border-bottom:1px solid #c0c8d4;{S_TD_SEP}">Core Revenue</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:50px;{S_TD_SEP}">6W</th>'
            f'<th colspan="3" style="{S_TH_C}color:#dc2626;border-bottom:1px solid #c0c8d4;{S_TD_SEP}">GenAI Revenue</th>'
            f'<th rowspan="2" style="{S_TH_C}vertical-align:bottom;width:50px;{S_TD_SEP}">6W</th>'
            f'</tr><tr>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #1a1a1a;">WoW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #d97706;{S_TD_SEP}">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #d97706;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #d97706;">WoW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #dc2626;{S_TD_SEP}">CW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #dc2626;">PW</th>'
            f'<th style="{S_TH_R}border-bottom:2px solid #dc2626;">WoW</th>'
            f'</tr></thead>'
        )

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;color:#1a1a1a;line-height:1.5;">
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:1100px;margin:0 auto;">
<tr><td style="padding:24px 32px;">

<!-- HEADER -->
<div style="margin-bottom:20px;">
 <div style="margin:0;font-size:18px;font-weight:600;color:#1a1a1a;">📊 CMHK Weekly Usage &amp; Revenue Report</div>
 <div style="margin:4px 0 0;font-size:12px;color:#888;">W{wk_num} | {cw_start_fmt} – {cw_end_fmt}, {year}</div>
 <div style="margin:2px 0 0;font-size:12px;color:#888;">6W Trend: {" → ".join(wk_labels)} ({wk_range})</div>
</div>

<!-- BIG KPI CARDS — pure table layout -->
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr>
{_big_kpi("Overall Usage", fmt_money(o["total_cw_usg"]),
          _kpi_change_div(usg_pct, usg_delta), fmt_money(o["total_pw_usg"]),
          usg_svg, "#2563eb")}
<td style="width:2%;"></td>
{_big_kpi("Overall Revenue", fmt_money(o["total_cw_rev"]),
          _kpi_change_div(rev_pct, rev_delta), fmt_money(o["total_pw_rev"]),
          rev_svg, "#d97706")}
</tr>
</table>

<!-- SUB KPI CARDS — 4 columns -->
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:24px;">
<tr>
{_sub_kpi("Core Usage", fmt_money(o["core_cw_usg"]), _chg_span(cu_pct), f"{cum} mix", cu_svg, CORE_USG_COLOR)}
<td style="width:1%;"></td>
{_sub_kpi("GenAI Usage", fmt_money(o["genai_cw_usg"]), _chg_span(gu_pct), f"{gum} mix", gu_svg, GENAI_USG_COLOR)}
<td style="width:1%;"></td>
{_sub_kpi("Core Revenue", fmt_money(o["core_cw_rev"]), _chg_span(cr_pct), f"{crm} mix", cr_svg, CORE_REV_COLOR)}
<td style="width:1%;"></td>
{_sub_kpi("GenAI Revenue", fmt_money(o["genai_cw_rev"]), _chg_span(gr_pct), f"{grm} mix", gr_svg, GENAI_REV_COLOR)}
</tr>
</table>

<!-- USAGE TABLE -->
<div style="font-size:15px;color:#1a1a1a;margin:24px 0 10px;padding-bottom:6px;border-bottom:2px solid #e8eaed;font-weight:600;">⚡ Usage by BU — Core / GenAI</div>
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;font-size:12px;margin-bottom:4px;">
{_usage_header()}
<tbody>
{usg_rows}
</tbody>
</table>

<!-- REVENUE TABLE -->
<div style="font-size:15px;color:#1a1a1a;margin:24px 0 10px;padding-bottom:6px;border-bottom:2px solid #e8eaed;font-weight:600;">💰 Revenue by BU — Core / GenAI</div>
<table cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse;font-size:12px;margin-bottom:4px;">
{_revenue_header()}
<tbody>
{rev_rows}
</tbody>
</table>

<!-- FOOTER -->
<div style="margin-top:20px;padding-top:10px;border-top:1px solid #eee;font-size:11px;color:#999;">
 Data source: fact_estimated_revenue | Generated by DataRetriever 🐕
</div>

</td></tr>
</table>
</body></html>'''

    return html
