---
name: s_meddpicc-scorecard
description: >
  Score Salesforce opportunities using the AWS MEDDPICC framework (8 dimensions, 100 points).
  Pulls data from AWSentral MCP, scores each dimension against evidence, checks stage gates,
  detects risk signals, and generates prioritized actions with estimated point impact.
  TRIGGER: "MEDDPICC", "MEDDPICC analysis", "MEDDPICC score", "score this opportunity",
  "qualify this deal", "opportunity scorecard", "MEDDPICC 打分", "MEDDPICC 分析".
  DO NOT USE: for general account health reviews without scoring (use AWSentral directly),
  pipeline summary without per-deal qualification, or non-sales analysis.
tier: lazy
---
# s_meddpicc-scorecard

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

TRIGGER: "MEDDPICC", "MEDDPICC analysis", "MEDDPICC score", "score this opportunity",
DO NOT USE: for general account health reviews without scoring (use AWSentral directly),
