# Upstream Source Attribution

This directory contains vendored source files from two upstream projects,
fetched at pinned commits for audit and provenance tracking.

## KiroCrew (kirodotdev/KiroCrew)

- **Repository**: https://github.com/kirodotdev/KiroCrew
- **Commit**: 27cb0849eddd693663b3c42c865fb76d7ba5c5d7
- **License**: Apache-2.0 (see kirocrew/LICENSE)
- **Paths vendored**:
  - `src/kiro_crew/apps/builtins/pptx_maker/` — app definition, agents, backend, prompts
  - `src/kiro_crew/builtin_skills/pptx-maker/` — builtin skill
  - `website/src/apps/pptx-maker/` — React UI components
  - `website/public/app-assets/pptx-maker/` — visual assets

## spec-driven-presentation-maker (aws-samples)

- **Repository**: https://github.com/aws-samples/sample-spec-driven-presentation-maker
- **Commit**: b7c7fcf0972b33a480f1b717a3de5bc288d53742
- **License**: MIT-0 (see `../engine/LICENSE.upstream`)
- **Archive SHA-256**: ffe8e10b973cada1f6e99629ac780bde25461b432ad07943b4f15ca11c07dfe0
- **Status**: Descriptor only (engine tar not vendored; fetched at runtime)

---

All file hashes are recorded in `../PROVENANCE.json`.
