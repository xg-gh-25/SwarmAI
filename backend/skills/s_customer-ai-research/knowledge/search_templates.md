# Search Query Templates

## Per-customer queries (Chinese)

```
"{company} AI Agent 场景 2025 2026"
"{company} 智能体 产品 agentic"
"{company} OpenClaw 龙虾"
```

## Per-customer queries (English)

```
"{company} AI agent product 2025 2026"
"{company} agentic AI cloud platform"
```

## Per-industry batch queries

```
"中国{industry} AI Agent 场景 2025"
"{industry} AI agent China companies"
```

## Usage

The agent substitutes `{company}` and `{industry}` at runtime.
Use `researcher.build_search_queries(customer_name, industry)` to generate
the full list programmatically.
