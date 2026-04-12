"""Shared HTML generation helpers — used by both summary and detailed templates."""

from datetime import datetime

from utils import fmt_money, fmt_delta, wow_pct, wow_str, wow_color, wow_arrow


def fmt_week_dates(weeks):
    """Parse week date strings into formatted labels."""
    cw_start_dt = datetime.strptime(weeks["cw_start"], "%Y-%m-%d")
    cw_end_dt = datetime.strptime(weeks["cw_end"], "%Y-%m-%d")
    pw_start_dt = datetime.strptime(weeks["pw_start"], "%Y-%m-%d")
    pw_end_dt = datetime.strptime(weeks["pw_end"], "%Y-%m-%d")
    return {
        "cw_start_fmt": cw_start_dt.strftime("%b %d"),
        "cw_end_fmt": cw_end_dt.strftime("%b %d"),
        "pw_start_fmt": pw_start_dt.strftime("%b %d"),
        "pw_end_fmt": pw_end_dt.strftime("%b %d"),
        "year": cw_start_dt.year,
    }


TD_STYLE = 'padding:7px 10px;border-bottom:1px solid #e8eaed;font-size:13px;'


def data_row(name, cu, pu, upct, cr, pr, rpct, is_total=False, rank=None):
    """Generate a single table <tr> for breakdown / top-account tables."""
    rc = wow_color(rpct); ra = wow_arrow(rpct)
    uc = wow_color(upct); ua = wow_arrow(upct)
    w = "700" if is_total else "400"
    bg = "#f0f4f8" if is_total else "white"
    bt = "border-top:2px solid #2b579a;" if is_total else ""
    ns = f'font-weight:{w};' if is_total else ''
    rank_td = f'<td style="{TD_STYLE}text-align:center;color:#999;">{rank}</td>' if rank is not None else ''
    return (
        f'<tr style="background:{bg};{bt}">'
        f'{rank_td}'
        f'<td style="{TD_STYLE}{ns}">{name}</td>'
        f'<td style="{TD_STYLE}text-align:right;font-weight:{w}">{fmt_money(cu)}</td>'
        f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(pu)}</td>'
        f'<td style="{TD_STYLE}text-align:right;color:{uc};font-weight:600">{ua} {wow_str(upct)}</td>'
        f'<td style="{TD_STYLE}text-align:right;font-weight:{w};border-left:2px solid #e8eaed;">{fmt_money(cr)}</td>'
        f'<td style="{TD_STYLE}text-align:right;color:#888">{fmt_money(pr)}</td>'
        f'<td style="{TD_STYLE}text-align:right;color:{rc};font-weight:600">{ra} {wow_str(rpct)}</td>'
        f'</tr>'
    )


def mover_section(title, emoji, color, total_cw, total_pw, accels, decels):
    """Generate a WoW Attribution section (Accelerators + Decelerators)."""
    total_delta = total_cw - total_pw
    total_pct = wow_pct(total_cw, total_pw)
    tc = wow_color(total_pct)
    ta = wow_arrow(total_pct)

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

    # Accelerators
    html += (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;font-size:12px;">'
        '<tr style="background:#e8f5e9;">'
        '<th colspan="4" style="padding:6px 10px;text-align:left;font-weight:600;color:#0a7c42;'
        'border-bottom:2px solid #0a7c42;">▲ Top Accelerators</th></tr>'
        '<tr style="background:#f0f4f8;">'
        '<th style="padding:5px 8px;text-align:left;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">Account</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">CW</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">Delta</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">WoW</th>'
        '</tr>'
    )
    for m in accels:
        html += (
            '<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:12px;max-width:160px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{m["name"]}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;">{fmt_money(m["cw"])}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;color:#0a7c42;font-weight:600;">{fmt_delta(m["delta"])}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;color:#0a7c42;">{wow_str(m["pct"])}</td>'
            '</tr>'
        )
    if not accels:
        html += '<tr><td colspan="4" style="padding:8px;color:#999;font-size:12px;">No accelerators this week</td></tr>'
    html += '</table>'

    html += '</td><td width="2%">&nbsp;</td><td width="49%" style="vertical-align:top;">'

    # Decelerators
    html += (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;font-size:12px;">'
        '<tr style="background:#ffebee;">'
        '<th colspan="4" style="padding:6px 10px;text-align:left;font-weight:600;color:#d13438;'
        'border-bottom:2px solid #d13438;">▼ Top Decelerators</th></tr>'
        '<tr style="background:#f0f4f8;">'
        '<th style="padding:5px 8px;text-align:left;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">Account</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">CW</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">Delta</th>'
        '<th style="padding:5px 8px;text-align:right;font-weight:500;color:#666;font-size:11px;border-bottom:1px solid #ddd;">WoW</th>'
        '</tr>'
    )
    for m in decels:
        html += (
            '<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:12px;max-width:160px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{m["name"]}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;">{fmt_money(m["cw"])}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;color:#d13438;font-weight:600;">{fmt_delta(m["delta"])}</td>'
            f'<td style="padding:5px 8px;text-align:right;border-bottom:1px solid #eee;font-size:12px;color:#d13438;">{wow_str(m["pct"])}</td>'
            '</tr>'
        )
    if not decels:
        html += '<tr><td colspan="4" style="padding:8px;color:#999;font-size:12px;">No decelerators this week</td></tr>'
    html += '</table>'

    html += '</td></tr></table>'
    return html
