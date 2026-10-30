---
title: "Could SwarmAI runs become shareable, cinematic run stories?"
created: 2026-07-30
updated: 2026-07-30
status: published
---
<!-- GitHub Discussion #104: https://github.com/xg-gh-25/SwarmAI/discussions/104 -->

SwarmAI's autonomous coding pipeline and Pollinate content loop look unusually well suited to showing *how* an agent reached a result, not just the final output.

I built a small GitHub Action, **Publish Agent Story**, that turns Codex/Claude/OpenClaw/JSONL/log runs into a redacted, interactive SunfishLoop Story. It uses GitHub OIDC, so there is no API secret to configure:

https://github.com/marketplace/actions/publish-agent-story

Would a visual run record be useful for SwarmAI demos, evaluations, or release notes? If the maintainers are interested, I can prepare a minimal opt-in workflow PR against a sample run. No obligation; concrete feedback on the format would be valuable too.
