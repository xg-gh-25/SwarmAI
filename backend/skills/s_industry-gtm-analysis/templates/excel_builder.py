"""
Working-level Excel builder — multi-sheet customer analysis workbook.
Sheets: AUTO 客户明细 / MFG 客户明细 / AI场景 by 产品大类 / Bedrock 渗透分析
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Styles
HF = PatternFill('solid', fgColor='1F4E79')
HFont = Font(bold=True, color='FFFFFF', size=11)
XXL_F = PatternFill('solid', fgColor='FFF2CC')
XL_F = PatternFill('solid', fgColor='E2EFDA')
L_F = PatternFill('solid', fgColor='D6E4F0')
AUTO_BG = PatternFill('solid', fgColor='D6E4F0')  # Blue background for AUTO
MFG_BG = PatternFill('solid', fgColor='E2EFDA')   # Green background for MFG
C = Alignment(horizontal='center', vertical='center', wrap_text=True)
W = Alignment(wrap_text=True, vertical='top')
T = Border(left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
           top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9'))

AUTO_CATS = {'汽车整车', '自动驾驶/智驾', '汽车零部件', '出行/两轮'}


def _write_sheet(ws, accounts):
    """Write account data to a worksheet."""
    headers = ['#', '客户名称', '全称', '网站', 'Size', 'Owner',
               'TTM Revenue', 'GenAI TTM', 'Bedrock TTM', 'GenAI %',
               '产品大类', '主要产品', 'AI Agent 场景', 'OpenClaw',
               'AI成熟度', 'GTM备注', 'SFDC Link']
    widths = [4, 15, 28, 20, 6, 13, 12, 11, 11, 8,
              13, 26, 30, 24, 9, 26, 14]

    for c_idx, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=c_idx, value=h)
        cell.font = HFont
        cell.fill = HF
        cell.alignment = C
        cell.border = T
        ws.column_dimensions[get_column_letter(c_idx)].width = w

    ws.freeze_panes = 'A2'

    SIZE_ORD = {'XXL': 0, 'XL': 1, 'L': 2}
    accounts.sort(key=lambda x: (SIZE_ORD.get(x.get('size', ''), 9), x.get('short', '')))

    for i, acc in enumerate(accounts, 1):
        r = i + 1
        ttm = float(acc.get('ttm', 0) or 0)
        genai = float(acc.get('genai', 0) or 0)
        bedrock = float(acc.get('bedrock', 0) or 0)
        genai_pct = genai / ttm if ttm > 0 else 0

        ws.cell(row=r, column=1, value=i).alignment = C
        ws.cell(row=r, column=2, value=acc.get('short', ''))
        ws.cell(row=r, column=3, value=acc.get('name', ''))
        ws.cell(row=r, column=4, value=acc.get('website', ''))
        ws.cell(row=r, column=5, value=acc.get('size', '')).alignment = C
        ws.cell(row=r, column=6, value=acc.get('owner', ''))
        ws.cell(row=r, column=7, value=round(ttm)).number_format = '#,##0'
        ws.cell(row=r, column=7).alignment = C
        ws.cell(row=r, column=8, value=round(genai)).number_format = '#,##0'
        ws.cell(row=r, column=8).alignment = C
        ws.cell(row=r, column=9, value=round(bedrock)).number_format = '#,##0'
        ws.cell(row=r, column=9).alignment = C
        ws.cell(row=r, column=10, value=genai_pct).number_format = '0.0%'
        ws.cell(row=r, column=10).alignment = C
        ws.cell(row=r, column=11, value=acc.get('category', ''))
        ws.cell(row=r, column=12, value=acc.get('products', '')).alignment = W
        ws.cell(row=r, column=13, value=acc.get('ai_scenarios', '')).alignment = W
        ws.cell(row=r, column=14, value=acc.get('openclaw', '')).alignment = W
        ws.cell(row=r, column=15, value=acc.get('maturity', '')).alignment = C
        ws.cell(row=r, column=16, value=acc.get('gtm', '')).alignment = W

        sfdc = acc.get('sfdc_url', '')
        if sfdc:
            cell = ws.cell(row=r, column=17)
            cell.value = sfdc
            cell.hyperlink = sfdc
            cell.font = Font(color='0563C1', underline='single', size=9)

        sf = {'XXL': XXL_F, 'XL': XL_F, 'L': L_F}.get(acc.get('size', ''))
        if sf:
            ws.cell(row=r, column=5).fill = sf
        if bedrock > 0:
            ws.cell(row=r, column=9).fill = PatternFill('solid', fgColor='E2EFDA')

        for c_idx in range(1, 18):
            ws.cell(row=r, column=c_idx).border = T

    ws.auto_filter.ref = f'A1:Q{len(accounts) + 1}'
    return len(accounts)


def _write_category_sheet(ws, categories: dict):
    """Sheet 3: AI场景 by 产品大类 — one row per category."""
    headers = ['行业', '产品大类', '客户数', 'TTM', 'GenAI', 'Bedrock', 'XXL客户', 'XL客户']
    widths = [8, 18, 8, 14, 12, 12, 30, 30]

    for c_idx, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=c_idx, value=h)
        cell.font = HFont
        cell.fill = HF
        cell.alignment = C
        cell.border = T
        ws.column_dimensions[get_column_letter(c_idx)].width = w

    ws.freeze_panes = 'A2'

    # Sort: AUTO first, then MFG; within each group sort by TTM desc
    sorted_cats = sorted(
        categories.items(),
        key=lambda x: (0 if x[1].get('industry') == 'AUTO' else 1, -x[1].get('ttm', 0))
    )

    for i, (cat_name, cat_data) in enumerate(sorted_cats, 1):
        r = i + 1
        industry = cat_data.get('industry', 'MFG')
        is_auto = industry == 'AUTO'
        row_fill = AUTO_BG if is_auto else MFG_BG

        ws.cell(row=r, column=1, value=industry).alignment = C
        ws.cell(row=r, column=1).fill = row_fill
        ws.cell(row=r, column=2, value=cat_name)
        ws.cell(row=r, column=3, value=cat_data.get('count', 0)).alignment = C
        ws.cell(row=r, column=4, value=round(cat_data.get('ttm', 0))).number_format = '#,##0'
        ws.cell(row=r, column=4).alignment = C
        ws.cell(row=r, column=5, value=round(cat_data.get('genai', 0))).number_format = '#,##0'
        ws.cell(row=r, column=5).alignment = C
        ws.cell(row=r, column=6, value=round(cat_data.get('bedrock', 0))).number_format = '#,##0'
        ws.cell(row=r, column=6).alignment = C
        ws.cell(row=r, column=7, value=', '.join(cat_data.get('xxl', []))).alignment = W
        ws.cell(row=r, column=8, value=', '.join(cat_data.get('xl', []))).alignment = W

        for c_idx in range(1, 9):
            ws.cell(row=r, column=c_idx).border = T

    ws.auto_filter.ref = f'A1:H{len(sorted_cats) + 1}'
    return len(sorted_cats)


def _write_bedrock_sheet(ws, accounts: list[dict]):
    """Sheet 4: Bedrock 渗透分析 — accounts with Bedrock > 0, sorted desc."""
    headers = ['#', '客户', 'Size', 'TTM', 'Bedrock TTM', 'Bedrock %', '产品大类']
    widths = [4, 18, 6, 14, 14, 10, 18]

    for c_idx, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=c_idx, value=h)
        cell.font = HFont
        cell.fill = HF
        cell.alignment = C
        cell.border = T
        ws.column_dimensions[get_column_letter(c_idx)].width = w

    ws.freeze_panes = 'A2'

    # Filter and sort by bedrock desc
    bedrock_accts = [a for a in accounts if float(a.get('bedrock', 0) or 0) > 0]
    bedrock_accts.sort(key=lambda x: -float(x.get('bedrock', 0) or 0))

    for i, acc in enumerate(bedrock_accts, 1):
        r = i + 1
        ttm = float(acc.get('ttm', 0) or 0)
        bedrock = float(acc.get('bedrock', 0) or 0)
        bedrock_pct = bedrock / ttm if ttm > 0 else 0

        ws.cell(row=r, column=1, value=i).alignment = C
        ws.cell(row=r, column=2, value=acc.get('short', acc.get('name', '')))
        ws.cell(row=r, column=3, value=acc.get('size', '')).alignment = C
        ws.cell(row=r, column=4, value=round(ttm)).number_format = '#,##0'
        ws.cell(row=r, column=4).alignment = C
        ws.cell(row=r, column=5, value=round(bedrock)).number_format = '#,##0'
        ws.cell(row=r, column=5).alignment = C
        ws.cell(row=r, column=5).fill = PatternFill('solid', fgColor='E2EFDA')
        ws.cell(row=r, column=6, value=bedrock_pct).number_format = '0.0%'
        ws.cell(row=r, column=6).alignment = C
        ws.cell(row=r, column=7, value=acc.get('category', ''))

        # Size fill
        sf = {'XXL': XXL_F, 'XL': XL_F, 'L': L_F}.get(acc.get('size', ''))
        if sf:
            ws.cell(row=r, column=3).fill = sf

        for c_idx in range(1, 8):
            ws.cell(row=r, column=c_idx).border = T

    ws.auto_filter.ref = f'A1:G{len(bedrock_accts) + 1}'
    return len(bedrock_accts)


def build_excel(accounts: list[dict], output_path: str, product: str = "",
                categories: dict = None, bedrock_penetration: dict = None):
    """
    Build multi-sheet Excel workbook.

    Args:
        accounts: list of account dicts with revenue data
        output_path: where to save the .xlsx file
        product: AWS product name (for sheet titles)
        categories: dict of category distributions (from analyzer)
        bedrock_penetration: dict with penetration stats (from analyzer)
    """
    wb = Workbook()

    # Split AUTO vs MFG
    auto = [a for a in accounts if a.get('category', '') in AUTO_CATS or a.get('industry') == 'AUTO']
    mfg = [a for a in accounts if a not in auto]

    # Sheet 1: AUTO
    ws_auto = wb.active
    ws_auto.title = 'AUTO 客户明细'
    n_auto = _write_sheet(ws_auto, auto)

    # Sheet 2: MFG
    ws_mfg = wb.create_sheet('MFG 客户明细')
    n_mfg = _write_sheet(ws_mfg, mfg)

    # Sheet 3: AI场景 by 产品大类
    if categories:
        ws_cat = wb.create_sheet('AI场景 by 产品大类')
        _write_category_sheet(ws_cat, categories)

    # Sheet 4: Bedrock 渗透分析
    bedrock_accts = [a for a in accounts if float(a.get('bedrock', 0) or 0) > 0]
    if bedrock_accts:
        ws_br = wb.create_sheet('Bedrock 渗透分析')
        _write_bedrock_sheet(ws_br, accounts)

    wb.save(output_path)
    return {"auto": n_auto, "mfg": n_mfg, "total": n_auto + n_mfg}
