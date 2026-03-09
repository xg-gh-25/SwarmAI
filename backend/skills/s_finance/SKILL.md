---
name: Finance
description: >
  Invoice generation, expense tracking, financial calculations, and basic bookkeeping. Zero external dependencies.
  TRIGGER: "invoice", "expense", "budget", "profit", "revenue", "financial", "accounting", "receipt", "tax calculation", "ROI", "cashflow", "P&L".
  DO NOT USE: for stock trading, real-time market data, or professional tax filing (consult a CPA).
  SIBLINGS: xlsx = spreadsheet creation/analysis | pdf = generate PDF invoices | finance = financial logic and templates.
version: "1.0.0"
---

# Finance

Generate invoices, track expenses, perform financial calculations, and create basic financial reports. Zero external dependencies -- uses Python3 stdlib, CSV files, and sibling skills (xlsx, pdf) for output.

## Capabilities

| Feature | How |
|---------|-----|
| Invoice generation | Python3 + pdf skill or HTML template |
| Expense tracking | CSV-based ledger (append-only, portable) |
| Financial calculations | Python3 decimal module (precise math) |
| Budget reports | CSV data + xlsx skill for charts |
| P&L statements | Aggregate from expense/income ledger |
| Tax estimates | Configurable rates, basic calculations |

## Workflow

### Step 1: Determine the Task

| User Request | Action |
|-------------|--------|
| "Create an invoice" | Generate invoice (Step 2a) |
| "Track an expense" | Append to expense ledger (Step 2b) |
| "Show my expenses" | Query and summarize ledger (Step 2c) |
| "Calculate ROI / margins" | Financial calculation (Step 2d) |
| "Monthly P&L" | Generate P&L report (Step 2e) |
| "Budget template" | Create budget spreadsheet (Step 2f) |

### Step 2a: Invoice Generation

#### Data Collection

Ask for (or extract from context):
- **From**: Business name, address, email
- **To**: Client name, address, email
- **Items**: Description, quantity, unit price
- **Invoice number**: Auto-increment or user-specified
- **Date**: Today or user-specified
- **Due date**: Net 30 by default
- **Tax rate**: 0% default, configurable
- **Notes**: Payment terms, bank details, etc.

#### Generate Invoice

Option 1: **HTML Invoice** (zero dependencies, opens in browser)

```python
#!/usr/bin/env python3
"""Generate a professional HTML invoice. Zero dependencies."""
import json, datetime, decimal

def generate_invoice(data):
    """
    data = {
        "invoice_number": "INV-001",
        "date": "2026-03-09",
        "due_date": "2026-04-08",
        "from": {"name": "...", "address": "...", "email": "..."},
        "to": {"name": "...", "address": "...", "email": "..."},
        "items": [
            {"description": "...", "quantity": 1, "unit_price": "100.00"}
        ],
        "tax_rate": "0.00",
        "notes": "Payment due within 30 days.",
        "currency": "USD"
    }
    """
    D = decimal.Decimal
    subtotal = sum(D(str(i["quantity"])) * D(str(i["unit_price"])) for i in data["items"])
    tax = subtotal * D(str(data.get("tax_rate", "0"))) / D("100")
    total = subtotal + tax

    currency_symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "KRW": "₩", "THB": "฿"}
    sym = currency_symbols.get(data.get("currency", "USD"), "$")

    # Returns HTML string -- write to file and open
    # ... (full template in implementation)
```

The agent generates a clean HTML invoice with:
- Professional styling (no external CSS/fonts needed)
- Proper decimal arithmetic (no floating point errors)
- Auto-calculated subtotal, tax, total
- Print-friendly layout

Then optionally convert to PDF using the pdf skill.

Option 2: **CSV invoice** for import into accounting software
Option 3: **XLSX invoice** using the xlsx skill

#### Invoice Number Tracking

Store the last invoice number for auto-increment:

```bash
# Invoice counter file
COUNTER_FILE="~/.swarm-ai/SwarmWS/.context/invoice_counter"

# Read current
LAST=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")

# Increment
NEXT=$((LAST + 1))
echo "$NEXT" > "$COUNTER_FILE"

# Format
printf "INV-%04d\n" "$NEXT"  # INV-0001, INV-0002, ...
```

### Step 2b: Expense Tracking

#### Ledger Format

Use a simple CSV ledger (append-only, works with any tool):

```
File: ~/.swarm-ai/SwarmWS/.context/finance/ledger.csv
```

Schema:
```csv
date,type,category,description,amount,currency,tags,notes
2026-03-09,expense,software,GitHub Pro subscription,-4.00,USD,recurring;tools,Monthly
2026-03-09,income,consulting,Client ABC - March invoice,5000.00,USD,client-abc,INV-0042
```

Rules:
- **type**: `income` or `expense`
- **amount**: Positive for income, negative for expense
- **category**: user-defined (software, travel, meals, consulting, etc.)
- **tags**: semicolon-separated for flexible filtering
- **Append-only**: never modify past entries; add corrections as new entries

#### Adding an Expense

```python
#!/usr/bin/env python3
"""Append an entry to the finance ledger."""
import csv, os, datetime

LEDGER = os.path.expanduser("~/.swarm-ai/SwarmWS/.context/finance/ledger.csv")

def add_entry(entry_type, category, description, amount, currency="USD", tags="", notes=""):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    file_exists = os.path.exists(LEDGER)

    with open(LEDGER, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["date", "type", "category", "description", "amount", "currency", "tags", "notes"])
        writer.writerow([
            datetime.date.today().isoformat(),
            entry_type,
            category,
            description,
            f"{float(amount):.2f}",
            currency,
            tags,
            notes
        ])
```

### Step 2c: Query & Summarize Expenses

