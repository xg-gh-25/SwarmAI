# Frontend Design upstream adaptation

Source: `anthropics/skills@dbd4588f9e1033efb41dad4bef2f7947c8993d44`

The original `frontend-design` Skill is retained verbatim in `upstream/SKILL.md`.
SwarmAI already had a richer `s_frontend-design` Skill with a design database,
scripts and product-specific output conventions, so this package does not register
a duplicate Skill name.

The existing SwarmAI Skill incorporates the upstream emphasis on deliberate,
subject-specific typography, color, motion and composition. Repository instructions
and established design systems remain authoritative. In the SwarmAI repository this
means CSS variables, the fixed type scale, shared overlay components, accessibility
roles and visual-system guards override any general aesthetic suggestion.

Modified SwarmAI file:

- `backend/skills/s_frontend-design/INSTRUCTIONS.md`

The modification is identified in that file and retains this source and license
reference. No upstream executable code, network client or dependency is included.
