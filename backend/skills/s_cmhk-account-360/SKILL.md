---
name: cmhk-account-360
description: >
  Unified Account 360 analysis for CMHK customers. Combines company profile,
  AWS revenue, pipeline, and call plan into one 4-tab HTML report. Uses Sentral
  MCP for real-time account/opportunity data, DataProxy SDK for Athena revenue,
  PhoneTool for people, and web search for news.
  TRIGGER: "account 360", "客户全景", "分析 XX 客户", "XX 客户分析",
  "call plan for XX", "account analysis".
  DO NOT USE: for weekly revenue reports (use cmhk-weekly-report), for GTM
  industry analysis (use cmhk-industry-gtm-analysis), for pipeline-only queries
  (use cmhk-data-proxy).
tier: lazy
platform: all
---
# Account 360

Unified customer analysis — 4 Rocky skills in 1. Read INSTRUCTIONS.md for the
full workflow.
