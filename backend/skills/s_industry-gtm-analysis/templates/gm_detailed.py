"""
GM level detailed report — 6-tab interactive Chinese HTML with CSS-only charts.
Tabs: 总览 / 客户分析 / 行业调研 / GTM打法 / 优先级 / 竞品对比

All tabs are now data-driven from summary_data computed by analyzer.py.
"""


def _fmt_dollar(value: float) -> str:
    """Format a dollar value with appropriate suffix."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.0f}K"
    else:
        return f"${value:,.0f}"


def _bar_row(name: str, value: float, max_val: float, color: str = "#1F6FEB") -> str:
    pct = (value / max_val * 100) if max_val > 0 else 0
    return f'''<div style="display:flex;align-items:center;margin-bottom:3px;font-size:0.75rem;">
        <div style="width:120px;text-align:right;padding-right:6px;color:#8B949E;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{name}</div>
        <div style="flex:1;height:16px;background:#21262D;border-radius:3px;overflow:hidden;">
            <div style="height:100%;width:{pct:.1f}%;background:{color};"></div>
        </div>
        <div style="width:70px;padding-left:6px;color:#8B949E;font-size:0.7rem;">{_fmt_dollar(value)}</div>
    </div>'''


def _stacked_bar_row(name: str, base: float, genai: float, bedrock: float, max_val: float) -> str:
    """Stacked bar: base (blue) + genai (orange) + bedrock (green)."""
    if max_val <= 0:
        return ""
    base_only = base - genai  # base includes genai+bedrock or just base
    base_pct = (base_only / max_val * 100)
    genai_pct = (genai / max_val * 100)
    bedrock_pct = (bedrock / max_val * 100)
    ttm = base  # total
    br_flag = " 🟢" if bedrock > 0 else ""
    return f'''<div style="display:flex;align-items:center;margin-bottom:4px;font-size:0.75rem;">
        <div style="width:130px;text-align:right;padding-right:8px;color:#8B949E;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>
        <div style="flex:1;height:18px;background:#21262D;border-radius:3px;position:relative;overflow:hidden;display:flex;">
            <div style="height:100%;width:{base_pct:.1f}%;background:#1F6FEB;"></div>
            <div style="height:100%;width:{genai_pct:.1f}%;background:#FF9900;"></div>
            <div style="height:100%;width:{bedrock_pct:.1f}%;background:#3FB950;"></div>
        </div>
        <div style="width:80px;padding-left:6px;color:#8B949E;font-size:0.7rem;">{_fmt_dollar(ttm)}{br_flag}</div>
    </div>'''


def _render_tab1(data: dict) -> str:
    """Tab 1: Overview with key stats, top accounts, bedrock chart."""
    bu = data.get("bu_name", "N/A")
    product = data.get("product", "N/A")
    total = data.get("total_accounts", 0)
    ttm = data.get("total_ttm", 0)
    genai = data.get("total_genai", 0)
    bedrock = data.get("total_bedrock", 0)
    auto_count = data.get("auto_count", 0)
    mfg_count = data.get("mfg_count", 0)
    top = data.get("top_accounts", [])
    br_accts = data.get("bedrock_accounts", [])
    br_pen = data.get("bedrock_penetration", {})
    ga_pen = data.get("genai_penetration", {})

    # Top accounts stacked bar chart
    max_ttm = top[0]["ttm"] if top else 1
    top_bars = "\n".join(
        _stacked_bar_row(a["name"][:15], a["ttm"], a.get("genai", 0), a.get("bedrock", 0), max_ttm)
        for a in top[:15]
    )

    # Bedrock chart
    max_br = br_accts[0]["bedrock"] if br_accts else 1
    br_bars = "\n".join(_bar_row(a["name"][:15], a["bedrock"], max_br, "#3FB950") for a in br_accts[:10])

    return f"""<div class="tc tc1">
<div class="row4">
<div class="card stat"><div class="n">{total}</div><div class="l">目标客户 (AUTO {auto_count} + MFG {mfg_count})</div></div>
<div class="card stat"><div class="n">{_fmt_dollar(ttm)}</div><div class="l">TTM 总收入</div></div>
<div class="card stat"><div class="n grn">{_fmt_dollar(genai)}</div><div class="l">GenAI TTM ({ga_pen.get('total_with_genai', 0)}家有用量)</div></div>
<div class="card stat"><div class="n red">$0</div><div class="l">{product} Pipeline = 绿地</div></div>
</div>
<div class="row3">
<div class="card stat"><div class="n">{_fmt_dollar(bedrock)}</div><div class="l">Bedrock TTM ({br_pen.get('total_with_bedrock', 0)}家)</div></div>
<div class="card stat"><div class="n">{ga_pen.get('pct', 0)}%</div><div class="l">企业已用 GenAI</div></div>
<div class="card stat"><div class="n">{br_pen.get('pct', 0)}%</div><div class="l">Bedrock 渗透率</div></div>
</div>

