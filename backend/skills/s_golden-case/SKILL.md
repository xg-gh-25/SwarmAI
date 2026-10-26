---
name: golden-case
description: "Add, validate, retire or propose eval golden cases: your own cases in the workspace, or a product case shipped with SwarmAI. TRIGGER: \"add golden case\", \"new eval case\", \"retire a case\", \"add a product eval probe\". NOT FOR: running eval."
tier: lazy
platform: desktop
---

# Golden Case

The standard intake for eval golden cases. Read INSTRUCTIONS.md before proceeding.

There are two sets. The product set ships with SwarmAI, is the same on every install
and is read-only at runtime: changing it is a code change. Your own cases live in
your workspace. Every case says in plain words what it protects (`purpose`), and a
quick-check case must prove it can fail. Nothing creates cases automatically.
