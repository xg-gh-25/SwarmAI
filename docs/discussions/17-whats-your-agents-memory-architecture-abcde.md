# What's your agent's memory architecture? (A/B/C/D/E)

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/17) | Category: Q&A | Published: 2026-05-18

---

Quick question for anyone running an AI agent/assistant more than a few sessions:

## How does your agent remember things between sessions?

Curious what the actual distribution looks like in practice (not what the docs say — what you actually shipped):

**A) Single file** (CLAUDE.md, .cursorrules, system prompt doc)
- One big file, manually maintained
- Works until it doesn't (~month 3 it's stale or bloated)

**B) RAG / vector store**
- Embeddings + retrieval
- Good recall, bad precision (retrieves "related" but not "needed")

**C) Structured multi-file** (multiple context files with ownership/priority)
- Different files for different purposes
- Maintenance split between human and AI

**D) No persistence**
- Each session starts fresh
- Context via paste/attach/manual

**E) Something else**
- Describe below 👇

## Follow-up questions (if you want to share more)

1. How many sessions before it "feels like it knows you"?
2. What's the #1 thing your agent forgets that it shouldn't?
3. Have you ever switched tools and lost accumulated context? How painful was it (1-10)?

---

*No wrong answers. Genuinely trying to map what's actually deployed vs what's marketed.*