<div class="stitle">TTM 收入 Top 15 (含 GenAI / Bedrock 拆分)</div>
<div style="margin-bottom:14px;">
{top_bars}
</div>
<div style="font-size:0.7rem;color:#8B949E;margin-bottom:14px;">
图例: <span style="color:#1F6FEB">■</span> 基础用量 &nbsp; <span style="color:#FF9900">■</span> GenAI 用量 &nbsp; <span style="color:#3FB950">■</span> Bedrock 用量 &nbsp; 🟢 = 已有 Bedrock spend ({product} 自然 upsell 目标)
</div>

<div class="stitle">Bedrock 客户 Top 10 ({product} 首批目标)</div>
<div style="margin-bottom:14px;">
{br_bars}
</div>

<div class="card" style="border-left:3px solid #F85149;">
  <h4>🔑 关键判断</h4>
  <p style="font-size:0.82rem;margin-bottom:6px;"><strong>① {product} Pipeline = $0 = 纯绿地</strong> — {total} 家客户没有任何 {product} opp，但 {_fmt_dollar(genai)} GenAI 和 {_fmt_dollar(bedrock)} Bedrock 证明 AI 预算存在。先到先得。</p>
  <p style="font-size:0.82rem;margin-bottom:6px;"><strong>② Bedrock → {product} 自然 upsell</strong> — {br_pen.get('total_with_bedrock', 0)} 家已有 Bedrock 的客户最容易转化。</p>
  <p style="font-size:0.82rem;"><strong>③ GenAI 渗透率 {ga_pen.get('pct', 0)}%</strong> — 说明近半数企业已有 AI 预算，剩下的是教育成本更高的绿地。</p>
</div>
</div>"""


def _render_tab2(data: dict) -> str:
    """Tab 2: Customer analysis — AUTO vs MFG split, category bar chart, penetration."""
    categories = data.get("categories", {})
    auto_count = data.get("auto_count", 0)
    mfg_count = data.get("mfg_count", 0)
    br_pen = data.get("bedrock_penetration", {})
    ga_pen = data.get("genai_penetration", {})
    product = data.get("product", "N/A")

    # Split categories into AUTO and MFG
    auto_cats = {k: v for k, v in categories.items() if v.get("industry") == "AUTO"}
    mfg_cats = {k: v for k, v in categories.items() if v.get("industry") != "AUTO"}

    # AUTO summary
    auto_cat_summary = " / ".join(f"{k} {v.get('count', 0)}" for k, v in sorted(auto_cats.items(), key=lambda x: -x[1].get("count", 0)))
    auto_xxl = ", ".join(set(name for v in auto_cats.values() for name in v.get("xxl", [])))
    auto_bedrock_top = sorted(
        [(k, v) for k, v in auto_cats.items() if v.get("bedrock", 0) > 0],
        key=lambda x: -x[1].get("bedrock", 0)
    )
    auto_bedrock_str = ", ".join(f"{k} {_fmt_dollar(v.get('bedrock', 0))}" for k, v in auto_bedrock_top[:4])

    # MFG summary
    mfg_cat_summary = " / ".join(f"{k} {v.get('count', 0)}" for k, v in sorted(mfg_cats.items(), key=lambda x: -x[1].get("count", 0)))
    mfg_xxl = ", ".join(set(name for v in mfg_cats.values() for name in v.get("xxl", [])))
    mfg_bedrock_top = sorted(
        [(k, v) for k, v in mfg_cats.items() if v.get("bedrock", 0) > 0],
        key=lambda x: -x[1].get("bedrock", 0)
    )
    mfg_bedrock_str = ", ".join(f"{k} {_fmt_dollar(v.get('bedrock', 0))}" for k, v in mfg_bedrock_top[:5])

    # Category bar chart sorted by TTM desc
    sorted_cats = sorted(categories.items(), key=lambda x: -x[1].get("ttm", 0))
    max_cat_ttm = sorted_cats[0][1].get("ttm", 0) if sorted_cats else 1
    if max_cat_ttm <= 0:
        max_cat_ttm = 1
    cat_bars = "\n".join(
        _bar_row(name[:10], v.get("ttm", 0), max_cat_ttm, "#1F6FEB" if v.get("industry") != "AUTO" else "#D29922")
        for name, v in sorted_cats
    )

    # Bedrock top accounts
    br_accts = data.get("bedrock_accounts", [])
    top3_br = br_accts[:3]
    top3_str = " + ".join(f"{a['name']}({_fmt_dollar(a['bedrock'])})" for a in top3_br)
    top3_total = sum(a["bedrock"] for a in top3_br)
    total_bedrock = data.get("total_bedrock", 1)
    top3_pct = (top3_total / total_bedrock * 100) if total_bedrock > 0 else 0

    return f"""<div class="tc tc2">
