---
inclusion: always
---

# SwarmAI Project Steering Rules

## Rule 1: Ignore Amazon Internal Steering
This project is NOT part of the Amazon internal stack. Do NOT apply rules from `amazon-builder-steering.md` or any Amazon-specific tooling guidance (Brazil, CRUX, Apollo, Pipelines, Taskei, etc.). This is an independent open-source project with its own build system and workflows.

## Rule 2: Spec Mode Prompt Clarification Process
In "Spec" mode, use this prompt template to clarify user input and requirements for effective spec generation. ALWAYS follow these rules:

1. Your first response will be to ask me what the prompt should be about. I will provide my answer, but we will need to improve it through continual iterations by going through the next steps.

2. Based on my input, you will generate 2 sections:
   a) Revised prompt (provide your rewritten prompt, it should be clear, concise, and easily understood by you)
   b) Questions (ask any relevant questions pertaining to what additional information is needed from me to improve the prompt)

3. We will continue this iterative process with me providing additional information to you and you updating the prompt in the Revised prompt section until I say we are done.
