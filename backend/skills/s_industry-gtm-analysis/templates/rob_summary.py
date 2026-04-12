"""
Rob/CEO level summary — 1-page Chinese HTML executive briefing.
Key stats + Top 10 + recommended actions. Email-safe inline CSS.
"""


def render_rob_summary(data: dict) -> str:
    """
    Render Rob-level executive summary HTML.

    Args:
        data: dict with keys: bu_name, product, total_accounts, total_ttm,
              total_genai, total_bedrock, top_accounts, bedrock_accounts
    """
    bu = data.get("bu_name", "N/A")
    product = data.get("product", "N/A")
    total = data.get("total_accounts", 0)
    ttm = data.get("total_ttm", 0)
    genai = data.get("total_genai", 0)
    bedrock = data.get("total_bedrock", 0)
    top = data.get("top_accounts", [])
    br_accts = data.get("bedrock_accounts", [])

    # Top accounts rows
    top_rows = ""
    for i, a in enumerate(top[:10], 1):
        top_rows += f"""<tr>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;">{i}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;">{a.get('name','')}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;text-align:right;">${a.get('ttm',0):,.0f}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;text-align:right;">${a.get('genai',0):,.0f}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;text-align:right;">${a.get('bedrock',0):,.0f}</td>
        </tr>"""

    # Bedrock accounts rows
    br_rows = ""
    for i, a in enumerate(br_accts[:5], 1):
        br_rows += f"""<tr>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;">{i}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;">{a.get('name','')}</td>
            <td style="padding:4px 8px;border-bottom:1px solid #30363D;text-align:right;">${a.get('bedrock',0):,.0f}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{product} GTM 总览 — {bu}</title>
<style>
body {{ font-family: -apple-system, 'PingFang SC', sans-serif; background: #0D1117; color: #E6EDF3; max-width: 800px; margin: 0 auto; padding: 24px; }}
body::before {{ content: '机密 — AWS 内部'; position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%) rotate(-35deg); font-size: 4rem; font-weight: 800; color: rgba(255,153,0,0.03); pointer-events: none; z-index: 9999; }}
h1 {{ color: #FF9900; font-size: 1.4rem; border-bottom: 2px solid #FF9900; padding-bottom: 8px; }}
.stats {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin: 16px 0; }}
.stat {{ background: #161B22; border: 1px solid #30363D; border-radius: 8px; padding: 12px; text-align: center; }}
.stat .n {{ font-size: 1.5rem; font-weight: 800; color: #FF9900; }}
.stat .n.red {{ color: #F85149; }}
.stat .l {{ font-size: 0.7rem; color: #8B949E; margin-top: 2px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; margin: 12px 0; }}
th {{ background: #1F4E79; color: #fff; padding: 6px 8px; text-align: left; }}
.section {{ margin: 20px 0; }}
.section h3 {{ color: #FF9900; font-size: 1rem; border-left: 3px solid #FF9900; padding-left: 8px; }}
.footer {{ font-size: 0.65rem; color: #484F58; text-align: center; margin-top: 20px; border-top: 1px solid #30363D; padding-top: 8px; }}
</style>
</head>
<body>
<h1>{product} GTM 总览 — {bu}</h1>

<div class="stats">
    <div class="stat"><div class="n">{total}</div><div class="l">目标客户</div></div>
    <div class="stat"><div class="n">${ttm:,.0f}</div><div class="l">TTM 总收入</div></div>
    <div class="stat"><div class="n">${genai:,.0f}</div><div class="l">GenAI TTM</div></div>
    <div class="stat"><div class="n">${bedrock:,.0f}</div><div class="l">Bedrock TTM</div></div>
</div>

<div class="section">
    <h3>TTM 收入 Top 10</h3>
    <table>
        <tr><th>#</th><th>客户</th><th>TTM</th><th>GenAI</th><th>Bedrock</th></tr>
        {top_rows}
    </table>
</div>

<div class="section">
    <h3>Bedrock 客户 Top 5 ({product} 首批目标)</h3>
    <table>
        <tr><th>#</th><th>客户</th><th>Bedrock TTM</th></tr>
        {br_rows}
    </table>
</div>

<div class="footer">
    {product} GTM 总览 · {bu} · 机密 — AWS 内部<br>
    数据来源: DataProxy Athena (fact_estimated_revenue) + Sentral MCP
</div>
</body>
</html>"""
