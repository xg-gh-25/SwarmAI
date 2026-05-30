---
name: cmhk-bms-risk
description: >
  BMS Workload Risk & Alert intelligence for CMHK leadership.
  Queries fd_workload_risk_integrated and app_risk_indicators_*_alert from Athena.
  Outputs: Risk summary by BU, competitor threat analysis, alert heatmap, account-level risk detail.
  Self-contained — does NOT depend on other CMHK skills.
  TRIGGER: "BMS risk", "workload risk", "risk report", "alert heatmap", "at risk accounts",
           "风险报告", "workload 风险", "BMS 数据".
  DO NOT USE: for revenue reports (use cmhk-weekly-report), forecast (use cmhk-forecast-report),
              or account-360 (use cmhk-account-360).
tier: lazy
platform: all
---
# cmhk-bms-risk

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "BMS risk", "workload risk", "risk report", "alert heatmap", "at risk accounts", "风险报告"
