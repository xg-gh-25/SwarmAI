"""
GM level detailed report — 6-tab interactive Chinese HTML with CSS-only charts.
Tabs: 总览 / 客户分析 / 行业调研 / GTM打法 / 优先级 / 竞品对比
"""


def _bar_row(name: str, value: float, max_val: float, color: str = "#1F6FEB") -> str:
    pct = (value / max_val * 100) if max_val > 0 else 0
    return f'''<div style="display:flex;align-items:center;margin-bottom:3px;font-size:0.75rem;">
        <div style="width:120px;text-align:right;padding-right:6px;color:#8B949E;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{name}</div>
        <div style="flex:1;height:16px;background:#21262D;border-radius:3px;overflow:hidden;">
            <div style="height:100%;width:{pct:.1f}%;background:{color};"></div>
        </div>
        <div style="width:70px;padding-left:6px;color:#8B949E;font-size:0.7rem;">${value:,.0f}</div>
    </div>'''


def render_gm_detailed(data: dict) -> str:
    """
    Render GM-level detailed HTML report with 6 interactive tabs.

    Args:
        data: dict with keys: bu_name, product, total_accounts, total_ttm,
              total_genai, total_bedrock, auto_count, mfg_count,
              top_accounts, bedrock_accounts, categories, plays, scenarios, competitive
    """
    bu = data.get("bu_name", "N/A")
    product = data.get("product", "N/A")
    total = data.get("total_accounts", 0)
    ttm = data.get("total_ttm", 0)
    genai = data.get("total_genai", 0)
    bedrock = data.get("total_bedrock", 0)
    top = data.get("top_accounts", [])
    br_accts = data.get("bedrock_accounts", [])

    # Top accounts chart
    max_ttm = top[0]["ttm"] if top else 1
    top_bars = "\n".join(_bar_row(a["name"][:15], a["ttm"], max_ttm) for a in top[:15])

    # Bedrock chart
    max_br = br_accts[0]["bedrock"] if br_accts else 1
    br_bars = "\n".join(_bar_row(a["name"][:15], a["bedrock"], max_br, "#3FB950") for a in br_accts[:10])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{product} GTM 详细分析 — {bu}</title>
<style>
body {{ font-family: -apple-system, 'PingFang SC', sans-serif; background: #0D1117; color: #E6EDF3; line-height: 1.6; font-size: 14px; }}
body::before {{ content: '机密 — AWS 内部'; position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%) rotate(-35deg); font-size: 5rem; font-weight: 800; color: rgba(255,153,0,0.03); pointer-events: none; z-index: 9999; }}
.wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 32px; }}
h1 {{ color: #FF9900; font-size: 1.5rem; border-bottom: 3px solid #FF9900; padding-bottom: 10px; }}
.tabs input[type=radio] {{ display: none; }}
.tab-labels {{ display: flex; gap: 4px; border-bottom: 2px solid #30363D; }}
.tab-labels label {{ padding: 8px 14px; font-size: 0.82rem; font-weight: 600; color: #8B949E; cursor: pointer; border-radius: 6px 6px 0 0; }}
.tab-labels label:hover {{ color: #E6EDF3; background: #161B22; }}
.tc {{ display: none; padding-top: 16px; }}
#t1:checked ~ .tab-labels label[for=t1],
#t2:checked ~ .tab-labels label[for=t2],
#t3:checked ~ .tab-labels label[for=t3],
#t4:checked ~ .tab-labels label[for=t4],
#t5:checked ~ .tab-labels label[for=t5],
#t6:checked ~ .tab-labels label[for=t6] {{ color: #FF9900; background: #161B22; border-bottom: 2px solid #FF9900; margin-bottom: -2px; }}
#t1:checked ~ .tc1, #t2:checked ~ .tc2, #t3:checked ~ .tc3,
#t4:checked ~ .tc4, #t5:checked ~ .tc5, #t6:checked ~ .tc6 {{ display: block; }}
.row4 {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 14px; }}
.card {{ background: #161B22; border: 1px solid #30363D; border-radius: 8px; padding: 12px; }}
.stat {{ text-align: center; }}
.stat .n {{ font-size: 1.5rem; font-weight: 800; color: #FF9900; }}
.stat .n.red {{ color: #F85149; }}
.stat .l {{ font-size: 0.7rem; color: #8B949E; }}
.stitle {{ font-size: 1rem; color: #FF9900; font-weight: 700; border-left: 3px solid #FF9900; padding-left: 8px; margin: 16px 0 10px; }}
.footer {{ font-size: 0.65rem; color: #484F58; text-align: center; margin-top: 20px; border-top: 1px solid #30363D; padding-top: 8px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>{product} GTM 详细分析 — {bu}</h1>

<div class="tabs">
<input type="radio" id="t1" name="tabs" checked>
<input type="radio" id="t2" name="tabs">
<input type="radio" id="t3" name="tabs">
<input type="radio" id="t4" name="tabs">
<input type="radio" id="t5" name="tabs">
<input type="radio" id="t6" name="tabs">

<div class="tab-labels">
<label for="t1">📊 总览</label>
<label for="t2">🔍 客户分析</label>
<label for="t3">📰 调研</label>
<label for="t4">🎯 GTM</label>
<label for="t5">⚡ 优先级</label>
<label for="t6">⚔️ 竞品</label>
</div>

<!-- Tab 1: 总览 -->
<div class="tc tc1">
<div class="row4">
<div class="card stat"><div class="n">{total}</div><div class="l">目标客户</div></div>
<div class="card stat"><div class="n">${ttm:,.0f}</div><div class="l">TTM 总收入</div></div>
<div class="card stat"><div class="n">${genai:,.0f}</div><div class="l">GenAI TTM</div></div>
<div class="card stat"><div class="n red">$0</div><div class="l">{product} Pipeline</div></div>
</div>

<div class="stitle">TTM 收入 Top 15</div>
{top_bars}

<div class="stitle">Bedrock 客户 Top 10 ({product} 首批目标)</div>
{br_bars}
</div>

<!-- Tab 2-6: placeholders for agent to fill with research/analysis -->
<div class="tc tc2"><div class="stitle">客户分析</div><p style="color:#8B949E;">此 Tab 由 agent 在 Step 3 (联网调研后) 填充，或手动编辑。</p></div>
<div class="tc tc3"><div class="stitle">行业调研</div><p style="color:#8B949E;">此 Tab 由 agent 在 Step 3 填充。</p></div>
<div class="tc tc4"><div class="stitle">GTM 打法</div><p style="color:#8B949E;">此 Tab 由 agent 基于产品知识和调研填充。</p></div>
<div class="tc tc5"><div class="stitle">优先级矩阵</div><p style="color:#8B949E;">此 Tab 由 agent 基于分析填充。</p></div>
<div class="tc tc6"><div class="stitle">竞品对比</div><p style="color:#8B949E;">此 Tab 由 agent 基于产品知识填充。</p></div>

</div><!-- tabs -->

<div class="footer">
{product} GTM 详细分析 · {bu} · 机密 — AWS 内部<br>
数据来源: DataProxy Athena + Sentral MCP · Tab 2-6 由 Agent 调研后生成
</div>
</div>
</body>
</html>"""