<div class="stitle">AUTO vs MFG 分布</div>
<div class="row2">
  <div class="card">
    <h4>🚗 AUTO 汽车 — {auto_count} 家</h4>
    <p style="font-size:0.8rem;color:#8B949E;">{auto_cat_summary or '无细分'}</p>
    <p style="font-size:0.8rem;margin-top:4px;">XXL: {auto_xxl or '无'}</p>
    <p style="font-size:0.8rem;">Bedrock: {auto_bedrock_str or '无'}</p>
  </div>
  <div class="card">
    <h4>🏭 MFG 制造 — {mfg_count} 家</h4>
    <p style="font-size:0.8rem;color:#8B949E;">{mfg_cat_summary or '无细分'}</p>
    <p style="font-size:0.8rem;margin-top:4px;">XXL: {mfg_xxl or '无'}</p>
    <p style="font-size:0.8rem;">Bedrock: {mfg_bedrock_str or '无'}</p>
  </div>
</div>

<div class="stitle">按产品大类 TTM 分布</div>
<div style="margin-bottom:14px;">
{cat_bars}
</div>
<div style="font-size:0.7rem;color:#8B949E;margin-bottom:14px;">
图例: <span style="color:#D29922">■</span> AUTO 类别 &nbsp; <span style="color:#1F6FEB">■</span> MFG 类别
</div>

<div class="stitle">Bedrock 渗透率</div>
<div class="row3">
<div class="card stat"><div class="n grn">{br_pen.get('total_with_bedrock', 0)}</div><div class="l">有 Bedrock 用量 (占 {br_pen.get('pct', 0)}%)</div></div>
<div class="card stat"><div class="n">{ga_pen.get('total_with_genai', 0)}</div><div class="l">有 GenAI 用量 (占 {ga_pen.get('pct', 0)}%)</div></div>
<div class="card stat"><div class="n red">0</div><div class="l">{product} Opp (绿地)</div></div>
</div>
<p style="font-size:0.8rem;color:#8B949E;">Bedrock → {product} 自然 upsell 路径：{br_pen.get('total_with_bedrock', 0)} 家已有 Bedrock 的客户最容易转化，{top3_str} 三家占 Bedrock 总量 {top3_pct:.0f}%</p>
</div>"""


def _render_tab3(data: dict) -> str:
    """Tab 3: Industry research — for each top bedrock account, show a data card.
    Research content is flagged for agent/manual completion."""
    br_accts = data.get("bedrock_accounts", [])
    product = data.get("product", "N/A")
    categories = data.get("categories", {})

    # AUTO research section
    auto_br = [a for a in br_accts if a.get("category", "") in
               {'汽车整车', '自动驾驶/智驾', '汽车零部件', '出行/两轮'}]
    mfg_br = [a for a in br_accts if a not in auto_br]

    def _intel_card(a: dict) -> str:
        name = a.get("name", "")
        bedrock = a.get("bedrock", 0)
        cat = a.get("category", "")
        genai = a.get("genai", 0)
        ttm = a.get("ttm", 0)
        return f'''<div class="intel">
<div class="co">{name} — {cat} (Bedrock {_fmt_dollar(bedrock)})</div>
<div class="fact">TTM: {_fmt_dollar(ttm)} / GenAI: {_fmt_dollar(genai)} / Bedrock: {_fmt_dollar(bedrock)}</div>
<div class="imp">→ {product} 场景分析待补充 — Agent 在 Step 3 (联网调研) 填充具体产品、AI 战略、{product} 匹配组件</div>
</div>'''

    auto_cards = "\n".join(_intel_card(a) for a in auto_br[:5]) if auto_br else '<p style="color:#8B949E;font-size:0.82rem;">无 AUTO Bedrock 客户</p>'
    mfg_cards = "\n".join(_intel_card(a) for a in mfg_br[:8]) if mfg_br else '<p style="color:#8B949E;font-size:0.82rem;">无 MFG Bedrock 客户</p>'

    # Non-bedrock top accounts by GenAI
    genai_only = [a for a in data.get("top_accounts", [])
                  if float(a.get("genai", 0)) > 0 and float(a.get("bedrock", 0)) == 0]
    genai_cards = ""
    for a in genai_only[:5]:
        genai_cards += f'''<div class="intel">
<div class="co">{a.get("name", "")} — {a.get("category", "")} (GenAI {_fmt_dollar(a.get("genai", 0))}，无 Bedrock)</div>
<div class="fact">TTM: {_fmt_dollar(a.get("ttm", 0))} — 有 GenAI 预算但未用 Bedrock，潜在转化目标</div>
<div class="imp">→ 调研方向: 当前使用哪个 LLM 厂商？是否有自建 Agent？{product} 切入点待分析</div>
</div>'''

    return f"""<div class="tc tc3">
