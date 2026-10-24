---
name: pollinate
description: "Turn one message into a media package (poster, video, narrative, shorts, README) through a 9-stage pipeline. TRIGGER: \"pollinate\", \"make content about\", \"GTM for\", \"promote\". NOT FOR: code changes or research."
tier: lazy
python-deps: [boto3, defusedxml, openpyxl, pptx, yaml]
python-optional-deps: [edge_tts, playwright]
---

# Pollinate -- Swarm Media Value Delivery Engine

> Your message, their attention, the right format.

Read INSTRUCTIONS.md before proceeding.

## Quick Start

Tell your agent: **"Pollinate: [topic]"** or **"Make a video about [topic]"**

## Verification

Before marking a Pollinate run complete, show evidence for each:

- [ ] **REPORT.md generated** -- saved to `content/{name}/REPORT.md`
- [ ] **All 9 stages executed** -- EVALUATE through REFLECT
- [ ] **Confidence score calculated** -- breakdown shown
- [ ] **Decision log complete** -- every decision classified (mechanical/taste/judgment)
- [ ] **Studio preview reviewed** -- user approved before 4K render
- [ ] **Platform specs validated** -- `check_specs.py` passed for all target platforms
