# Show your setup: what does your agent's context look like on disk?

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/18) | Category: Show and tell | Published: 2026-05-18

---

## Share your agent's context file

Everyone's setup is different. Here's the question:

**What does your AI agent's "brain" look like on disk?**

Post a screenshot, a tree output, or just describe:
- How many files?
- Who maintains them (you manually? AI auto-updates? both?)
- What goes stale fastest?
- What's the one file you'd save if you could only keep one?

## To start — here's ours

```
.context/           (11 files, 3 ownership tiers)
├── SWARMAI.md      ← system-owned, overwritten on startup
├── IDENTITY.md     ← system-owned
├── SOUL.md         ← system-owned (personality)
├── AGENT.md        ← system-owned (behavioral rules, ~15K tokens)
├── USER.md         ← user-owned (my preferences, background)
├── STEERING.md     ← user-owned (session overrides, standing rules)
├── TOOLS.md        ← user-owned (environment config)
├── MEMORY.md       ← agent-owned 🔒 (AI maintains exclusively)
├── EVOLUTION.md    ← agent-owned 🔒 (corrections, capabilities)
├── KNOWLEDGE.md    ← auto-generated (index of 200+ knowledge files)
└── PROJECTS.md     ← auto-generated (active projects scan)
```

Key design choice: **3 ownership tiers prevent the "who updates what" problem.**
- System files = code-controlled (always correct, can't drift)
- User files = human-controlled (preferences, won't be overwritten)
- Agent files = AI-exclusive (human directs "remember X", AI decides structure)

Total: ~39K tokens loaded per session. Budget: 91K. Never truncates in practice.

The file I'd save if I could only keep one: **MEMORY.md** — 67 days of curated decisions, lessons, and corrections. Everything else can be regenerated.

## Your turn

Even a one-line answer is interesting:
- "Just CLAUDE.md, 200 lines, I rewrite it monthly"
- "RAG over my docs folder, no explicit context file"
- "Nothing — I paste context every session"

All valid. Curious what's actually working for people.

