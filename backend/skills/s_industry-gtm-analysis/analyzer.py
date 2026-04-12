#!/usr/bin/env python3
"""
Industry GTM Analysis — CLI entry point.
Orchestrates: load accounts -> query revenue -> merge -> generate reports.

Usage:
    python analyzer.py --bu "AUTO & MFG" --product agentcore --accounts /tmp/accounts.json --output /tmp/gtm/
"""
import argparse
import json
import os
import re
import sys

# Add skill dir to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

from data import query_revenue, merge_accounts_with_revenue
from templates.rob_summary import render_rob_summary
from templates.gm_detailed import render_gm_detailed
from templates.excel_builder import build_excel

AUTO_CATS = {'汽车整车', '自动驾驶/智驾', '汽车零部件', '出行/两轮'}


def parse_args():
    p = argparse.ArgumentParser(description="Industry GTM Analysis Report Generator")
    p.add_argument("--bu", required=True, help="sh_l3 BU name (e.g., 'AUTO & MFG')")
    p.add_argument("--product", required=True, help="AWS product (e.g., agentcore, bedrock)")
    p.add_argument("--tshirt", default="L", choices=["L", "XL", "XXL"], help="Min T-shirt size")
    p.add_argument("--accounts", required=True, help="Path to Sentral accounts JSON")
    p.add_argument("--template", default="all", choices=["rob", "gm", "excel", "all"])
    p.add_argument("--output", default="/tmp/gtm-output/", help="Output directory")
    return p.parse_args()