<div class="stitle">🚗 AUTO 行业 Bedrock 客户调研</div>
{auto_cards}

<div class="stitle">🏭 MFG 行业 Bedrock 客户调研</div>
{mfg_cards}

<div class="stitle">📊 GenAI 客户 (无 Bedrock) — 潜在转化目标</div>
{genai_cards or '<p style="color:#8B949E;font-size:0.82rem;">所有 GenAI 客户已有 Bedrock 用量</p>'}

<div class="card" style="border-left:3px solid #D29922;margin-top:12px;">
  <h4>📝 调研说明</h4>
  <p style="font-size:0.82rem;color:#8B949E;">以上卡片基于收入数据自动生成。具体的产品战略、AI Agent 使用情况、竞品关系等调研内容需要在 Step 3 (联网调研) 阶段由 Agent 补充。每张卡片将被增强为完整的行业调研发现，包含: 公司 AI 战略、产品细节、{product} 组件匹配分析。</p>
</div>
</div>"""


def _render_tab4(data: dict) -> str:
    """Tab 4: GTM plays — load product components and map to categories/accounts."""
    components = data.get("product_components", [])
    categories = data.get("categories", {})
    product = data.get("product", "N/A")
    br_accts = data.get("bedrock_accounts", [])
    top_accts = data.get("top_accounts", [])

    if not components:
        return f"""<div class="tc tc4">
<div class="stitle">GTM 打法</div>
<p style="color:#8B949E;">无产品知识文件 ({product})，无法生成 GTM Play。请添加 knowledge/{product}.md。</p>
</div>"""

    # Component CSS class mapping
    comp_css = {
        "Runtime": "c-runtime", "Memory": "c-memory", "Gateway": "c-gateway",
        "Policy": "c-policy", "Identity": "c-identity", "Browser": "c-browser",
        "Code Interpreter": "c-code", "Observability": "c-obs",
        "Evaluations": "c-eval", "Registry": "c-reg",
    }

    # Map components to relevant categories based on use cases
    # This is a heuristic: components with IoT/device keywords map to IoT cats, etc.
    comp_cat_map = {
        "Runtime": ["自动驾驶/智驾", "清洁/服务机器人", "工业制造/重工"],
        "Memory": ["汽车整车", "清洁/服务机器人", "消费电子", "智能家居/IoT"],
        "Gateway": ["智能家居/IoT", "工业制造/重工", "安防/摄像头", "消费电子"],
        "Policy": ["汽车整车", "工业制造/重工", "自动驾驶/智驾"],
        "Identity": ["汽车整车", "消费电子"],
        "Browser": ["消费电子", "工业制造/重工"],
        "Code Interpreter": ["消费电子", "半导体"],
        "Observability": ["汽车整车", "工业制造/重工", "安防/摄像头"],
        "Evaluations": ["汽车整车", "清洁/服务机器人", "消费电子", "智能家居/IoT", "安防/摄像头"],
        "Registry": ["智能家居/IoT", "工业制造/重工", "消费电子"],
    }

    plays_html = ""
    play_num = 0
    for comp in components:
        comp_name = comp["name"]
        mapped_cats = comp_cat_map.get(comp_name, [])
        # Find categories that actually exist in our data
        active_cats = [c for c in mapped_cats if c in categories]
        if not active_cats and categories:
            # Fall back: map to all categories
            active_cats = list(categories.keys())[:3]

        # Find target accounts: top accounts in those categories with bedrock/genai
        target_accounts = []
        for a in (br_accts + top_accts):
            if a.get("category", "") in active_cats and a["name"] not in [t["name"] for t in target_accounts]:
                target_accounts.append(a)
            if len(target_accounts) >= 5:
                break

        if not target_accounts and top_accts:
            target_accounts = top_accts[:3]

        play_num += 1
        css_class = comp_css.get(comp_name, "c-runtime")
        cat_tags = " ".join(f'<span style="font-size:0.65rem;background:#21262D;color:#8B949E;padding:2px 6px;border-radius:3px;margin-right:3px;">{c}</span>' for c in active_cats)
        target_names = ", ".join(f"{a['name']}" for a in target_accounts[:4])
        target_detail = " / ".join(
            f"{a['name']}(Bedrock {_fmt_dollar(a.get('bedrock', 0))})" if a.get("bedrock", 0) > 0
            else f"{a['name']}(TTM {_fmt_dollar(a.get('ttm', 0))})"
            for a in target_accounts[:4]
        )

        plays_html += f'''<div class="play">
  <h4>Play {play_num}: <span class="tag-comp {css_class}">{comp_name}</span> {comp["desc"][:50]}</h4>
  <div style="margin-bottom:4px;">{cat_tags}</div>
  <div class="pain">组件: {comp["desc"]}</div>
  <div class="solve"><strong>场景:</strong> {comp["use_case"]}</div>
  <div class="target"><strong>首批目标:</strong> {target_detail or '待确定'}</div>
</div>
'''

    return f"""<div class="tc tc4">
