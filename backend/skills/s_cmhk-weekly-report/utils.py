"""Shared utility functions — zero external dependencies."""

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def fmt_money(v):
    """Format dollar amounts."""
    if abs(v) >= 1e6:
        return f"${v/1e6:,.2f}M"
    elif abs(v) >= 1e3:
        return f"${v/1e3:,.1f}K"
    else:
        return f"${v:,.0f}"


def fmt_delta(v):
    """Format delta with sign, always show."""
    sign = "+" if v >= 0 else ""
    if abs(v) >= 1e6:
        return f"{sign}${v/1e6:,.2f}M"
    elif abs(v) >= 1e3:
        return f"{sign}${v/1e3:,.1f}K"
    else:
        return f"{sign}${v:,.0f}"


def wow_pct(cw, pw):
    if pw == 0:
        return 0
    return (cw - pw) / abs(pw) * 100


def wow_str(pct):
    """Format WoW%. abs < 1% → no sign (e.g. '0.7%'); abs >= 1% → with sign."""
    if abs(pct) < 1.0:
        return f"{abs(pct):.1f}%"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def wow_color(pct):
    if pct > 1:
        return "#0a7c42"
    elif pct < -1:
        return "#d13438"
    else:
        return "#666666"


def wow_arrow(pct):
    """WoW arrow. abs < 1% → empty string (no dash); abs >= 1% → ▲/▼."""
    if pct > 1:
        return "▲"
    elif pct < -1:
        return "▼"
    else:
        return ""


def safe_name(bu_name):
    """Convert BU name to filesystem-safe string."""
    return bu_name.replace(" ", "_").replace("&", "and").lower()


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M CST (UTC+8)")