def _load_product_knowledge(product: str) -> list[dict]:
    """Load product components from knowledge/{product}.md and parse the component table."""
    knowledge_path = os.path.join(SKILL_DIR, "knowledge", f"{product}.md")
    if not os.path.exists(knowledge_path):
        return []

    with open(knowledge_path) as f:
        content = f.read()

    components = []
    # Parse markdown table: | # | Component | Description | Key Use Case |
    in_table = False
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("| #"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")]
            # parts[0] is empty (before first |), parts[-1] is empty (after last |)
            parts = [p for p in parts if p]
            if len(parts) >= 4:
                components.append({
                    "num": parts[0],
                    "name": parts[1],
                    "desc": parts[2],
                    "use_case": parts[3],
                })
        elif in_table and not line.startswith("|"):
            in_table = False

    return components


def _compute_categories(accounts: list[dict]) -> dict:
    """
    Group accounts by category field. For each category, compute:
    - count, ttm, genai, bedrock sums
    - industry (AUTO or MFG)
    - xxl and xl account names
    """
    cats = {}
    for a in accounts:
        cat = a.get("category", "其他") or "其他"
        if cat not in cats:
            cats[cat] = {
                "count": 0, "ttm": 0.0, "genai": 0.0, "bedrock": 0.0,
                "industry": "AUTO" if cat in AUTO_CATS else "MFG",
                "xxl": [], "xl": [], "accounts": [],
            }
        c = cats[cat]
        c["count"] += 1
        c["ttm"] += float(a.get("ttm", 0))
        c["genai"] += float(a.get("genai", 0))
        c["bedrock"] += float(a.get("bedrock", 0))
        short = a.get("short", a.get("name", ""))
        c["accounts"].append(short)
        size = a.get("size", "")
        if size == "XXL":
            c["xxl"].append(short)
        elif size == "XL":
            c["xl"].append(short)

    return cats


def _load_competitive_data(product: str) -> list[dict]:
    """Load competitive comparison data for a product.

    Returns list of dicts with keys: dimension, values (list of 6 competitor values).
    Competitors: [product, 阿里百炼, 字节扣子, 华为盘古, Azure AI, OpenClaw]
    """
    if product.lower() == "agentcore":
        competitors = ["AgentCore", "阿里百炼", "字节扣子", "华为盘古", "Azure AI", "OpenClaw"]
        rows = [
            {"dimension": "Runtime(MicroVM)", "values": ["✅ 8hr async按秒计费", "有限", "❌", "有限", "✅", "❌本地"]},
            {"dimension": "Memory(跨会话)", "values": ["✅ 短期+长期+语义+共享", "❌", "❌", "❌", "有限", "❌"]},
            {"dimension": "Gateway(API→MCP)", "values": ["✅ 语义发现千级", "有限", "插件市场", "有限", "❌", "❌"]},
            {"dimension": "Policy(Cedar)", "values": ["✅ NL→Cedar不可绕过", "❌", "❌", "❌", "有限", "❌"]},
            {"dimension": "Identity(企业IdP)", "values": ["✅ Cognito/Okta/Entra", "❌", "有限", "华为账号", "✅Entra", "❌"]},
            {"dimension": "Browser(沙箱)", "values": ["✅ Playwright+录屏+接管", "❌", "❌", "❌", "❌", "❌本地"]},
            {"dimension": "Code Interpreter", "values": ["✅ Py/JS/TS+VPC", "有限", "❌", "有限", "✅", "✅本地"]},
            {"dimension": "Observability", "values": ["✅ OTEL+第三方", "有限", "❌", "有限", "✅", "❌"]},
            {"dimension": "Evaluations(三级)", "values": ["✅ Session/Trace/Tool", "❌", "❌", "❌", "有限", "❌"]},
            {"dimension": "Registry(目录)", "values": ["✅ 语义搜索+审批+MCP", "❌", "❌", "❌", "❌", "❌"]},
            {"dimension": "全球化", "values": ["✅ 9 Region", "有限CN", "❌CN", "有限CN", "✅", "N/A"]},
            {"dimension": "模型无关", "values": ["✅ 任意", "通义", "豆包", "盘古", "OpenAI", "✅任意"]},
        ]
        return {"competitors": competitors, "rows": rows}
    # Other products: return empty
    return {"competitors": [], "rows": []}


def _generate_scenario_rows(categories: dict, product_components: list[dict],
                            product: str) -> list[dict]:
    """Generate expanded scenario rows for the AI场景 sheet.

    Each category gets multiple rows: 对外 (external) + 内部 (internal) scenarios.
    Returns list of dicts with keys matching the 13-column layout.
    """
    # Product component name lookup for quick reference
    comp_names = [c["name"] for c in product_components] if product_components else []

    # Scenario templates per category — data-driven from product knowledge
    # These map category patterns to specific scenario data
    SCENARIO_MAP = {
        "汽车整车": [
            {"type": "对外", "name": "车载AI语音助手/座舱Agent",
             "components": "Memory+Identity+Evaluations",
             "reasoning": "理想Mind GPT已有50万车+1.2亿日交互+300工具；BYD全系集成DeepSeek璇玑架构；蔚来NOMI在Azure(竞争置换)",
             "verdict": "🏆Lighthouse: 理想/蔚来已有成熟Agent产品，Memory+Eval直接增强"},
            {"type": "对外", "name": "自动驾驶OTA质量验证",
             "components": "Evaluations+Observability",
             "reasoning": "每次OTA推送Agent行为变化需验证——三级Eval(Session/Trace/Tool_call)确保不退化",
             "verdict": "📈Scale: 所有车企OTA都需要，标准化方案"},
            {"type": "对外", "name": "车载情感/个性化记忆",
             "components": "Memory+Identity",
             "reasoning": "蔚来NOMI情感引擎、理想Mind GPT 98.7%准确率——需要跨行程的长期记忆",
             "verdict": "⏳花时间: 深度技术集成但品牌价值最高"},
            {"type": "内部", "name": "全球销售Agent",
             "components": "Browser+Gateway+Memory",
             "reasoning": "BYD 40国销售团队——多国CRM统一+自动跟进+报价生成",
             "verdict": ""},
            {"type": "内部", "name": "售后客服Agent",
             "components": "Memory+Gateway+Evaluations",
             "reasoning": "蔚来NIO House体验——客服记住客户车辆+历史问题",
             "verdict": ""},
        ],
        "自动驾驶/智驾": [
            {"type": "对外", "name": "自动驾驶仿真环境",
             "components": "Runtime+Code Interpreter",
             "reasoning": "Momenta飞轮数据闭环每天百万场景仿真；文远L4 Robotaxi多城运营",
             "verdict": "🏆Lighthouse: Momenta/文远训练量巨大，Runtime 8hr async直接匹配"},
            {"type": "对外", "name": "高精地图Agent更新",
             "components": "Memory+Gateway+Code Interpreter",
             "reasoning": "四维图新HD地图自动更新——Agent持续监测道路变化",
             "verdict": "🍎Low-hanging: Gateway连接传感器数据源+Memory积累变化"},
            {"type": "内部", "name": "AD数据标注/训练Agent",
             "components": "Code Interpreter+Runtime",
             "reasoning": "大规模点云/视频标注自动化——8hr async任务",
             "verdict": ""},
        ],
        "汽车零部件": [
            {"type": "对外", "name": "零部件预测维护Agent",
             "components": "Memory+Gateway+Code Interpreter",
             "reasoning": "Garrett涡轮增压器/安波福SVA/大陆集团HPC——传感器数据→预测故障",
             "verdict": "📈Scale: 所有Tier 1都有相同需求"},
            {"type": "内部", "name": "供应商质量Agent",
             "components": "Browser+Code Interpreter+Policy",
             "reasoning": "自动扫描供应商质量报告→异常检测→告警",
             "verdict": ""},
        ],
        "消费电子": [
            {"type": "对外", "name": "3D打印AI监控订阅",
             "components": "Browser+Code Interpreter+Evaluations",
             "reasoning": "Bambu Lab 1000万月活+260万模型83%留存——AI打印监控/故障检测/自适应控制",
             "verdict": "🍎Low-hanging: 云连接设备海量用户，Browser远程查看+Eval质量监控，天然SaaS"},
            {"type": "对外", "name": "运动相机/无人机AI编辑",
             "components": "Code Interpreter+Memory",
             "reasoning": "Insta360 AI自动剪辑+AI追踪CES2025; DJI AI避障+自主航线; 道通科技AI诊断",
             "verdict": "📈Scale: 消费影像设备都需要云端AI处理"},
            {"type": "对外", "name": "智能穿戴健康Agent",
             "components": "Memory+Evaluations",
             "reasoning": "ZEPP/华米 AI健康顾问/运动教练——需要跨会话记忆(训练周期/健康趋势)",
             "verdict": "🍎Low-hanging: Memory天然匹配健康数据积累"},
            {"type": "内部", "name": "全球电商运营Agent",
             "components": "Browser+Code Interpreter+Gateway",
             "reasoning": "Anker/TP-LINK全球电商——自动分析销售数据/竞品监控/定价优化",
             "verdict": ""},
        ],
        "大家电/白电": [
            {"type": "对外", "name": "全屋AI管家",
             "components": "Gateway+Memory+Policy+Registry",
             "reasoning": "海尔三翼鸟\"AI for Home\"超越App/语音的主动场景自动化；美的COLMO发布业界首个AI管家Agent",
             "verdict": "🏆Lighthouse: 三翼鸟×AgentCore联合品牌，Gateway编排全屋几百种设备"},
            {"type": "对外", "name": "家电AI节能/智能控制",
             "components": "Memory+Code Interpreter",
             "reasoning": "美的AI节能运行、海尔预测维护、格力AI语音管家——记住用户习惯+优化运行",
             "verdict": "📈Scale: 所有白电品牌相同需求"},
            {"type": "内部", "name": "全球工厂Agent",
             "components": "Gateway+Policy+Observability",
             "reasoning": "美的Agent Factory(质检15min→30s)全球复制; 海尔COSMOPlat 57个工业Agent/$79亿估值",
             "verdict": ""},
        ],
        "智能家居/IoT": [
            {"type": "对外", "name": "IoT Agent平台卖OEM",
             "components": "Gateway+Registry+Runtime+Policy",
             "reasoning": "Tuya 2025.2推出AI Agent开发平台集成DeepSeek/GPT/Gemini/Qwen——给几千OEM提供Agent能力",
             "verdict": "🏆Lighthouse: Tuya核心商业模式=PaaS，Gateway+Registry天然匹配"},
            {"type": "对外", "name": "智能家居场景自动化",
             "components": "Gateway+Memory+Policy",
             "reasoning": "SwitchBot/绿米Aqara/Meross——从手动规则→AI学习用户习惯自动执行",
             "verdict": "🍎Low-hanging: Memory学习习惯+Gateway编排设备，几周POC"},
            {"type": "内部", "name": "设备运营分析Agent",
             "components": "Code Interpreter+Memory",
             "reasoning": "IoT平台分析千万设备运行数据→发现异常模式",
             "verdict": ""},
        ],
        "安防/摄像头": [
            {"type": "对外", "name": "AI看家Agent(C端)",
             "components": "Evaluations+Memory",
             "reasoning": "萤石/Reolink人形识别+追踪+AI回看摘要——Eval优化误报率是核心KPI",
             "verdict": "🍎Low-hanging: Eval直接提升产品质量，C端迭代周期短(2-4周)"},
            {"type": "对外", "name": "AI安防开放平台",
             "components": "Gateway+Registry+Policy+Evaluations",
             "reasoning": "海康2025.3与DeepSeek合作观澜大模型三层架构；大华发布AI Agent Software 1.0",
             "verdict": "🏆Lighthouse: 海康/大华已有Agent平台，Gateway+Registry直接增强生态"},
            {"type": "对外", "name": "安防AI增值SaaS",
             "components": "Runtime+Code Interpreter+Memory",
             "reasoning": "从卖摄像头→卖AI分析服务(零售客流/车流热力图)——SaaS月费模式",
             "verdict": "⏳花时间: 海康体量大决策链长，从萤石C端切入更快"},
            {"type": "内部", "name": "视频巡检Agent",
             "components": "Browser+Code Interpreter",
             "reasoning": "自动巡检摄像头画面质量/覆盖盲区/设备健康",
             "verdict": ""},
        ],
        "工业制造/重工": [
            {"type": "对外", "name": "产线AI质检Agent",
             "components": "Code Interpreter+Gateway+Evaluations",
             "reasoning": "美的Agent Factory WRCA认证全球首个智能Agent工厂(质检15min→30s)——多Agent分布式决策",
             "verdict": "🏆Lighthouse: 案例已有且全球首个认证，包装即标杆"},
            {"type": "对外", "name": "设备预测维护Agent",
             "components": "Memory+Gateway+Code Interpreter",
             "reasoning": "三一树根互联已有工业AI平台；ABB Ability平台; 施耐德EcoStruxure——传感器数据→预测故障→自动派工",
             "verdict": "🏆Lighthouse: 三一树根是国内工业IoT标杆，Gateway连接设备API"},
            {"type": "对外", "name": "工业Agent编排平台",
             "components": "Registry+Gateway+Policy",
             "reasoning": "海尔COSMOPlat 57个工业Agent覆盖40+场景——需要Agent目录管理/审批/版本控制",
             "verdict": "📈Scale: 所有智能工厂都需要Agent治理"},
            {"type": "内部", "name": "工厂财务/审计Agent",
             "components": "Browser+Code Interpreter+Policy",
             "reasoning": "全球工厂月结自动化——Policy确保合规不可绕过",
             "verdict": ""},
            {"type": "内部", "name": "供应链预测Agent",
             "components": "Gateway+Code Interpreter+Memory",
             "reasoning": "美的全球供应链——多系统(ERP+WMS+TMS)编排+需求预测",
             "verdict": ""},
        ],
        "清洁/服务机器人": [
            {"type": "对外", "name": "\"不说就动\"主动Agent",
             "components": "Memory+Gateway+Policy+Evaluations",
             "reasoning": "科沃斯AGENT YIKO(IFA2025)：业界首个LLM深度集成自主家庭管家——学习习惯主动清洁无需指令",
             "verdict": "🏆Lighthouse: YIKO是真正Agentic AI，需Memory(习惯学习)+Eval(主动行为准确率)"},
            {"type": "对外", "name": "清洁机器人AI订阅",
             "components": "Memory+Evaluations+Runtime",
             "reasoning": "石头Z70首款机械臂扫地机CES2025创新奖——高级清洁策略按月付费",
             "verdict": "🍎Low-hanging: YIKO/Z70已有Agent能力，AgentCore补Memory+Eval=订阅模式"},
            {"type": "对外", "name": "物流/服务机器人编排",
             "components": "Runtime+Memory+Policy",
             "reasoning": "炬星科技AMR多机器人协同——仓储AI调度",
             "verdict": "📈Scale: 仓储机器人市场标准化"},
        ],
        "半导体/芯片": [
            {"type": "对外", "name": "端侧AI Agent框架",
             "components": "Code Interpreter+Runtime",
             "reasoning": "乐鑫ESP32 ESP-Agent框架; 高通骁龙AI平台; 嘉楠勘智AI芯片——端侧Agent需要云端训练",
             "verdict": "📈Scale: 端云协同是所有AI芯片厂的架构"},
            {"type": "内部", "name": "芯片EDA/验证Agent",
             "components": "Code Interpreter+Runtime",
             "reasoning": "AI辅助芯片设计验证——长时间仿真任务",
             "verdict": ""},
        ],
        "新能源/电池": [
            {"type": "对外", "name": "能源AI优化Agent",
             "components": "Runtime+Memory+Code Interpreter",
             "reasoning": "EcoFlow OASIS(CES2025)：AI预测引擎分析实时用电/太阳能/电价/天气→自动调节充放电→节能77.6%",
             "verdict": "🍎Low-hanging: OASIS已有AI但缺云端Runtime(8hr连续优化)+Memory(历史用电模式)"},
            {"type": "对外", "name": "电池AI管理",
             "components": "Memory+Code Interpreter+Evaluations",
             "reasoning": "CATL智能工厂质检+电池寿命预测——Memory积累电池全生命周期数据",
             "verdict": "📈Scale: 所有电池/储能企业相同需求"},
        ],
        "出行/两轮": [
            {"type": "对外", "name": "出行平台调度Agent",
             "components": "Runtime+Gateway+Memory",
             "reasoning": "出行平台实时调度——多Agent协同分配订单+动态定价",
             "verdict": "📈Scale: 出行平台共性需求"},
            {"type": "内部", "name": "车队运营Agent",
             "components": "Code Interpreter+Memory",
             "reasoning": "车队管理自动化——维保预测+调度优化",
             "verdict": ""},
        ],
        "矿机/区块链": [
            {"type": "对外", "name": "算力调度Agent",
             "components": "Runtime+Code Interpreter",
             "reasoning": "ANTPOOL/BitFuFu云算力——AI优化挖矿效率和算力分配",
             "verdict": "📈Scale: 算力调度标准化"},
        ],
    }

    scenario_rows = []
    sorted_cats = sorted(
        categories.items(),
        key=lambda x: (0 if x[1].get('industry') == 'AUTO' else 1, -x[1].get('ttm', 0))
    )

    for cat_name, cat_data in sorted_cats:
        industry = cat_data.get('industry', 'MFG')
        scenarios = SCENARIO_MAP.get(cat_name, [])

        if not scenarios:
            # Generate a generic scenario for unmapped categories
            scenarios = [
                {"type": "对外", "name": f"{cat_name}AI Agent",
                 "components": "Gateway+Runtime",
                 "reasoning": f"{cat_name}行业Agent自动化机会",
                 "verdict": "📈Scale: 行业通用需求"},
                {"type": "内部", "name": "运营效率Agent",
                 "components": "Browser+Code Interpreter",
                 "reasoning": "内部运营自动化——报表/分析/监控",
                 "verdict": ""},
            ]

        # Inject actual account names from data into reasoning where helpful
        xxl_names = ', '.join(cat_data.get('xxl', [])) or '—'
        xl_names = ', '.join(cat_data.get('xl', [])) or '—'

        for i, s in enumerate(scenarios):
            row = {
                "industry": industry,
                "category": cat_name,
                "count": cat_data.get('count', 0),
                "ttm": cat_data.get('ttm', 0),
                "genai": cat_data.get('genai', 0),
                "bedrock": cat_data.get('bedrock', 0),
                "xxl": xxl_names,
                "xl": xl_names,
                "scene_type": s["type"],
                "scene_name": s["name"],
                "components": s["components"],
                "reasoning": s["reasoning"],
                "verdict": s.get("verdict", ""),
                "is_first_in_category": (i == 0),
                "category_row_count": len(scenarios),
            }
            scenario_rows.append(row)

    return scenario_rows


def _generate_gtm_plays(categories: dict, product_components: list[dict],
                        priority: dict, product: str) -> dict:
    """Generate GTM plays data for the GTM Plays sheet.

    Returns dict with 4 sections: plays, external_scenarios, internal_scenarios, poc_recommendations.
    """
    # Get top account names for customer references
    qw = priority.get("quick_win", [])
    strat = priority.get("strategic", [])
    qw_names = [a["name"] for a in qw[:5]]
    strat_names = [a["name"] for a in strat[:5]]

    if product.lower() == "agentcore":
        plays = [
            {"name": "🦞 OpenClaw\non AgentCore",
             "components": "Browser+Identity\n+Policy+Obs+Registry",
             "pain": "员工已在用龙虾\nIT没法管",
             "why": "唯一云端Browser沙箱\n+企业IdP+Cedar拦截\n+审计链",
             "external": "—（水平能力）",
             "internal": "研效Agent平台\n办公自动化",
             "customers": "AUTO:蔚来/理想\nMFG:西门子/施耐德/海康",
             "type_diff": "🍎→📈 ⭐"},
            {"name": "🏭 Gateway\n多系统集成",
             "components": "Gateway+Registry\n+Policy",
             "pain": "工厂5-6套系统\nIoT千种设备API不统一",
             "why": "API→MCP一键转换\n语义发现千级工具\n自建6月→1天",
             "external": "IoT编排(Tuya)\n工厂Agent(美的/三一)\n全屋智能(海尔)",
             "internal": "供应链Agent\n销售CRM集成",
             "customers": "MFG:Tuya(XXL)\n美的/三一/海尔/中控",
             "type_diff": "🏆+📈 ⭐⭐"},
            {"name": "🚗 Memory\n跨会话记忆",
             "components": "Memory+Identity\n+Evaluations",
             "pain": "车不记人\n家电不记习惯\n客服不记历史",
             "why": "层级模型天然匹配\n多用户+跨Agent共享\n+语义检索",
             "external": "车载AI(NIO/理想)\n清洁机器人(科沃斯)\n预测维护(三一)",
             "internal": "客服记住历史\n销售记住客户",
             "customers": "AUTO:蔚来/理想/BYD\nMFG:科沃斯/石头/海尔",
             "type_diff": "🏆 ⭐⭐"},
            {"name": "🔍 Evaluations\n质量门禁",
             "components": "Evaluations\n+Observability",
             "pain": "Agent怎么知道好不好?\nOTA后退化谁负责?",
             "why": "三级评估+ground truth\n+在线持续评估\n没有eval=盲飞",
             "external": "OTA验证(所有车企)\nYIKO准确率(科沃斯)\n误报率(萤石)\n打印检测(Bambu)",
             "internal": "客服质量监控\n审计完整性评估",
             "customers": "AUTO:理想/所有车企\nMFG:科沃斯/萤石/Tuya/Bambu",
             "type_diff": "🍎→📈 ⭐"},
            {"name": "🖥️ Browser\n+Code Interp",
             "components": "Browser+Code Interp\n+Identity+Policy",
             "pain": "切5个Web系统\n手工汇总报表\n翻百个网页",
             "why": "云端沙箱+Python/JS\n+人工接管+录屏",
             "external": "3D打印远程(Bambu)\n安防云VMS(TP-Link)",
             "internal": "财务Agent\n审计Agent\n销售报告Agent",
             "customers": "MFG:美的(财务)/Bambu\nAUTO:BYD(销售)",
             "type_diff": "🍎 ⭐"},
            {"name": "📦 Registry\n平台运营",
             "components": "Registry+Gateway\n+Identity+Policy",
             "pain": "Agent/工具爆炸增长\n无目录/审批/版本",
             "why": "语义+关键词搜索\n审批流程\nMCP原生",
             "external": "IoT工具目录(Tuya)\n工业Agent管理(COSMOPlat)",
             "internal": "企业Agent目录\nMCP工具白名单",
             "customers": "MFG:Tuya(XXL)\n海尔/美的/乐鑫",
             "type_diff": "🏆 ⭐⭐"},
        ]

        external_scenarios = [
            {"industry": "AUTO", "value": "📈", "name": "车载AI记忆升级",
             "components": "Memory+Identity", "customers": "蔚来NIO(XL)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐", "play": "Play3"},
            {"industry": "AUTO", "value": "📈", "name": "车载记忆复制",
             "components": "Memory+Identity+Eval", "customers": "理想/小鹏/极氪/BYD",
             "type": "📈Scale", "difficulty": "⭐⭐", "play": "Play3"},
            {"industry": "AUTO", "value": "🚀", "name": "车载情感Agent",
             "components": "Memory+Eval", "customers": "蔚来NOMI(XL)",
             "type": "⏳花时间", "difficulty": "⭐⭐⭐", "play": "Play3+4"},
            {"industry": "AUTO", "value": "🔍", "name": "OTA Agent质量验证",
             "components": "Eval+Obs", "customers": "所有新势力",
             "type": "📈Scale", "difficulty": "⭐", "play": "Play4"},
            {"industry": "AUTO", "value": "💰", "name": "自动驾驶仿真",
             "components": "Runtime+Code Interp", "customers": "Momenta/文远/小马",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐⭐", "play": "Play5"},
            {"industry": "MFG", "value": "📈", "name": "IoT Agent平台",
             "components": "Gateway+Registry+Runtime", "customers": "Tuya(XXL)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐", "play": "Play2+6"},
            {"industry": "MFG", "value": "🚀", "name": "\"不说就动\"Agent",
             "components": "Memory+Gateway+Policy", "customers": "科沃斯YIKO(L)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐", "play": "Play3+4"},
            {"industry": "MFG", "value": "📈", "name": "清洁机器人订阅",
             "components": "Memory+Eval+Runtime", "customers": "科沃斯/石头(L)",
             "type": "🍎Low-hang", "difficulty": "⭐⭐", "play": "Play3+4"},
            {"industry": "MFG", "value": "🚀", "name": "全屋AI管家",
             "components": "Gateway+Memory+Policy", "customers": "海尔/美的COLMO(XL)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐⭐", "play": "Play2+3"},
            {"industry": "MFG", "value": "📈", "name": "安防C端AI看家",
             "components": "Eval+Memory", "customers": "萤石/Reolink(L-XL)",
             "type": "🍎Low-hang", "difficulty": "⭐", "play": "Play3+4"},
            {"industry": "MFG", "value": "📈", "name": "安防AI增值SaaS",
             "components": "Runtime+Code Interp", "customers": "海康(XL)",
             "type": "⏳花时间", "difficulty": "⭐⭐⭐", "play": "Play4+5"},
            {"industry": "MFG", "value": "📈", "name": "3D打印AI订阅",
             "components": "Browser+Code Interp+Eval", "customers": "Bambu Lab(XL)",
             "type": "🍎Low-hang", "difficulty": "⭐", "play": "Play4+5"},
            {"industry": "MFG", "value": "📈", "name": "智能穿戴健康AI",
             "components": "Memory+Eval", "customers": "ZEPP/华米(XL)",
             "type": "🍎Low-hang", "difficulty": "⭐⭐", "play": "Play3+4"},
            {"industry": "MFG", "value": "💰", "name": "产线AI质检",
             "components": "Code Interp+Gateway+Eval", "customers": "美的(XL)",
             "type": "🏆Lighthouse", "difficulty": "⭐", "play": "Play2+4+5"},
            {"industry": "MFG", "value": "💰", "name": "质检方案复制",
             "components": "同上", "customers": "CATL/海天/鹰普(L)",
             "type": "📈Scale", "difficulty": "⭐", "play": "Play2+4+5"},
            {"industry": "MFG", "value": "💰", "name": "设备预测维护",
             "components": "Memory+Gateway+Code Interp", "customers": "三一树根(XL)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐", "play": "Play2+3"},
            {"industry": "MFG", "value": "💰", "name": "能源AI优化",
             "components": "Runtime+Memory", "customers": "EcoFlow(XL)",
             "type": "🍎Low-hang", "difficulty": "⭐", "play": "Play3+5"},
            {"industry": "MFG", "value": "🚀", "name": "全球首个XX Agent",
             "components": "全套", "customers": "美的/DJI/三一(XL)",
             "type": "🏆Lighthouse", "difficulty": "⭐⭐", "play": "All"},
            {"industry": "MFG", "value": "🚀", "name": "无人机Agent编队",
             "components": "Runtime+Memory+Policy", "customers": "DJI农业(XL)",
             "type": "⏳花时间", "difficulty": "⭐⭐⭐", "play": "Play3+4"},
        ]

        internal_scenarios = [
            {"name": "研效Agent平台",
             "agent_does": "代码→测试→部署\n龙虾管控+工具目录",
             "vs_traditional": "RPA不写代码\n龙虾无管控",
             "components": "Browser+Code Interp\n+Identity+Registry",
             "auto_lighthouse": "蔚来/理想",
             "mfg_lighthouse": "华为/中兴",
             "why_cloud": "万人统一管控",
             "play": "Play1+6"},
            {"name": "客服Agent",
             "agent_does": "理解→知识库→解决\n→记住→升级",
             "vs_traditional": "关键词匹配\n不记上下文",
             "components": "Memory+Gateway\n+Evaluations",
             "auto_lighthouse": "BYD/蔚来",
             "mfg_lighthouse": "Tuya/海尔",
             "why_cloud": "7×24弹性\n全球多语言",
             "play": "Play2+3+4"},
            {"name": "销售Agent",
             "agent_does": "CRM→优先级→跟进\n→报价",
             "vs_traditional": "RPA填表\nBI看报表",
             "components": "Browser+Gateway\n+Memory",
             "auto_lighthouse": "BYD(40国)\n长城",
             "mfg_lighthouse": "三一/Anker",
             "why_cloud": "多国CRM统一",
             "play": "Play2+5"},
            {"name": "财务Agent",
             "agent_does": "发票→记账→异常\n→月结报表",
             "vs_traditional": "Excel固定\n人工核对",
             "components": "Code Interp+Policy\n+Observability",
             "auto_lighthouse": "上汽/BYD",
             "mfg_lighthouse": "美的/海尔",
             "why_cloud": "Policy确定性\n审计链",
             "play": "Play4+5"},
            {"name": "审计/风控",
             "agent_does": "全量扫描→风险评分\n→合规→告警",
             "vs_traditional": "人工抽检\n覆盖不全",
             "components": "Browser+Code Interp\n+Policy+Obs",
             "auto_lighthouse": "—",
             "mfg_lighthouse": "海康/施耐德",
             "why_cloud": "Policy不可绕\n审计=合规",
             "play": "Play1+4+5"},
            {"name": "供应链Agent",
             "agent_does": "需求预测→库存→供应商\n→物流",
             "vs_traditional": "BI看历史\n不主动调",
             "components": "Gateway+Code Interp\n+Memory",
             "auto_lighthouse": "BYD(零件)\n长安",
             "mfg_lighthouse": "美的/三一",
             "why_cloud": "多Region\n跨系统",
             "play": "Play2+5"},
        ]

        poc_recommendations = [
            {"num": "1", "poc": "OpenClaw on AgentCore", "customer": "西门子/施耐德",
             "industry": "MFG", "components": "Browser+Identity+Policy+Obs",
             "why": "外企合规刚需→复制所有外企", "type": "🍎→📈", "target": "W18 POC"},
            {"num": "2", "poc": "IoT Agent平台", "customer": "Tuya",
             "industry": "MFG", "components": "Gateway+Registry+Runtime",
             "why": "核心商业模式匹配 XXL", "type": "🏆Lighthouse", "target": "W18 POC"},
            {"num": "3", "poc": "Agent质量Eval", "customer": "科沃斯/萤石",
             "industry": "MFG", "components": "Evaluations+Memory",
             "why": "C端快迭代+品牌联合", "type": "🍎→🏆", "target": "W18 POC"},
            {"num": "+1", "poc": "Agent Factory案例", "customer": "美的",
             "industry": "MFG", "components": "全套",
             "why": "案例已有 包装即可", "type": "🏆Lighthouse", "target": "W17 包装"},
            {"num": "+2", "poc": "车载Memory", "customer": "蔚来NIO",
             "industry": "AUTO", "components": "Memory+Identity+Eval",
             "why": "NOMI在Azure 竞争置换", "type": "🏆→📈", "target": "W19 接触"},
        ]
    else:
        # Generic empty structure for other products
        plays = []
        external_scenarios = []
        internal_scenarios = []
        poc_recommendations = []

    return {
        "plays": plays,
        "external_scenarios": external_scenarios,
        "internal_scenarios": internal_scenarios,
        "poc_recommendations": poc_recommendations,
    }


def _compute_penetration(accounts: list[dict]) -> dict:
    """Compute bedrock and genai penetration stats."""
    total = len(accounts)
    with_bedrock = [a for a in accounts if float(a.get("bedrock", 0)) > 0]
    with_genai = [a for a in accounts if float(a.get("genai", 0)) > 0]

    bedrock_pct = (len(with_bedrock) / total * 100) if total > 0 else 0
    genai_pct = (len(with_genai) / total * 100) if total > 0 else 0

    return {
        "bedrock_penetration": {
            "total_with_bedrock": len(with_bedrock),
            "total_accounts": total,
            "pct": round(bedrock_pct, 1),
        },
        "genai_penetration": {
            "total_with_genai": len(with_genai),
            "total_accounts": total,
            "pct": round(genai_pct, 1),
        },
    }


def run_analysis(bu_name: str, product: str, accounts_path: str,
                 tshirt_min: str = "L", template: str = "all",
                 output_dir: str = "/tmp/gtm-output/") -> dict:
    """
    Run the full GTM analysis pipeline.

    Returns dict with paths to generated files.
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. Load accounts from Sentral export
    with open(accounts_path) as f:
        accounts = json.load(f)
    print(f"Loaded {len(accounts)} accounts from {accounts_path}")

    # 2. Query revenue from Athena
    print(f"Querying revenue for BU: {bu_name}...")
    revenue = query_revenue(bu_name, tshirt_min)
    print(f"Got revenue for {len(revenue)} accounts")

    # Save raw revenue
    rev_path = os.path.join(output_dir, "revenue_data.json")
    with open(rev_path, "w") as f:
        json.dump(revenue, f, indent=2)

    # 3. Merge accounts with revenue
    accounts = merge_accounts_with_revenue(accounts, revenue)

    # Filter by tshirt
    size_order = {"XXL": 3, "XL": 2, "L": 1}
    min_ord = size_order.get(tshirt_min, 1)
    accounts = [a for a in accounts if size_order.get(a.get("size", ""), 0) >= min_ord]
    print(f"After T-shirt filter ({tshirt_min}+): {len(accounts)} accounts")

    # 4. Compute summary stats
    total_ttm = sum(float(a.get("ttm", 0)) for a in accounts)
    total_genai = sum(float(a.get("genai", 0)) for a in accounts)
    total_bedrock = sum(float(a.get("bedrock", 0)) for a in accounts)

    top_accounts = sorted(accounts, key=lambda x: -float(x.get("ttm", 0)))
    bedrock_accounts = sorted(
        [a for a in accounts if float(a.get("bedrock", 0)) > 0],
        key=lambda x: -float(x.get("bedrock", 0))
    )

    # 4a. Compute category distribution
    categories = _compute_categories(accounts)

    # 4b. Compute penetration stats
    penetration = _compute_penetration(accounts)

    # 4c. Load product knowledge components
    product_components = _load_product_knowledge(product)

    # 4c2. Load competitive data
    competitive_data = _load_competitive_data(product)

    # 4d. Classify top 20 accounts into priority quadrants
    # Quick Win = has bedrock + high TTM (top-right)
    # Strategic = high TTM but no bedrock (top-left)
    # Seed = has bedrock but low TTM (bottom-right)
    # Monitor = low TTM + no bedrock (bottom-left)
    sorted_by_ttm = sorted(accounts, key=lambda x: -float(x.get("ttm", 0)))
    ttm_median = float(sorted_by_ttm[len(sorted_by_ttm) // 2].get("ttm", 0)) if sorted_by_ttm else 0

    quick_win, strategic, seed, monitor = [], [], [], []
    for a in sorted_by_ttm[:20]:
        has_bedrock = float(a.get("bedrock", 0)) > 0
        high_ttm = float(a.get("ttm", 0)) >= ttm_median
        entry = {
            "name": a.get("short", a.get("name", "")),
            "ttm": float(a.get("ttm", 0)),
            "bedrock": float(a.get("bedrock", 0)),
            "genai": float(a.get("genai", 0)),
            "category": a.get("category", ""),
        }
        if has_bedrock and high_ttm:
            quick_win.append(entry)
        elif high_ttm and not has_bedrock:
            strategic.append(entry)
        elif has_bedrock and not high_ttm:
            seed.append(entry)
        else:
            monitor.append(entry)

    # 4e. Generate GTM plays and scenarios
    gtm_plays_data = _generate_gtm_plays(categories, product_components, {
        "quick_win": quick_win, "strategic": strategic,
        "seed": seed, "monitor": monitor,
    }, product)

    # 4f. Generate expanded scenario rows for AI場景 sheet
    scenario_rows = _generate_scenario_rows(categories, product_components, product)

    summary_data = {
        "bu_name": bu_name,
        "product": product,
        "total_accounts": len(accounts),
        "total_ttm": total_ttm,
        "total_genai": total_genai,
        "total_bedrock": total_bedrock,
        "auto_count": sum(1 for a in accounts if a.get("industry") == "AUTO"),
        "mfg_count": sum(1 for a in accounts if a.get("industry") != "AUTO"),
        "top_accounts": [
            {"name": a.get("short", a.get("name", "")), "ttm": float(a.get("ttm", 0)),
             "genai": float(a.get("genai", 0)), "bedrock": float(a.get("bedrock", 0)),
             "category": a.get("category", "")}
            for a in top_accounts[:20]
        ],
        "bedrock_accounts": [
            {"name": a.get("short", a.get("name", "")), "bedrock": float(a.get("bedrock", 0)),
             "genai": float(a.get("genai", 0)), "ttm": float(a.get("ttm", 0)),
             "category": a.get("category", "")}
            for a in bedrock_accounts[:15]
        ],
        "categories": categories,
        "bedrock_penetration": penetration["bedrock_penetration"],
        "genai_penetration": penetration["genai_penetration"],
        "product_components": product_components,
        "priority": {
            "quick_win": quick_win,
            "strategic": strategic,
            "seed": seed,
            "monitor": monitor,
        },
        "plays": gtm_plays_data.get("plays", []),
        "scenarios": scenario_rows,
        "competitive": competitive_data,
        "gtm_plays_data": gtm_plays_data,
    }

    outputs = {"revenue": rev_path}

    # 5. Generate reports
    if template in ("rob", "all"):
        rob_path = os.path.join(output_dir, "rob_summary.html")
        html = render_rob_summary(summary_data)
        with open(rob_path, "w") as f:
            f.write(html)
        outputs["rob"] = rob_path
        print(f"Rob summary: {rob_path}")

    if template in ("gm", "all"):
        gm_path = os.path.join(output_dir, "gm_detailed.html")
        html = render_gm_detailed(summary_data)
        with open(gm_path, "w") as f:
            f.write(html)
        outputs["gm"] = gm_path
        print(f"GM detailed: {gm_path}")

    if template in ("excel", "all"):
        excel_path = os.path.join(output_dir, "gtm_analysis.xlsx")
        result = build_excel(accounts, excel_path, product=product,
                             categories=categories,
                             bedrock_penetration=penetration["bedrock_penetration"],
                             competitive=competitive_data,
                             gtm_plays_data=gtm_plays_data,
                             scenario_rows=scenario_rows)
        outputs["excel"] = excel_path
        print(f"Excel: {excel_path} ({result['total']} accounts: AUTO {result['auto']} + MFG {result['mfg']})")

    print(f"\n✅ Done. Outputs in {output_dir}")
    return outputs


def main():
    args = parse_args()
    run_analysis(
        bu_name=args.bu,
        product=args.product,
        accounts_path=args.accounts,
        tshirt_min=args.tshirt,
        template=args.template,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
