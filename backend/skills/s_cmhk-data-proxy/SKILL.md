---
name: cmhk-data-proxy
description: "Query CMHK revenue, usage, forecast, and account data via the DataProxy API. Covers Athena (fact_estimated_revenue), GCR Sales Data API (forecast cycles, baselines, inputs), hierarchy permissions,\
  \ and report publishing.\n  TRIGGER: \"revenue\", \"usage\", \"CMHK data\", \"weekly numbers\".\n  NOT FOR: aws-sentral-mcp use cases."
version: 1.0.0
tier: lazy
---
# CMHK Data Proxy

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "revenue", "usage", "CMHK data", "weekly numbers", "forecast",
DO NOT USE: for generating full HTML reports (that stays on DataRetriever cron),