<div class="stitle">{len(components)} 个 {product} 组件 GTM Play — 基于产品知识 + 客户数据</div>
{plays_html}
<div class="card" style="border-left:3px solid #D29922;margin-top:12px;">
  <h4>📝 说明</h4>
  <p style="font-size:0.82rem;color:#8B949E;">以上 Play 基于 {product} 产品组件和客户类别自动映射。Step 3 联网调研后，每个 Play 将增强: 具体痛点证据、解决方案细节、竞争对比、商业价值量化。</p>
</div>
</div>"""


def _render_tab5(data: dict) -> str:
    """Tab 5: Priority matrix — 2x2 based on TTM + Bedrock."""
    priority = data.get("priority", {})
    product = data.get("product", "N/A")

    quick_win = priority.get("quick_win", [])
    strategic = priority.get("strategic", [])
    seed = priority.get("seed", [])
    monitor = priority.get("monitor", [])

    def _items(accts: list[dict]) -> str:
        if not accts:
            return '<div class="item" style="color:#484F58;">暂无匹配客户</div>'
        return "\n".join(
            f'<div class="item"><strong>{a["name"]}</strong> — TTM {_fmt_dollar(a["ttm"])}, Bedrock {_fmt_dollar(a.get("bedrock", 0))}, {a.get("category", "")}</div>'
            for a in accts
        )

    # POC recommendations: top from quick_win
    poc_core = quick_win[:3] if quick_win else (strategic[:2] + seed[:1])
    poc_extend = (strategic[:2] if len(quick_win) >= 3 else strategic[:2])

    poc_core_html = ""
    for i, a in enumerate(poc_core, 1):
        poc_core_html += f'<p style="font-size:0.82rem;margin-bottom:6px;"><strong>{chr(9311+i)} {a["name"]}</strong> — {a.get("category", "")} / Bedrock {_fmt_dollar(a.get("bedrock", 0))} / TTM {_fmt_dollar(a["ttm"])}</p>'

    poc_extend_html = ""
    for i, a in enumerate(poc_extend, 1):
        poc_extend_html += f'<p style="font-size:0.82rem;margin-bottom:6px;"><strong>+{i} {a["name"]}</strong> — {a.get("category", "")} / TTM {_fmt_dollar(a["ttm"])}</p>'

    return f"""<div class="tc tc5">
<div class="stitle">优先级矩阵 — 基于 TTM 收入 + Bedrock 用量 (Top 20)</div>
<div class="matrix">
  <div class="q q1">
    <h4>✅ 快赢 Quick Win (高TTM + 有Bedrock)</h4>
    {_items(quick_win)}
  </div>
  <div class="q q2">
    <h4>🎯 战略押注 Strategic (高TTM + 无Bedrock)</h4>
    {_items(strategic)}
  </div>
  <div class="q q3">
    <h4>🌱 播种 Seed (低TTM + 有Bedrock)</h4>
    {_items(seed)}
  </div>
  <div class="q q4">
    <h4>👀 观察 Monitor (低TTM + 无Bedrock)</h4>
    {_items(monitor)}
  </div>
</div>

<div class="stitle">推荐 POC</div>
<div class="row2">
  <div class="card" style="border-left:3px solid #3FB950;">
    <h4>核心 POC</h4>
    {poc_core_html or '<p style="font-size:0.82rem;color:#8B949E;">数据不足，待分析</p>'}
  </div>
  <div class="card" style="border-left:3px solid #D29922;">
    <h4>延伸 POC</h4>
    {poc_extend_html or '<p style="font-size:0.82rem;color:#8B949E;">数据不足，待分析</p>'}
  </div>
</div>

