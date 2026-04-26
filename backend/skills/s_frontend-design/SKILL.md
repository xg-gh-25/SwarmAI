---
name: Frontend Design
description: >
  Create production-grade frontend interfaces with design intelligence database (67 styles, 161 palettes, 57 fonts, 161 industry rules).
  TRIGGER: "build a landing page", "create a website", "design a UI", "frontend prototype", "interactive page", "HTML page", "web app mockup".
  DO NOT USE: for backend APIs (just code directly) or for reviewing existing UI (use web-design-review).
version: "2.0.0"
tier: lazy
---
# Frontend Design

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "build a landing page", "create a website", "design a UI", "frontend prototype", "interactive page", "HTML page", "web app mockup"
DO NOT USE: for backend APIs (just code directly) or for reviewing existing UI (use web-design-review)

## MANDATORY: Generate Design System Before Coding

Before writing ANY frontend code, run the design system generator to get industry-specific style, color, typography, and anti-pattern recommendations:

```bash
python3 scripts/search.py "<product description>" --design-system -p "ProjectName" -f markdown
```

This is NOT optional. The database has 161 industry-specific reasoning rules that prevent generic AI aesthetics. Skip this step only if the user explicitly provides a complete design system (colors, fonts, style).
