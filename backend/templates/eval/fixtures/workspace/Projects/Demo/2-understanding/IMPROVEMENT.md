# Demo — Improvement

## What Worked

- [guideline] **Small pull requests reviewed the same day** kept the main branch green. (2026-04-02)

## What Failed

- [pitfall] **Tests that mocked the clock passed while real time-zone handling was broken** — the bug shipped twice; tests now run against fixed real dates in three time zones. (2026-05-14)
- [pitfall] **A release went out on a Friday evening** and nobody was around to roll back. (2026-06-20)
- [pitfall] **Two people edited the same config file in parallel** and one change was lost. (2026-07-08)