<div class="card" style="border-left:3px solid #F85149;margin-top:10px;">
  <h4>分类逻辑</h4>
  <p style="font-size:0.8rem;color:#8B949E;">
    <strong>快赢</strong> = TTM >= 中位数 + 有 Bedrock 用量 (自然 upsell 到 {product})<br>
    <strong>战略</strong> = TTM >= 中位数 + 无 Bedrock (需要教育但价值大)<br>
    <strong>播种</strong> = TTM < 中位数 + 有 Bedrock (已有 AI 意识，可培育)<br>
    <strong>观察</strong> = TTM < 中位数 + 无 Bedrock (观望为主)
  </p>
</div>
</div>"""


def _render_tab6(data: dict) -> str:
    """Tab 6: Competitive comparison — product-specific table."""
    product = data.get("product", "N/A")
    components = data.get("product_components", [])

    if not components:
        return f"""<div class="tc tc6">
<div class="stitle">竞品对比</div>
<p style="color:#8B949E;">无产品知识文件，无法生成竞品对比。</p>
</div>"""

    # Build competitive table dynamically from components
    competitors = ["阿里百炼", "字节扣子", "华为盘古", "Azure AI", "OpenClaw"]

    # Default competitive position: AgentCore leads, others partial/none
    # This is product-knowledge driven — for a more accurate view,
    # the knowledge file should include competitive data
    comp_default = {
        "Runtime": {"阿里百炼": "p", "字节扣子": "n", "华为盘古": "p", "Azure AI": "y", "OpenClaw": "n"},
        "Memory": {"阿里百炼": "n", "字节扣子": "n", "华为盘古": "n", "Azure AI": "p", "OpenClaw": "n"},
        "Gateway": {"阿里百炼": "p", "字节扣子": "p", "华为盘古": "p", "Azure AI": "n", "OpenClaw": "n"},
        "Policy": {"阿里百炼": "n", "字节扣子": "n", "华为盘古": "n", "Azure AI": "p", "OpenClaw": "n"},
        "Identity": {"阿里百炼": "n", "字节扣子": "p", "华为盘古": "p", "Azure AI": "y", "OpenClaw": "n"},
        "Browser": {"阿里百炼": "n", "字节扣子": "n", "华为盘古": "n", "Azure AI": "n", "OpenClaw": "n"},
        "Code Interpreter": {"阿里百炼": "p", "字节扣子": "n", "华为盘古": "p", "Azure AI": "y", "OpenClaw": "y"},
        "Observability": {"阿里百炼": "p", "字节扣子": "n", "华为盘古": "p", "Azure AI": "y", "OpenClaw": "n"},
        "Evaluations": {"阿里百炼": "n", "字节扣子": "n", "华为盘古": "n", "Azure AI": "p", "OpenClaw": "n"},
        "Registry": {"阿里百炼": "n", "字节扣子": "n", "华为盘古": "n", "Azure AI": "n", "OpenClaw": "n"},
    }

    status_map = {
        "y": '<td class="y">✅</td>',
        "n": '<td class="n">❌</td>',
        "p": '<td class="p">有限</td>',
    }

    header = "<tr><th>组件</th><th>" + product + "</th>" + "".join(f"<th>{c}</th>" for c in competitors) + "</tr>"

    rows = ""
    for comp in components:
        name = comp["name"]
        desc_short = comp["desc"][:30]
        comp_data = comp_default.get(name, {})
        competitor_cells = "".join(status_map.get(comp_data.get(c, "n"), status_map["n"]) for c in competitors)
        rows += f'<tr><td>{name}<br><span style="font-size:0.65rem;color:#8B949E;">{desc_short}</span></td><td class="y">✅</td>{competitor_cells}</tr>\n'

    # Add global rows
    rows += f'<tr><td>全球化</td><td class="y">✅ 9 Region</td><td class="p">有限(CN)</td><td class="n">❌(CN)</td><td class="p">有限(CN)</td><td class="y">✅</td><td>N/A</td></tr>'
    rows += f'<tr><td>模型无关</td><td class="y">✅ 任意模型</td><td class="p">通义为主</td><td class="p">豆包为主</td><td class="p">盘古为主</td><td class="p">OpenAI为主</td><td class="y">✅ 任意</td></tr>'

    return f"""<div class="tc tc6">
<div class="stitle">{product} {len(components)} 组件级竞品对比</div>
<table class="tbl">
{header}
{rows}
</table>
<div class="card" style="border-left:3px solid #FF9900;">
  <h4>核心差异化</h4>
  <p style="font-size:0.85rem;">{product} 是<strong>唯一</strong>同时具备 Browser 云端沙箱 + Cedar 确定性 Policy + 企业 Identity + 三级 Evaluations + Agent Registry 的全托管平台。<br>
  国产平台没有安全隔离 → 企业 CISO 不签字。Azure 没有 Browser/Gateway/Registry → 能力不全。OpenClaw 什么管控都没有 → 只能个人用。</p>
