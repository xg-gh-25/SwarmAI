# THINK (research)

A research run answers a question; it lands no code. THINK is where it is answered: it ends with the report the requirement asks for, then DELIVER (reflect, `stage-complete`).

Read `artifacts/knowledge_design.md` first: what the project's Brain and the repo's lessons already know about the question. Start from that, not from zero, and say where the report agrees with it or overrides it.

## Splitting the question (a Swarm Run, optional)

When the question has parts that stand on their own (sources to survey, options to compare, cases to check) and each is real work, you may hand them to the person's Hives as children of this run, each coming back as a document. Everything about them is yours to decide: whether to split, which parts, whether a result is good enough, whether to correct it, do it yourself or drop it. The person judges the finished report, not the children.

1. Split into sub-questions that stand alone: each task text complete (nothing from this conversation, no path on this machine), with its acceptance (what a good answer holds: sources, the comparison asked, what is out of scope) and `"returns": "document"`. A part that needs another's answer waits for it (`after`); a part that is small, or needs this conversation, you do yourself.
2. Write the plan and dispatch what is ready: `"$SWARM_BACKEND_BIN" pipeline-v1 child plan --project <P> --run-id <R> --file -` with `{"children": [{"wp", "title", "task", "acceptance", "execution", "returns", "why", "after"}]}`, then `"$SWARM_BACKEND_BIN" pipeline-v1 child dispatch --project <P> --run-id <R> Q1 Q2`. Work on your own parts meanwhile.
3. Wait: end your turn; the run is woken when a child changes. `child status` shows them; a child's wait you answer with `child card` then `child answer`, or `child escalate` only when it is the person's to decide.
4. Read each document against its acceptance. Take it (`child accept <dispatch> --ended-at <its result's ended_at> --why ...`), correct it (`child rerun <dispatch> --text-file -`), do that part yourself, or `child discard` it. One part goes out at most three times.

## The report

Synthesize: answer the question asked, with what each part contributed. Every claim of fact carries its source (a link, a file and line, a command and its output); a claim you could not source is marked as unverified. Name what was not answered and why (a part dropped, a source not reachable). When the stakes need it, have the report's claims checked against their sources by a review expert (`s_expert-review`); its findings are for you to verify and fix, they do not decide.

If evidence cannot settle a question whose answer is irreversible or high risk, submit one DC-1 decision with 2–3 options and consequences. Mechanical failures never become questions.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R>` and continue.
