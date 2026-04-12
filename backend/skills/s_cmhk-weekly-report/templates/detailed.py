"""Detailed template — 3-tab interactive layout with 6-Week Trend sparklines (v10).

Tab order: Overall → Usage → Revenue (Overall default checked).
Includes sparkline bars, SVG line charts, breakdown tables with CORE/GenAI 6W trends,
top 10 accounts with total 6W trend, and WoW Attribution with per-account 6W sparklines.
"""

from utils import fmt_money, fmt_delta, wow_pct, wow_str, wow_color, wow_arrow, now_cst
from html_helpers import fmt_week_dates, TD_STYLE


# =============================================================================
# Sparkline helpers
# =============================================================================

def sparkline_bars(values, color="#2b579a", width=80, height=28, mini=False):
    """Inline sparkline bar chart with adaptive scaling.

    - If values have low variation (range/max < 30%), use baseline-offset scaling
      so that small trends are visually amplified. The minimum value still shows a
      visible bar (~15% of max height), so nothing looks like zero.
    - If values have high variation, use sqrt scaling to compress extreme ranges.
    """
    if mini:
        width, height = 52, 18
    if not values or all(v == 0 for v in values):
        return (f'<span style="display:inline-block;width:{width}px;height:{height}px;'
                f'color:#ccc;font-size:9px;line-height:{height}px;text-align:center;">—</span>')
    import math
    pos_vals = [max(v, 0) for v in values]
    mn = min(pos_vals) if any(v > 0 for v in pos_vals) else 0
    mx = max(pos_vals) or 1
    rng = mx - mn
    # Decide scaling mode: baseline-offset for low-variance, sqrt for high-variance
    low_variance = mn > 0 and (rng / mx) < 0.30
    if low_variance:
        # Baseline at 85% of min — min value gets ~15% bar height, not zero
        baseline = mn * 0.85
        scale_max = mx - baseline
    min_bar = 3 if not mini else 2
    bw = max(width // len(values) - 2, 3 if mini else 4)
    bars = ""
    for i, v in enumerate(values):
        pv = max(v, 0)
        if low_variance:
            fraction = (pv - baseline) / scale_max if scale_max > 0 else 1
            bh = max(int(fraction * (height - 4)), min_bar)
        else:
            # sqrt scaling for high-variance data
            sv = math.sqrt(pv) if pv > 0 else 0
            sqrt_mx = math.sqrt(mx) or 1
            bh = max(int(sv / sqrt_mx * (height - 4)), min_bar) if pv > 0 else min_bar
        op = "0.4" if i < len(values) - 1 else "1.0"
        bars += (f'<span style="display:inline-block;width:{bw}px;height:{bh}px;'
                 f'background:{color};opacity:{op};border-radius:1px;'
                 f'vertical-align:bottom;margin-right:1px;"></span>')
    return (f'<span style="display:inline-flex;align-items:flex-end;'
            f'width:{width}px;height:{height}px;padding:1px 0;">{bars}</span>')


def tc_mini(values, color):
    return (f'<td style="{TD_STYLE}text-align:center;vertical-align:middle;padding:4px 3px;">'
            f'{sparkline_bars(values, color=color, mini=True)}</td>')


def tc_norm(values, color, border=True):
    bl = "border-left:2px solid #e8eaed;" if border else ""
    return (f'<td style="{TD_STYLE}text-align:center;{bl}vertical-align:middle;">'
            f'{sparkline_bars(values, color=color)}</td>')


def sparkline_line(values, color="#2b579a", width=90, height=32):
    """Generate an inline SVG sparkline line chart."""
    if not values or all(v == 0 for v in values):
        return ""
    mn = min(values)
    mx = max(values)
    rng = mx - mn if mx != mn else 1
    pad = 2
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (width - 2 * pad) / (len(values) - 1)
        y = pad + (1 - (v - mn) / rng) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(pts)
    fill_pts = polyline + f" {width - pad:.1f},{height - pad:.1f} {pad:.1f},{height - pad:.1f}"
    return (
        f'<svg width="{width}" height="{height}" style="vertical-align:middle;margin-left:8px;">'
        f'<polyline points="{fill_pts}" fill="{color}" fill-opacity="0.08" stroke="none"/>'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-opacity="0.35" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{pts[-1].split(",")[0]}" cy="{pts[-1].split(",")[1]}" r="2" '
        f'fill="{color}" fill-opacity="0.6"/>'
        f'</svg>'
    )


# =============================================================================
# Mover section with 6W sparklines
# =============================================================================

def mover_section_6w(title, emoji, color, total_cw, total_pw, accels, decels,
                     acct_6w, metric, genai_flag=None):
    """WoW Attribution section with 6W trend per account row.

    metric: 'usage' or 'revenue'
    genai_flag: None=total, 'CORE', 'GENAI'
    """
    total_delta = total_cw - total_pw
    total_pct = wow_pct(total_cw, total_pw)
    tc = wow_color(total_pct)
    ta = wow_arrow(total_pct)

    if genai_flag == "CORE":
        key_prefix = "core_"
        spark_color = "#0ea5e9" if metric == "usage" else "#d97706"
    elif genai_flag == "GENAI":
        key_prefix = "genai_"
        spark_color = "#6d28d9" if metric == "usage" else "#059669"
    else:
        key_prefix = ""
        spark_color = "#1e40af" if metric == "usage" else "#1a365d"
    val_key = f"{key_prefix}{'usage' if metric == 'usage' else 'rev'}"

    html = (
        f'<h2 style="font-size:16px;color:#1a365d;margin:28px 0 12px;padding-bottom:8px;'
        f'border-bottom:2px solid #e8eaed;">{emoji} {title}</h2>'
        f'<div style="background:#f8f9fc;padding:14px 18px;margin-bottom:16px;border-radius:4px;'
        f'border-left:4px solid {color};">'
        f'<span style="font-size:18px;font-weight:700;color:#1a365d;">{fmt_money(total_cw)}</span>'
        f'<span style="font-size:14px;color:{tc};font-weight:600;margin-left:12px;">'
        f'{ta} {wow_str(total_pct)} WoW ({fmt_delta(total_delta)})</span>'
        f'<span style="font-size:12px;color:#999;margin-left:12px;">prev: {fmt_money(total_pw)}</span>'
        f'</div>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:8px;">'
        f'<tr><td width="49%" style="vertical-align:top;">'
    )

    empty6 = [{"rev": 0, "usage": 0, "core_rev": 0, "core_usage": 0,
               "genai_rev": 0, "genai_usage": 0}] * 6

    def _mover_table(movers, is_accel):
        if is_accel:
            hdr_bg, hdr_color, hdr_border = "#e8f5e9", "#0a7c42", "#0a7c42"
            hdr_label = "▲ Top Accelerators"
            delta_color = "#0a7c42"
        else:
            hdr_bg, hdr_color, hdr_border = "#ffebee", "#d13438", "#d13438"
            hdr_label = "▼ Top Decelerators"
            delta_color = "#d13438"

        t = (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;font-size:12px;">'
            f'<tr style="background:{hdr_bg};">'
            f'<th colspan="5" style="padding:6px 10px;text-align:left;font-weight:600;'
            f'color:{hdr_color};border-bottom:2px solid {hdr_border};">{hdr_label}</th></tr>'
            '<tr style="background:#f0f4f8;">'
            '<th style="padding:5px 8px;text-align:left;font-weight:500;color:#666;font-size:11px;'
            'border-bottom:1px solid #ddd;">Account</th>'
            '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;'
            'border-bottom:1px solid #ddd;">CW</th>'
            '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;'
            'border-bottom:1px solid #ddd;">Delta</th>'
            '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;'
            'border-bottom:1px solid #ddd;">WoW</th>'
            '<th style="padding:5px 4px;text-align:center;font-weight:500;color:#666;font-size:10px;'
            'border-bottom:1px solid #ddd;width:58px;">6W</th>'
            '</tr>'
        )
        for m in movers:
            trend = acct_6w.get(m["name"], empty6)
            vals = [d.get(val_key, d.get("usage" if metric == "usage" else "rev", 0)) for d in trend]
            spark = sparkline_bars(vals, color=spark_color, mini=True)
            t += (
                f'<tr><td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:12px;'
                f'max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{m["name"]}</td>'
                f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;'
                f'font-size:12px;">{fmt_money(m["cw"])}</td>'
                f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;'
                f'font-size:12px;color:{delta_color};font-weight:600;">{fmt_delta(m["delta"])}</td>'
                f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;'
                f'font-size:12px;color:{delta_color};">{wow_str(m["pct"])}</td>'
                f'<td style="padding:4px 3px;text-align:center;border-bottom:1px solid #eee;'
                f'vertical-align:middle;">{spark}</td>'
                f'</tr>'
            )
        if not movers:
            t += (f'<tr><td colspan="5" style="padding:8px;color:#999;font-size:12px;">'
                  f'No {"accelerators" if is_accel else "decelerators"} this week</td></tr>')
        t += '</table>'
        return t

    html += _mover_table(accels, True)
    html += '</td><td width="2%">&nbsp;</td><td width="49%" style="vertical-align:top;">'
    html += _mover_table(decels, False)
    html += '</td></tr></table>'
    return html


# =============================================================================
# Main HTML generation
# =============================================================================

def generate_detailed_html(bu_name, data, weeks, six_weeks, seg_6w, acct_6w,
                           total_movers, segment_label="Unit (sh_l4)"):
    """Generate a tabbed HTML report with 6-week trend sparklines.

    Args:
        bu_name: display name in header.
        data: dict from data.fetch_detailed_data().
        weeks: anchor/week dict.
        six_weeks: list of 6 week dicts from compute_6_weeks().
        seg_6w: dict from fetch_6w_segment_data_split().
        acct_6w: dict from fetch_6w_account_data_split().
        total_movers: dict with total_usg/total_rev accel/decel.
        segment_label: label for breakdown headers.
    """
    wf = fmt_week_dates(weeks)
    cw_start_fmt, cw_end_fmt, year = wf["cw_start_fmt"], wf["cw_end_fmt"], wf["year"]
    wk_labels = [w["label"] for w in six_weeks]
    o = data["overall"]
    l4_rows = data["l4_rows"]
    acct_rev = data["acct_rows_by_rev"]
    acct_usg = data["acct_rows_by_usg"]
    movers = data["movers"]
    last_refresh = data["last_refresh"]
    is_gcr = (segment_label == "BU (sh_l3)")
    is_l4 = (segment_label == "Team (sh_l5)")
    header_title = ("CMHK Weekly Usage &amp; Revenue Report" if is_gcr
                    else f"{bu_name} — Weekly Usage &amp; Revenue Report")
    if is_gcr:
        scope_line = "Scope: GCR Overall (all BUs)"
    elif is_l4:
        scope_line = f"Scope: sh_l4 = {bu_name}"
    else:
        scope_line = f"Scope: sh_l3 = {bu_name}"

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

    # Colors
    CORE_USG_COLOR = "#0ea5e9"
    GENAI_USG_COLOR = "#6d28d9"
    TOTAL_USG_COLOR = "#1e40af"
    CORE_REV_COLOR = "#d97706"
    GENAI_REV_COLOR = "#059669"
    TOTAL_REV_COLOR = "#1a365d"

    # --- Summary card with 6W sparkline line ---
    def _summary(label, cw, pw, color, cl, ccw, cpw, cc, gl, gcw, gpw, gc, mix_total,
                 trend_total=None, trend_core=None, trend_genai=None):
        pct = wow_pct(cw, pw); c = wow_color(pct); a = wow_arrow(pct); d = cw - pw
        cp = wow_pct(ccw, cpw); ccc = wow_color(cp); ca = wow_arrow(cp)
        gp = wow_pct(gcw, gpw); gcc = wow_color(gp); ga = wow_arrow(gp)
        cm = f"{ccw / mix_total * 100:.1f}%" if mix_total > 0 else "–"
        gm = f"{gcw / mix_total * 100:.1f}%" if mix_total > 0 else "–"
        total_svg = sparkline_line(trend_total, color=color, width=180, height=48) if trend_total else ""
        core_svg = sparkline_line(trend_core, color=cc, width=120, height=30) if trend_core else ""
        genai_svg = sparkline_line(trend_genai, color=gc, width=120, height=30) if trend_genai else ""
        chart_right = f'<div style="flex-shrink:0;display:flex;align-items:center;">{total_svg}</div>' if total_svg else ''
        return f'''<div style="background:#f0f4f8;padding:18px 24px;border-left:4px solid {color};margin-bottom:20px;border-radius:0 4px 4px 0;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="flex:1;min-width:0;">
        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;font-weight:600;">{label}</div>
        <div style="font-size:26px;font-weight:700;color:#1a365d;">{fmt_money(cw)}</div>
        <div style="font-size:13px;color:{c};font-weight:600;margin-top:3px;">{a} {wow_str(pct)} ({fmt_delta(d)}) <span style="font-weight:400;color:#999;margin-left:6px;">prev: {fmt_money(pw)}</span></div>
      </div>
      {chart_right}
    </div>
    <div style="display:flex;gap:16px;margin-top:14px;padding-top:12px;border-top:1px solid #dce3eb;">
      <div style="flex:1;padding-left:10px;border-left:3px solid {cc};display:flex;justify-content:space-between;align-items:center;">
        <div>
          <div style="font-size:9px;color:#888;text-transform:uppercase;letter-spacing:0.6px;">{cl}</div>
          <div style="font-size:15px;font-weight:700;color:#1a365d;">{fmt_money(ccw)} <span style="font-size:11px;font-weight:600;color:{ccc};">{ca} {wow_str(cp)}</span></div>
          <div style="font-size:9px;color:#999;">{cm} mix</div>
        </div>
        <div style="flex-shrink:0;">{core_svg}</div>
      </div>
      <div style="flex:1;padding-left:10px;border-left:3px solid {gc};display:flex;justify-content:space-between;align-items:center;">
        <div>
          <div style="font-size:9px;color:#888;text-transform:uppercase;letter-spacing:0.6px;">{gl}</div>
          <div style="font-size:15px;font-weight:700;color:#1a365d;">{fmt_money(gcw)} <span style="font-size:11px;font-weight:600;color:{gcc};">{ga} {wow_str(gp)}</span></div>
          <div style="font-size:9px;color:#999;">{gm} mix</div>
        </div>
        <div style="flex-shrink:0;">{genai_svg}</div>
      </div>
    </div></div>'''

    # --- Breakdown row ---
    def _brow(name, ccw, cpw, gcw, gpw, trend, metric, is_total=False):
        cp = wow_pct(ccw, cpw); gp = wow_pct(gcw, gpw)
        ccc = wow_color(cp); ca = wow_arrow(cp)
        gcc = wow_color(gp); ga = wow_arrow(gp)
        w = "700" if is_total else "400"
        bg = "#f0f4f8" if is_total else "white"
        bt = "border-top:2px solid #2b579a;" if is_total else ""
        k = "usage" if metric == "usage" else "rev"
        _cc = CORE_USG_COLOR if metric == "usage" else CORE_REV_COLOR
        gcol = GENAI_USG_COLOR if metric == "usage" else GENAI_REV_COLOR
        tcol = TOTAL_USG_COLOR if metric == "usage" else TOTAL_REV_COLOR
        return (
            f'<tr style="background:{bg};{bt}">'
            f'<td style="{TD_STYLE}font-weight:{w};">{name}</td>'
            f'<td style="{TD_STYLE}text-align:right;">{fmt_money(ccw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(cpw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{ccc};font-weight:600">{ca} {wow_str(cp)}</td>'
            f'{tc_mini([d[f"core_{k}"] for d in trend], _cc)}'
            f'<td style="{TD_STYLE}text-align:right;border-left:2px solid #e8eaed;">{fmt_money(gcw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(gpw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{gcc};font-weight:600">{ga} {wow_str(gp)}</td>'
            f'{tc_mini([d[f"genai_{k}"] for d in trend], gcol)}'
            f'{tc_norm([d[k] for d in trend], tcol)}'
            f'</tr>'
        )

    # --- Top10 row ---
    def _t10row(rank, name, ccw, cp, gcw, gp, tcw, tp, trend_vals, trend_color):
        ccc = wow_color(cp); ca = wow_arrow(cp)
        gcc = wow_color(gp); ga = wow_arrow(gp)
        ttc = wow_color(tp); tta = wow_arrow(tp)
        return (
            f'<tr style="background:white;">'
            f'<td style="{TD_STYLE}text-align:center;color:#999;">{rank}</td>'
            f'<td style="{TD_STYLE}">{name}</td>'
            f'<td style="{TD_STYLE}text-align:right;">{fmt_money(ccw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{ccc};font-weight:600">{ca} {wow_str(cp)}</td>'
            f'<td style="{TD_STYLE}text-align:right;border-left:2px solid #e8eaed;">{fmt_money(gcw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{gcc};font-weight:600">{ga} {wow_str(gp)}</td>'
            f'<td style="{TD_STYLE}text-align:right;border-left:2px solid #e8eaed;font-weight:700;">{fmt_money(tcw)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{ttc};font-weight:600">{tta} {wow_str(tp)}</td>'
            f'{tc_norm(trend_vals, trend_color)}'
            f'</tr>'
        )

    # --- Overall row ---
    def _orow(name, cu, pu, up, cr, pr, rp, ut, rt, is_total=False, rank=None):
        uc = wow_color(up); ua = wow_arrow(up)
        rc = wow_color(rp); ra = wow_arrow(rp)
        w = "700" if is_total else "400"
        bg = "#f0f4f8" if is_total else "white"
        bt = "border-top:2px solid #2b579a;" if is_total else ""
        ns = f'font-weight:{w};' if is_total else ''
        rk = (f'<td style="{TD_STYLE}text-align:center;color:#999;">{rank}</td>'
              if rank is not None else '')
        return (
            f'<tr style="background:{bg};{bt}">{rk}'
            f'<td style="{TD_STYLE}{ns}">{name}</td>'
            f'<td style="{TD_STYLE}text-align:right;font-weight:{w}">{fmt_money(cu)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(pu)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{uc};font-weight:600">{ua} {wow_str(up)}</td>'
            f'<td style="{TD_STYLE}text-align:center;border-left:2px solid #e8eaed;">'
            f'{sparkline_bars(ut, color=TOTAL_USG_COLOR)}</td>'
            f'<td style="{TD_STYLE}text-align:right;font-weight:{w};border-left:2px solid #e8eaed;">'
            f'{fmt_money(cr)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(pr)}</td>'
            f'<td style="{TD_STYLE}text-align:right;color:{rc};font-weight:600">{ra} {wow_str(rp)}</td>'
            f'<td style="{TD_STYLE}text-align:center;border-left:2px solid #e8eaed;">'
            f'{sparkline_bars(rt, color=TOTAL_REV_COLOR)}</td>'
            f'</tr>'
        )

    # --- Table headers ---
    def _bkdn_hdr(cl, cc, gl, gc, total_trend_label):
        return (
            f'<thead><tr style="background:#f0f4f8;">'
            f'<th rowspan="2" style="padding:8px 10px;text-align:left;font-weight:600;color:#2b579a;'
            f'border-bottom:2px solid #2b579a;vertical-align:bottom;">Segment</th>'
            f'<th colspan="4" style="padding:8px 10px;text-align:center;font-weight:600;color:{cc};'
            f'border-bottom:1px solid #c0c8d4;">{cl}</th>'
            f'<th colspan="4" style="padding:8px 10px;text-align:center;color:{gc};font-weight:600;'
            f'border-bottom:1px solid #c0c8d4;border-left:2px solid #e8eaed;">{gl}</th>'
            f'<th rowspan="2" style="padding:8px 4px;text-align:center;font-weight:600;color:#1a365d;'
            f'border-bottom:2px solid #2b579a;border-left:2px solid #e8eaed;vertical-align:bottom;'
            f'width:90px;font-size:10px;line-height:1.3;">{total_trend_label}</th>'
            f'</tr><tr style="background:#f0f4f8;">'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:11px;">CW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:11px;">PW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:11px;">WoW</th>'
            f'<th style="padding:6px 4px;text-align:center;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:10px;width:58px;">6W</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:11px;border-left:2px solid #e8eaed;">CW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:11px;">PW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:11px;">WoW</th>'
            f'<th style="padding:6px 4px;text-align:center;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:10px;width:58px;">6W</th>'
            f'</tr></thead>'
        )

    def _t10_hdr(cl, cc, gl, gc, total_trend_label):
        return (
            f'<thead><tr style="background:#f0f4f8;">'
            f'<th rowspan="2" style="padding:8px 10px;text-align:center;font-weight:600;color:#2b579a;'
            f'border-bottom:2px solid #2b579a;vertical-align:bottom;width:30px;">#</th>'
            f'<th rowspan="2" style="padding:8px 10px;text-align:left;font-weight:600;color:#2b579a;'
            f'border-bottom:2px solid #2b579a;vertical-align:bottom;">Account</th>'
            f'<th colspan="2" style="padding:8px 10px;text-align:center;font-weight:600;color:{cc};'
            f'border-bottom:1px solid #c0c8d4;">{cl}</th>'
            f'<th colspan="2" style="padding:8px 10px;text-align:center;font-weight:600;color:{gc};'
            f'border-bottom:1px solid #c0c8d4;border-left:2px solid #e8eaed;">{gl}</th>'
            f'<th style="padding:8px 10px;text-align:center;font-weight:600;color:#1a365d;'
            f'border-bottom:1px solid #c0c8d4;border-left:2px solid #e8eaed;">Total</th>'
            f'<th style="padding:8px 10px;text-align:center;font-weight:600;color:#1a365d;'
            f'border-bottom:1px solid #c0c8d4;">WoW</th>'
            f'<th rowspan="2" style="padding:8px 4px;text-align:center;font-weight:600;color:#1a365d;'
            f'border-bottom:2px solid #2b579a;border-left:2px solid #e8eaed;vertical-align:bottom;'
            f'width:90px;font-size:10px;line-height:1.3;">{total_trend_label}</th>'
            f'</tr><tr style="background:#f0f4f8;">'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:11px;">CW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {cc};font-size:11px;">WoW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:11px;border-left:2px solid #e8eaed;">CW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid {gc};font-size:11px;">WoW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid #1a365d;font-size:11px;border-left:2px solid #e8eaed;">CW</th>'
            f'<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            f'border-bottom:2px solid #1a365d;font-size:11px;">%</th>'
            f'</tr></thead>'
        )

    def _overall_hdr(has_rank=False):
        rank_th = ('<th rowspan="2" style="padding:8px 10px;text-align:center;font-weight:600;color:#2b579a;'
                   'border-bottom:2px solid #2b579a;vertical-align:bottom;width:30px;">#</th>'
                   ) if has_rank else ''
        name_label = "Account" if has_rank else "Segment"
        return (
            '<thead><tr style="background:#f0f4f8;">'
            f'{rank_th}'
            f'<th rowspan="2" style="padding:8px 10px;text-align:left;font-weight:600;color:#2b579a;'
            f'border-bottom:2px solid #2b579a;vertical-align:bottom;">{name_label}</th>'
            '<th colspan="3" style="padding:8px 10px;text-align:center;font-weight:600;color:#0a7c42;'
            'border-bottom:1px solid #c0c8d4;">Usage</th>'
            '<th rowspan="2" style="padding:8px 6px;text-align:center;font-weight:600;color:#0a7c42;'
            'border-bottom:2px solid #2b579a;border-left:2px solid #e8eaed;vertical-align:bottom;'
            'width:90px;font-size:10px;line-height:1.3;">Total Usage<br/>6W Trend</th>'
            '<th colspan="3" style="padding:8px 10px;text-align:center;font-weight:600;color:#2b579a;'
            'border-bottom:1px solid #c0c8d4;border-left:2px solid #e8eaed;">Revenue</th>'
            '<th rowspan="2" style="padding:8px 6px;text-align:center;font-weight:600;color:#2b579a;'
            'border-bottom:2px solid #2b579a;border-left:2px solid #e8eaed;vertical-align:bottom;'
            'width:90px;font-size:10px;line-height:1.3;">Total Revenue<br/>6W Trend</th>'
            '</tr><tr style="background:#f0f4f8;">'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #0a7c42;font-size:11px;">CW</th>'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #0a7c42;font-size:11px;">PW</th>'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #0a7c42;font-size:11px;">WoW</th>'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #2b579a;font-size:11px;border-left:2px solid #e8eaed;">CW</th>'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #2b579a;font-size:11px;">PW</th>'
            '<th style="padding:6px 10px;text-align:right;font-weight:500;color:#666;'
            'border-bottom:2px solid #2b579a;font-size:11px;">WoW</th>'
            '</tr></thead>'
        )

    # --- Overall card with optional sparkline ---
    def _ocard(label, cw, pw, color, is_big=False, mix_str=None, trend=None):
        pct = wow_pct(cw, pw); c = wow_color(pct); a = wow_arrow(pct); d = cw - pw
        if is_big:
            svg = sparkline_line(trend, color=color, width=140, height=40) if trend else ""
            chart = f'<div style="flex-shrink:0;">{svg}</div>' if svg else ''
            return (
                f'<div style="flex:1;min-width:200px;background:#f0f4f8;padding:16px 18px;'
                f'border-left:4px solid {color};">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<div><div style="font-size:11px;color:#666;text-transform:uppercase;'
                f'letter-spacing:1px;margin-bottom:4px;font-weight:600;">{label}</div>'
                f'<div style="font-size:24px;font-weight:700;color:#1a365d;">{fmt_money(cw)}</div>'
                f'<div style="font-size:13px;color:{c};font-weight:600;margin-top:3px;">'
                f'{a} {wow_str(pct)} ({fmt_delta(d)})</div>'
                f'<div style="font-size:11px;color:#999;margin-top:2px;">prev: {fmt_money(pw)}</div></div>'
                f'{chart}</div></div>'
            )
        svg = sparkline_line(trend, color=color, width=90, height=26) if trend else ""
        chart = f'<div style="flex-shrink:0;">{svg}</div>' if svg else ''
        ml = f'<div style="font-size:10px;color:#999;margin-top:1px;">Mix: {mix_str}</div>' if mix_str else ''
        return (
            f'<div style="flex:1;min-width:180px;background:#f8f9fc;padding:10px 12px;'
            f'border-left:3px solid {color};">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div><div style="font-size:9px;color:#666;text-transform:uppercase;'
            f'letter-spacing:0.8px;margin-bottom:2px;font-weight:600;">{label}</div>'
            f'<div style="font-size:17px;font-weight:700;color:#1a365d;">{fmt_money(cw)}</div>'
            f'<div style="font-size:11px;color:{c};font-weight:600;margin-top:2px;">'
            f'{a} {wow_str(pct)} ({fmt_delta(d)})</div>'
            f'<div style="font-size:9px;color:#999;margin-top:1px;">prev: {fmt_money(pw)}</div>'
            f'{ml}</div>{chart}</div></div>'
        )

    # Compute mix percentages
    cum = f"{o['core_cw_usg'] / o['total_cw_usg'] * 100:.1f}%" if o['total_cw_usg'] > 0 else "–"
    gum = f"{o['genai_cw_usg'] / o['total_cw_usg'] * 100:.1f}%" if o['total_cw_usg'] > 0 else "–"
    crm = f"{o['core_cw_rev'] / o['total_cw_rev'] * 100:.1f}%" if o['total_cw_rev'] > 0 else "–"
    grm = f"{o['genai_cw_rev'] / o['total_cw_rev'] * 100:.1f}%" if o['total_cw_rev'] > 0 else "–"

    # === BUILD USAGE ROWS ===
    us = _summary("Overall Usage", o["total_cw_usg"], o["total_pw_usg"], "#1e40af",
                  "CORE", o["core_cw_usg"], o["core_pw_usg"], "#0ea5e9",
                  "GenAI", o["genai_cw_usg"], o["genai_pw_usg"], "#6d28d9",
                  o["total_cw_usg"],
                  trend_total=[d["usage"] for d in total_6w],
                  trend_core=[d["core_usage"] for d in total_6w],
                  trend_genai=[d["genai_usage"] for d in total_6w])
    ubr = "".join(
        _brow(r["name"], r["core_cw_usg"], r["core_pw_usg"],
              r["genai_cw_usg"], r["genai_pw_usg"],
              seg_6w.get(r["name"], empty6), "usage")
        for r in l4_rows)
    ubr += _brow("TOTAL", o["core_cw_usg"], o["core_pw_usg"],
                 o["genai_cw_usg"], o["genai_pw_usg"], total_6w, "usage", True)
    ut10 = "".join(
        _t10row(i, r["name"],
                r["core_cw_usg"], wow_pct(r["core_cw_usg"], r["core_pw_usg"]),
                r["genai_cw_usg"], wow_pct(r["genai_cw_usg"], r["genai_pw_usg"]),
                r["cw_usg"], wow_pct(r["cw_usg"], r["pw_usg"]),
                [d["usage"] for d in acct_6w.get(r["name"], empty6)], TOTAL_USG_COLOR)
        for i, r in enumerate(acct_usg, 1))

    # === BUILD REVENUE ROWS ===
    rs = _summary("Overall Revenue", o["total_cw_rev"], o["total_pw_rev"], "#1a365d",
                  "CORE", o["core_cw_rev"], o["core_pw_rev"], "#d97706",
                  "GenAI", o["genai_cw_rev"], o["genai_pw_rev"], "#059669",
                  o["total_cw_rev"],
                  trend_total=[d["rev"] for d in total_6w],
                  trend_core=[d["core_rev"] for d in total_6w],
                  trend_genai=[d["genai_rev"] for d in total_6w])
    rbr = "".join(
        _brow(r["name"], r["core_cw_rev"], r["core_pw_rev"],
              r["genai_cw_rev"], r["genai_pw_rev"],
              seg_6w.get(r["name"], empty6), "revenue")
        for r in l4_rows)
    rbr += _brow("TOTAL", o["core_cw_rev"], o["core_pw_rev"],
                 o["genai_cw_rev"], o["genai_pw_rev"], total_6w, "revenue", True)
    rt10 = "".join(
        _t10row(i, r["name"],
                r["core_cw_rev"], wow_pct(r["core_cw_rev"], r["core_pw_rev"]),
                r["genai_cw_rev"], wow_pct(r["genai_cw_rev"], r["genai_pw_rev"]),
                r["cw_rev"], wow_pct(r["cw_rev"], r["pw_rev"]),
                [d["rev"] for d in acct_6w.get(r["name"], empty6)], TOTAL_REV_COLOR)
        for i, r in enumerate(acct_rev, 1))

    # === BUILD OVERALL ROWS ===
    obr = "".join(
        _orow(r["name"],
              r["cw_usg"], r["pw_usg"], wow_pct(r["cw_usg"], r["pw_usg"]),
              r["cw_rev"], r["pw_rev"], wow_pct(r["cw_rev"], r["pw_rev"]),
              [d["usage"] for d in seg_6w.get(r["name"], empty6)],
              [d["rev"] for d in seg_6w.get(r["name"], empty6)])
        for r in l4_rows)
    obr += _orow("TOTAL",
                 o["total_cw_usg"], o["total_pw_usg"],
                 wow_pct(o["total_cw_usg"], o["total_pw_usg"]),
                 o["total_cw_rev"], o["total_pw_rev"],
                 wow_pct(o["total_cw_rev"], o["total_pw_rev"]),
                 [d["usage"] for d in total_6w], [d["rev"] for d in total_6w], True)

    wk_legend = " → ".join(wk_labels)
    wk_range = f"{six_weeks[0]['start']} ~ {six_weeks[-1]['end']}"
    header_line_icon = ('<svg width="28" height="12" style="vertical-align:middle;margin-right:4px;">'
                        '<polyline points="2,10 7,6 12,8 17,3 22,5 26,2" fill="none" stroke="#b0c4de" '
                        'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')

    # === OVERALL TOP 10 (GM only, not CEO/GCR) ===
    if is_gcr:
        overall_top10_section = ""
    else:
        # Sort accounts by revenue delta (CW - PW), descending
        acct_all = {}
        for r in acct_rev:
            acct_all[r["name"]] = r
        for r in acct_usg:
            if r["name"] not in acct_all:
                acct_all[r["name"]] = r
            else:
                # merge usage data in case acct_rev missed usage fields
                acct_all[r["name"]].update({k: v for k, v in r.items() if k != "name" and v != 0 and acct_all[r["name"]].get(k, 0) == 0})
        sorted_accts = sorted(acct_all.values(), key=lambda x: x.get("cw_rev", 0) - x.get("pw_rev", 0), reverse=True)[:10]

        ot10_rows = "".join(
            _orow(r["name"],
                  r.get("cw_usg", 0), r.get("pw_usg", 0),
                  wow_pct(r.get("cw_usg", 0), r.get("pw_usg", 0)),
                  r.get("cw_rev", 0), r.get("pw_rev", 0),
                  wow_pct(r.get("cw_rev", 0), r.get("pw_rev", 0)),
                  [d["usage"] for d in acct_6w.get(r["name"], empty6)],
                  [d["rev"] for d in acct_6w.get(r["name"], empty6)],
                  rank=i)
            for i, r in enumerate(sorted_accts, 1))

        overall_top10_section = (
            f'\n  <h2 class="section-title">👑 Top 10 Accounts (by Revenue Δ)</h2>\n'
            f'  <table class="data-table">{_overall_hdr(has_rank=True)}'
            f'<tbody>{ot10_rows}</tbody></table>'
        )

    # === MOVER SECTIONS WITH 6W ===
    m_total_usg = mover_section_6w(
        "Total Usage — WoW Attribution", "📊", "#14532d",
        o["total_cw_usg"], o["total_pw_usg"],
        total_movers["total_usg"]["accel"], total_movers["total_usg"]["decel"],
        acct_6w, "usage", None)
    m_core_usg = mover_section_6w(
        "CORE Usage — WoW Attribution", "⚡", "#0a7c42",
        o["core_cw_usg"], o["core_pw_usg"],
        movers["core_usg"]["accel"], movers["core_usg"]["decel"],
        acct_6w, "usage", "CORE")
    m_genai_usg = mover_section_6w(
        "GenAI Usage — WoW Attribution", "🔮", "#7c3aed",
        o["genai_cw_usg"], o["genai_pw_usg"],
        movers["genai_usg"]["accel"], movers["genai_usg"]["decel"],
        acct_6w, "usage", "GENAI")
    m_total_rev = mover_section_6w(
        "Total Revenue — WoW Attribution", "📊", "#1a365d",
        o["total_cw_rev"], o["total_pw_rev"],
        total_movers["total_rev"]["accel"], total_movers["total_rev"]["decel"],
        acct_6w, "revenue", None)
    m_core_rev = mover_section_6w(
        "CORE Revenue — WoW Attribution", "💰", "#2b579a",
        o["core_cw_rev"], o["core_pw_rev"],
        movers["core_rev"]["accel"], movers["core_rev"]["decel"],
        acct_6w, "revenue", "CORE")
    m_genai_rev = mover_section_6w(
        "GenAI Revenue — WoW Attribution", "🤖", "#6b46c1",
        o["genai_cw_rev"], o["genai_pw_rev"],
        movers["genai_rev"]["accel"], movers["genai_rev"]["decel"],
        acct_6w, "revenue", "GENAI")

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box;}}
body{{margin:0;padding:0;background:#f5f6fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;}}
.report{{max-width:1150px;margin:0 auto;background:white;}}
.header{{background:#1e3a5f;padding:28px 32px;}}
.header h1{{margin:0;font-size:22px;font-weight:600;letter-spacing:0.5px;color:white;}}
.header p{{margin:6px 0 0;font-size:14px;color:#b0c4de;}}
.tab-radio{{display:none;}}
.tab-bar{{display:flex;gap:6px;padding:8px 32px 0;background:#e8ecf1;border-bottom:none;}}
.tab-label{{padding:10px 22px;font-size:13px;font-weight:600;color:#5a6a7a;cursor:pointer;border:1px solid #c8d0da;border-bottom:none;border-radius:8px 8px 0 0;margin-bottom:-1px;background:linear-gradient(180deg,#f0f3f7 0%,#e4e8ee 100%);box-shadow:0 -1px 3px rgba(0,0,0,0.06);transition:all 0.15s ease;user-select:none;position:relative;}}
.tab-label:hover{{color:#1a365d;background:linear-gradient(180deg,#f8f9fc 0%,#eef1f5 100%);}}
.tab-panel{{display:none;padding:24px 32px;border-top:1px solid #c8d0da;}}
#tab-overall:checked ~ .tab-bar .lbl-overall,
#tab-usage:checked ~ .tab-bar .lbl-usage,
#tab-revenue:checked ~ .tab-bar .lbl-revenue{{color:#1a365d;background:white;border-color:#c8d0da;border-bottom-color:white;box-shadow:0 -2px 6px rgba(0,0,0,0.08);z-index:1;}}
#tab-overall:checked ~ .panel-overall,
#tab-usage:checked ~ .panel-usage,
#tab-revenue:checked ~ .panel-revenue{{display:block;}}
.section-title{{font-size:16px;color:#1a365d;margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid #e8eaed;}}
.data-table{{width:100%;border-collapse:collapse;font-size:13px;}}
.data-table th{{background:#f0f4f8;padding:8px 10px;font-weight:600;}}
.data-table td{{padding:7px 10px;border-bottom:1px solid #e8eaed;}}
.footer{{background:#f5f6fa;padding:16px 32px;font-size:12px;color:#888;border-top:1px solid #e8eaed;}}
.footer p{{margin:0;}} .footer p+p{{margin-top:4px;}}
</style></head><body>
<div class="report">
<div class="header">
  <h1>📊 {header_title}</h1>
  <p>{cw_start_fmt} – {cw_end_fmt}, {year} (Sun–Sat, {weeks["cw_days"]} days) | Generated by DataRetriever 🐕</p>
  <p style="font-size:12px;color:#8ba5c4;margin-top:4px;">{header_line_icon}6W Trend: {wk_legend} ({wk_range})</p>
</div>

<input type="radio" name="tab" id="tab-overall" class="tab-radio" checked>
<input type="radio" name="tab" id="tab-usage" class="tab-radio">
<input type="radio" name="tab" id="tab-revenue" class="tab-radio">

<div class="tab-bar">
  <label for="tab-overall" class="tab-label lbl-overall">📋 Overall</label>
  <label for="tab-usage" class="tab-label lbl-usage" style="text-align:center;">⚡ Usage<br/><span style="font-size:10px;font-weight:400;color:#888;display:block;text-align:center;">Core / GenAI</span></label>
  <label for="tab-revenue" class="tab-label lbl-revenue" style="text-align:center;">💰 Revenue<br/><span style="font-size:10px;font-weight:400;color:#888;display:block;text-align:center;">Core / GenAI</span></label>
</div>

<!-- OVERALL -->
<div class="tab-panel panel-overall">
  <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
    {_ocard("Overall Usage", o["total_cw_usg"], o["total_pw_usg"], "#1e40af", True, trend=[d["usage"] for d in total_6w])}
    {_ocard("Overall Revenue", o["total_cw_rev"], o["total_pw_rev"], "#1a365d", True, trend=[d["rev"] for d in total_6w])}
  </div>
  <div style="display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap;">
    {_ocard("CORE Usage", o["core_cw_usg"], o["core_pw_usg"], "#0ea5e9", mix_str=cum, trend=[d["core_usage"] for d in total_6w])}
    {_ocard("GenAI Usage", o["genai_cw_usg"], o["genai_pw_usg"], "#6d28d9", mix_str=gum, trend=[d["genai_usage"] for d in total_6w])}
    {_ocard("CORE Revenue", o["core_cw_rev"], o["core_pw_rev"], "#d97706", mix_str=crm, trend=[d["core_rev"] for d in total_6w])}
    {_ocard("GenAI Revenue", o["genai_cw_rev"], o["genai_pw_rev"], "#059669", mix_str=grm, trend=[d["genai_rev"] for d in total_6w])}
  </div>
  <h2 class="section-title">📊 Overall Usage &amp; Revenue by {segment_label}</h2>
  <table class="data-table">{_overall_hdr()}<tbody>{obr}</tbody></table>
{overall_top10_section}
</div>

<!-- USAGE -->
<div class="tab-panel panel-usage">
  {us}
  <h2 class="section-title">⚡ Usage by {segment_label} — CORE vs GenAI</h2>
  <table class="data-table">{_bkdn_hdr("CORE Usage", CORE_USG_COLOR, "GenAI Usage", GENAI_USG_COLOR, "Total Usage<br/>6W Trend")}<tbody>{ubr}</tbody></table>
  <h2 class="section-title">👑 Top 10 Accounts by Usage</h2>
  <table class="data-table">{_t10_hdr("CORE Usage", CORE_USG_COLOR, "GenAI Usage", GENAI_USG_COLOR, "Total Usage<br/>6W Trend")}<tbody>{ut10}</tbody></table>
  {m_total_usg}{m_core_usg}{m_genai_usg}
</div>

<!-- REVENUE -->
<div class="tab-panel panel-revenue">
  {rs}
  <h2 class="section-title">💰 Revenue by {segment_label} — CORE vs GenAI</h2>
  <table class="data-table">{_bkdn_hdr("CORE Revenue", CORE_REV_COLOR, "GenAI Revenue", GENAI_REV_COLOR, "Total Revenue<br/>6W Trend")}<tbody>{rbr}</tbody></table>
  <h2 class="section-title">👑 Top 10 Accounts by Revenue</h2>
  <table class="data-table">{_t10_hdr("CORE Revenue", CORE_REV_COLOR, "GenAI Revenue", GENAI_REV_COLOR, "Total Revenue<br/>6W Trend")}<tbody>{rt10}</tbody></table>
  {m_total_rev}{m_core_rev}{m_genai_rev}
</div>

<div class="footer">
  <p>Data source: fact_estimated_revenue (Athena, cn-north-1) &bull; FBR only &bull; Revenue = all charge types &bull; Usage = Net Usage only &bull; {scope_line}</p>
  <p>6W Trend: {wk_legend} &bull; Rightmost bar = CW</p>
  <p>Data refreshed: {last_refresh} &bull; Report generated: {now_cst()}</p>
</div>
</div></body></html>'''
    return html