</div>
</div>"""


def render_gm_detailed(data: dict) -> str:
    """
    Render GM-level detailed HTML report with 6 interactive tabs.

    Args:
        data: dict with keys: bu_name, product, total_accounts, total_ttm,
              total_genai, total_bedrock, auto_count, mfg_count,
              top_accounts, bedrock_accounts, categories, plays, scenarios, competitive,
              bedrock_penetration, genai_penetration, product_components, priority
    """
    bu = data.get("bu_name", "N/A")
    product = data.get("product", "N/A")
    total = data.get("total_accounts", 0)
    auto_count = data.get("auto_count", 0)
    mfg_count = data.get("mfg_count", 0)

    tab1 = _render_tab1(data)
    tab2 = _render_tab2(data)
    tab3 = _render_tab3(data)
    tab4 = _render_tab4(data)
    tab5 = _render_tab5(data)
    tab6 = _render_tab6(data)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{product} GTM 详细分析 — {bu}</title>
<style>
@page {{ margin: 1.5cm; }}
@media print {{ body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; background: #fff !important; color: #000 !important; }} }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #0D1117; color: #E6EDF3; line-height: 1.6; font-size: 14px; }}
body::before {{ content: '机密 — AWS 内部'; position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%) rotate(-35deg); font-size: 5rem; font-weight: 800; color: rgba(255,153,0,0.03); pointer-events: none; z-index: 9999; white-space: nowrap; }}
.wrap {{ max-width: 1140px; margin: 0 auto; padding: 24px 32px 40px; }}

/* Header */
.hdr {{ display: flex; justify-content: space-between; border-bottom: 3px solid #FF9900; padding-bottom: 14px; margin-bottom: 20px; }}
.hdr h1 {{ font-size: 1.6rem; color: #FF9900; }}
.hdr .sub {{ color: #8B949E; font-size: 0.82rem; margin-top: 3px; }}
.hdr-r {{ text-align: right; font-size: 0.75rem; color: #8B949E; }}
.hdr-r .conf {{ color: #FF9900; font-weight: 700; font-size: 0.7rem; letter-spacing: 0.08em; }}

/* Tabs */
.tabs {{ margin-bottom: 20px; }}
.tabs input[type=radio] {{ display: none; }}
.tab-labels {{ display: flex; gap: 4px; margin-bottom: 0; border-bottom: 2px solid #30363D; }}
.tab-labels label {{ padding: 8px 16px; font-size: 0.82rem; font-weight: 600; color: #8B949E; cursor: pointer; border-radius: 6px 6px 0 0; transition: all 0.2s; }}
.tab-labels label:hover {{ color: #E6EDF3; background: #161B22; }}
.tc {{ display: none; padding-top: 18px; }}
#t1:checked ~ .tab-labels label[for=t1],
#t2:checked ~ .tab-labels label[for=t2],
#t3:checked ~ .tab-labels label[for=t3],
#t4:checked ~ .tab-labels label[for=t4],
#t5:checked ~ .tab-labels label[for=t5],
#t6:checked ~ .tab-labels label[for=t6] {{ color: #FF9900; background: #161B22; border-bottom: 2px solid #FF9900; margin-bottom: -2px; }}
#t1:checked ~ .tc1, #t2:checked ~ .tc2, #t3:checked ~ .tc3,
#t4:checked ~ .tc4, #t5:checked ~ .tc5, #t6:checked ~ .tc6 {{ display: block; }}

/* Cards & Stats */
.row4 {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 16px; }}
.row3 {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin-bottom: 16px; }}
.row2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }}
.card {{ background: #161B22; border: 1px solid #30363D; border-radius: 10px; padding: 14px 16px; }}
.card h4 {{ color: #FF9900; font-size: 0.9rem; margin-bottom: 8px; }}
.stat {{ text-align: center; }}
.stat .n {{ font-size: 1.6rem; font-weight: 800; color: #FF9900; }}
.stat .n.red {{ color: #F85149; }}
.stat .n.grn {{ color: #3FB950; }}
.stat .l {{ font-size: 0.7rem; color: #8B949E; margin-top: 2px; }}
.stitle {{ font-size: 1.05rem; color: #FF9900; font-weight: 700; border-left: 4px solid #FF9900; padding-left: 10px; margin: 20px 0 12px; }}

/* Tags */
.tag-comp {{ display: inline-block; font-size: 0.62rem; font-weight: 600; padding: 1px 6px; border-radius: 3px; margin: 1px 2px; }}
.c-runtime {{ background: #1F3D5C; color: #58A6FF; }}
.c-memory {{ background: #2D1B4E; color: #BC8CFF; }}
.c-gateway {{ background: #1B3D2F; color: #3FB950; }}
.c-policy {{ background: #4A1B1B; color: #F85149; }}
.c-identity {{ background: #4A3D1B; color: #D29922; }}
.c-browser {{ background: #1B3D4A; color: #39D2C0; }}
.c-code {{ background: #4A2D1B; color: #FF9900; }}
.c-obs {{ background: #1B4A4A; color: #56D4DD; }}
.c-eval {{ background: #4A1B3D; color: #F778BA; }}
.c-reg {{ background: #2D2D2D; color: #8B949E; }}

/* Intel items */
.intel {{ background: #161B22; border: 1px solid #30363D; border-radius: 8px; padding: 12px 14px; margin-bottom: 8px; font-size: 0.82rem; }}
.intel .co {{ font-weight: 700; color: #E6EDF3; margin-bottom: 3px; }}
.intel .fact {{ color: #8B949E; margin-bottom: 3px; }}
.intel .imp {{ color: #FF9900; font-size: 0.78rem; }}

/* Play cards */
.play {{ background: #161B22; border: 1px solid #30363D; border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; }}
.play h4 {{ color: #FF9900; font-size: 0.95rem; margin-bottom: 6px; }}
.play .pain {{ color: #F85149; font-size: 0.8rem; margin-bottom: 4px; }}
.play .solve {{ font-size: 0.8rem; margin-bottom: 4px; }}
.play .target {{ font-size: 0.8rem; }}

/* 2x2 Matrix */
.matrix {{ display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; gap: 10px; margin-bottom: 16px; }}
.matrix .q {{ border-radius: 10px; padding: 14px; }}
.q1 {{ background: #0B2E0B; border: 1px solid #238636; }}
.q2 {{ background: #2D1B00; border: 1px solid #9E6A03; }}
.q3 {{ background: #0B1E3D; border: 1px solid #1F6FEB; }}
.q4 {{ background: #1B1B1B; border: 1px solid #484F58; }}
.q h4 {{ font-size: 0.85rem; margin-bottom: 6px; }}
.q1 h4 {{ color: #3FB950; }} .q2 h4 {{ color: #D29922; }} .q3 h4 {{ color: #58A6FF; }} .q4 h4 {{ color: #8B949E; }}
.q .item {{ font-size: 0.78rem; margin-bottom: 4px; padding-left: 8px; border-left: 2px solid #30363D; }}

/* Table */
.tbl {{ width: 100%; border-collapse: collapse; font-size: 0.78rem; margin-bottom: 14px; }}
.tbl th {{ background: #1F4E79; color: #fff; padding: 6px 8px; text-align: center; font-size: 0.72rem; }}
.tbl td {{ padding: 5px 8px; border: 1px solid #30363D; text-align: center; }}
.tbl .y {{ color: #3FB950; font-weight: 700; }}
.tbl .n {{ color: #F85149; }}
.tbl .p {{ color: #D29922; }}
.tbl tr:nth-child(even) td {{ background: #161B22; }}
.tbl td:first-child {{ text-align: left; font-weight: 600; }}

.footer {{ margin-top: 20px; padding-top: 10px; border-top: 1px solid #30363D; font-size: 0.65rem; color: #484F58; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">

<div class="hdr">
  <div>
    <h1>{product} GTM 详细分析</h1>
    <div class="sub">{bu} 行业 · {total} 家 L+ 客户 (AUTO {auto_count} + MFG {mfg_count})</div>
  </div>
  <div class="hdr-r">
    <div class="conf">机密 — AWS 内部</div>
    数据来源: DataProxy Athena + Sentral MCP
  </div>
</div>

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
{tab1}

<!-- Tab 2: 客户分析 -->
{tab2}

<!-- Tab 3: 行业调研 -->
{tab3}

<!-- Tab 4: GTM 打法 -->
{tab4}

<!-- Tab 5: 优先级 -->
{tab5}

<!-- Tab 6: 竞品对比 -->
{tab6}

</div><!-- tabs -->

<div class="footer">
{product} GTM 详细分析 · {bu} · {total} 家 L+ 客户 · 机密 — AWS 内部<br>
数据来源: DataProxy Athena + Sentral MCP · 所有 6 Tab 数据驱动自动生成
</div>
</div>
</body>
</html>"""
