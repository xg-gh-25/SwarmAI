# Card: brief

The brief is the front end. It exists so the first version is right, not to collect a form.
Skip it on the quick path (one poster or one post) and in the revise loop.

## 1. Look up what is already known

If the session has a project, search its knowledge with the words of the content before
you write anything:

```
"$SWARM_BACKEND_BIN" cognition find --project <P> --query "<topic words>"
```

Facts, numbers, names and earlier decisions come from there and from the sources the
person gave, never from memory. With no project, skip this step.

## 2. Answer six questions

| # | Question | Example answer |
|---|---|---|
| 1 | **Who** sees it? | leadership, customers, the team, developers, social followers |
| 2 | **What should happen** after they see it? | they agree, they act, they learn, they know it exists |
| 3 | **The thesis**: the one sentence they must remember | "Teams win by compounding what they learn, not by buying more tools" |
| 4 | **Where and how** they see it | a meeting screen, email, 小红书, a printed A4 |
| 5 | **Which formats** (this is the scope) | a deck; a deck and a 小红书 post |
| 6 | **Sources and constraints** | a reference deck to match, a .pptx template, a brand, a length, a date |

Answer each one yourself from the message, the project knowledge and the sources.
Ask the person **once**, in one message, and only the questions you truly cannot answer
(usually the thesis or the formats). Decide the rest, and write down what you decided.

The confirmed formats are the scope. Do not add a format the person did not ask for.

## 3. Write `brief.md`

Write the answers to `<work>/brief.md` (next to `work.json`, not in `src/`), in a few lines:
the six answers, the sources (paths or links), and "decided for you: …". Read it again
before each version.

## 4. Pick the look

- deck: pick a design system (kinds/deck.md § Pick a design system).
- poster: pick two directions (kinds/poster.md).
- page, copy: see their kind pages.

Then `work.py init` and write the source.
