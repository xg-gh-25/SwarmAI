# AgentCore — Product Knowledge

## 10 Components

| # | Component | Description | Key Use Case |
|---|-----------|-------------|-------------|
| 1 | Runtime | MicroVM serverless execution, 8hr async, per-second billing | Long-running agent tasks, AD simulation |
| 2 | Memory | Short-term (session) + Long-term (cross-session semantic), shareable | Car AI remembers driver, robot learns habits |
| 3 | Gateway | API→MCP conversion, semantic tool discovery (1000s of tools) | IoT device orchestration, factory multi-system |
| 4 | Policy | Cedar deterministic guardrails, natural language authoring | Industrial safety rules, financial compliance |
| 5 | Identity | Per-agent workload identity, Cognito/Okta/Entra ID | Enterprise OpenClaw management, multi-user devices |
| 6 | Browser | Cloud browser sandbox, Playwright, session recording, manual takeover | Enterprise agent web automation, remote monitoring |
| 7 | Code Interpreter | Python/JS/TS sandboxed execution, file I/O, pip packages | Data analysis agents, algorithm verification |
| 8 | Observability | OpenTelemetry native, CloudWatch, third-party export | Agent behavior audit, production debugging |
| 9 | Evaluations | LLM-as-Judge, Session/Trace/Tool_call three-level | OTA quality gate, agent accuracy monitoring |
| 10 | Registry | Agent/MCP/tool centralized catalog, hybrid search, governed publishing | Enterprise agent directory, approved tool catalog |

## Differentiators vs Competition
- Only platform with Browser sandbox + Cedar Policy + Enterprise Identity + 3-level Eval + Registry
- Framework-agnostic (Strands/LangGraph/CrewAI/OpenClaw/custom)
- Model-agnostic (Claude/Nova/GPT/Gemini/Llama/DeepSeek)
- Protocol-native (MCP/A2A/AG-UI)
- 9 AWS Regions globally
