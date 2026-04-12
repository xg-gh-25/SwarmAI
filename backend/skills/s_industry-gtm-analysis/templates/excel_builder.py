"""
Working-level Excel builder — multi-sheet customer analysis workbook.
Sheets: AUTO 客户明细 / MFG 客户明细 (split based on category).
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


def build_excel(accounts: list[dict], output_path: str, product: str = ""):
    """
    Build multi-sheet Excel workbook.

    Args:
        accounts: list of account dicts with revenue data
        output_path: where to save the .xlsx file
        product: AWS product name (for sheet titles)
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

    wb.save(output_path)
    return {"auto": n_auto, "mfg": n_mfg, "total": n_auto + n_mfg}