```python
#!/usr/bin/env python3
"""Query the finance ledger with filters."""
import csv, decimal, datetime, collections

D = decimal.Decimal

def query_ledger(ledger_path, filters=None):
    """
    filters = {
        "start_date": "2026-01-01",
        "end_date": "2026-03-31",
        "type": "expense",          # income or expense
        "category": "software",     # exact match
        "tag": "recurring",         # substring match in tags
        "min_amount": "-1000",
        "max_amount": "0",
    }
    """
    entries = []
    with open(ledger_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Apply filters...
            entries.append(row)

    # Aggregate by category
    by_category = collections.defaultdict(lambda: D("0"))
    for e in entries:
        by_category[e["category"]] += D(e["amount"])

    total = sum(D(e["amount"]) for e in entries)
    return {"entries": entries, "by_category": dict(by_category), "total": total}
```

Present results as:

```markdown
## Expense Summary: March 2026

| Category | Amount | % of Total |
|----------|--------|------------|
| Software | -$240.00 | 32% |
| Travel | -$180.00 | 24% |
| Meals | -$120.00 | 16% |
| Office | -$210.00 | 28% |
| **Total** | **-$750.00** | **100%** |

Top 3 expenses:
1. Flight to Tokyo - $450.00 (Travel)
2. Annual Figma license - $150.00 (Software)
3. Standing desk - $210.00 (Office)
```

### Step 2d: Financial Calculations

All calculations use Python3 `decimal` module for precision.

```python
#!/usr/bin/env python3
"""Common financial calculations. Zero dependencies."""
from decimal import Decimal as D, ROUND_HALF_UP

def roi(gain, cost):
    """Return on Investment as percentage."""
    return ((D(str(gain)) - D(str(cost))) / D(str(cost)) * 100).quantize(D("0.01"))

def margin(revenue, cost):
    """Profit margin as percentage."""
    return ((D(str(revenue)) - D(str(cost))) / D(str(revenue)) * 100).quantize(D("0.01"))

def compound_interest(principal, rate_pct, years, compounds_per_year=12):
    """Future value with compound interest."""
    P, r, n, t = D(str(principal)), D(str(rate_pct)) / 100, D(str(compounds_per_year)), D(str(years))
    return (P * (1 + r/n) ** (n*t)).quantize(D("0.01"))

def loan_payment(principal, annual_rate_pct, years):
    """Monthly payment for a fixed-rate loan."""
    P = D(str(principal))
    r = D(str(annual_rate_pct)) / 100 / 12
    n = D(str(years)) * 12
    if r == 0:
        return (P / n).quantize(D("0.01"))
    payment = P * (r * (1 + r)**n) / ((1 + r)**n - 1)
    return payment.quantize(D("0.01"))

def tax_estimate(income, rate_pct):
    """Simple flat tax estimate."""
    return (D(str(income)) * D(str(rate_pct)) / 100).quantize(D("0.01"))

def breakeven(fixed_costs, price_per_unit, variable_cost_per_unit):
    """Units needed to break even."""
    return (D(str(fixed_costs)) / (D(str(price_per_unit)) - D(str(variable_cost_per_unit)))).quantize(D("1"), rounding=ROUND_HALF_UP)
```

### Step 2e: P&L Statement

Generate from the ledger:

```markdown
## Profit & Loss: Q1 2026 (Jan-Mar)

### Revenue
| Source | Amount |
|--------|--------|
| Consulting | $15,000.00 |
| Product Sales | $3,200.00 |
| **Total Revenue** | **$18,200.00** |

### Expenses
| Category | Amount |
|----------|--------|
| Software & Tools | -$720.00 |
| Travel | -$1,800.00 |
| Contractor Payments | -$4,500.00 |
| Office & Equipment | -$600.00 |
| **Total Expenses** | **-$7,620.00** |

### Summary
| | Amount |
|--|--------|
| Gross Revenue | $18,200.00 |
| Total Expenses | -$7,620.00 |
| **Net Profit** | **$10,580.00** |
| Profit Margin | 58.1% |
```

### Step 2f: Budget Template

Create using xlsx skill:

```
Budget Template Columns:
- Category
- Budgeted (Monthly)
- Actual (Month-to-date)
- Variance
- Variance %
- YTD Budget
- YTD Actual
- YTD Variance
```

---

## Data Storage

All financial data is stored locally:

```
~/.swarm-ai/SwarmWS/.context/finance/
  ledger.csv          # Main income/expense ledger
  invoices/           # Generated invoices (HTML/PDF)
  invoice_counter     # Auto-increment counter
```

No cloud sync, no external services, no API keys.

---

## Currency Handling

- Default currency: USD (configurable per entry)
- For currency conversion, use free API:
  ```bash
  # Free, no key required
  curl -s "https://open.er-api.com/v6/latest/USD" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['rates']['EUR'])"
  ```
- Always display currency symbol with amounts
- Never mix currencies in calculations without explicit conversion

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Floating point errors | Always use `decimal.Decimal`, never `float` for money |
| Ledger CSV corrupted | Check for unescaped commas in description; use csv module, not string splitting |
| Invoice looks wrong | Open HTML in browser, check print preview; adjust CSS in template |
| Category inconsistency | List unique categories from ledger: `cut -d, -f3 ledger.csv | sort -u` |
| Tax calculation wrong | This skill does estimates only; always note "consult a tax professional" |

## Safety Rules

- This is NOT tax advice or professional accounting -- always include disclaimer
- Never delete or modify past ledger entries -- append corrections instead
- All amounts use decimal.Decimal for precision (never float)
- Sensitive financial data stays local (no external API sends your data)
- Invoice numbers are sequential and never reused
- Zero dependencies: uses only Python3 stdlib, CSV, and sibling skills (xlsx, pdf)
