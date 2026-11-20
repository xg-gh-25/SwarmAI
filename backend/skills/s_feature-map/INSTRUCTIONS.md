# Feature Map — instructions

A Feature Map is what a product does for its users, written so a program can prove each
feature still works. You write the knowledge (`features.yaml`) and the walks (standard
Playwright specs); the product replays them in a sandbox it builds, with no model, and
records every result itself. Use it to check your own user-visible change, to reproduce a
report, and in a pipeline run's TEST.

## Where the files are

Run `"$SWARM_BACKEND_BIN" py -m scripts.feature_map where` in the checkout. It prints the
feature folder and records its place on the Brain's binding the first time: the repo's
`.swarmai/features/` for a working checkout (commit the files with the change, like
tests), the Brain's `.artifacts/feature-map/<repo>/` for a managed mirror or when the
person asks to keep it out of the repo (`--keep-out-of-repo`).

```
features.yaml          # the feature knowledge
targets/<id>.yaml      # how to start each runnable part of the product
specs/*.spec.ts        # Playwright tests, one file per feature
fixtures/              # stub bodies and start-state data
```

## What a feature is

Something a user does and can see the result of, reached from a start state, small enough
that one walk shows it works. Write the product's main paths first (a handful per target,
never every button). Each entry:

```yaml
schema: 1
features:
  - id: pipeline-overlay-k3f9        # slug + 4 random characters, assigned once, never renamed
    title: Open the Pipeline view from the left nav
    reach: Click Pipeline in the left nav       # how a user gets there, in words
    expect: The Pipeline overlay opens with its mode badge
    target: web
    spec: specs/pipeline-overlay-k3f9.spec.ts
  - id: api-health-7q2m
    title: The API answers health checks
    target: api
    run: [curl, -sf, "http://127.0.0.1:{port.api}/health"]   # a command walk: exit 0 = pass
```

## Targets

```yaml
id: web
kind: web                         # web (Playwright specs) | command (command walks only)
root: desktop/                    # a change under here reaches this target
paths: [shared/ui/]               # more paths that reach it (optional)
needs: [api]                      # targets started first (optional)
prepare: [[npm, ci], [npm, run, build]]          # run to completion, in a copy of the checkout
services:
  - {id: ui, argv: [npx, vite, preview, --port, "{port.ui}", --strictPort], ready: "http://127.0.0.1:{port.ui}/"}
base_url: "http://127.0.0.1:{port.ui}/"
env: {VITE_API_URL: "http://127.0.0.1:{port.api}"}
start_states:
  home: {setup: [[curl, -sf, -X, PUT, "http://127.0.0.1:{port.api}/api/system/onboarding-complete"]]}
```

Every command is an argv list, never a shell string. Placeholders: `{port.<service>}`,
`{sandbox}`, `{checkout}`, `{state_dir}` (the current start state's own folder,
`<sandbox>/state/<state>`; it expands in service argv, `env`, `ready` URLs and setup).

### Start states

Each start state starts from fresh services: before it, the product stops every running
service, gives the state an empty `{state_dir}`, starts the target and the targets it
needs again (on the same ports), and then runs the state's `setup`. Keep the app's data
under `{state_dir}` (`env: {DATA_DIR: "{state_dir}/data"}`), so no state sees what
another state's setup changed; what the app keeps elsewhere (the sandbox `HOME`, the
checkout copy) is shared by the states. `prepare` runs once per target. `default` runs
first, then the other states by name; command walks run in `default`.

## Writing a spec

```ts
import { test, expect } from '@playwright/test';

test('from home', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-pipeline').click();
  const badge = page.getByTestId('overlay-host-mode-badge');
  await expect(badge).toBeVisible();
  await expect(badge).toBeInViewport({ ratio: 1 });
});
```

- **Start state**: the tag `@start:<name>` (none = `default`); the walk starts from that
  state's fresh services (see Start states above), never from what an earlier state left.
- **Stable handles**: `getByRole` + name where the label is product copy, `getByTestId`
  where it is not. Never a CSS path or an index.
- **In-viewport rule**: every walk that opens a dialog, popover or menu asserts
  `toBeInViewport({ ratio: 1 })` on it. Specs run in two viewports, `regular` 1440×900
  and `compact` 1024×640, so something that only fits a large window fails in `compact`.
- **No fixed sleeps**: wait on elements (`expect(...).toBeVisible()`), never
  `waitForTimeout`.
- **No network**: a page reaches only the sandbox (loopback). A response the page needs
  from elsewhere is a stub you write: `page.route('**/api/x', r => r.fulfill({ path: 'fixtures/x.json' }))`.
  Never capture a stub from a live call.
- Relative URLs only (`page.goto('/')`); the product sets `baseURL`.

## Checking

```
"$SWARM_BACKEND_BIN" py -m scripts.feature_map verify <feature>      # before committing a new or edited walk
"$SWARM_BACKEND_BIN" py -m scripts.feature_map affected              # which features your change reaches
"$SWARM_BACKEND_BIN" py -m scripts.feature_map replay --affected     # replay them
"$SWARM_BACKEND_BIN" py -m scripts.feature_map status [<feature>]    # last result, at which commit, commits since
```

Exit codes: 0 all passed; 1 a walk failed; 3 none failed but one was flaky (failed, then
passed on the retry — reported as flaky, never as a pass); 2 nothing could be judged (a
bad file, the runtime unavailable, the target could not start): the reason is in the
output; it is not a failure of the product.

Commit a new or edited walk only after `verify` is green. The first replay installs the
pinned runtime (Node, Playwright, Chromium) into the app data folder; that takes a few
minutes once.

In a pipeline run, TEST's `next` names the command: run it in the run's worktree with
`--run <run id>`. The run's TEST check reads what that command recorded, not your report
row: a failed reached walk keeps TEST red (unless it also fails at the run's base, which
the command checks itself); a reached feature not replayed on the final tree keeps TEST
red too, so replay again after the last edit. A walk that could not run for an
environment reason (`not_run` with its reason) is listed, never blocking. The REPORT and
the run card show the results, the screenshots, and every expected result the run changed.

## When a walk fails

First decide whether the product is broken or the walk is outdated, and say which.

- The change meant it (a label renamed on purpose): update the walk in the same commit;
  the changed expectation is listed in the REPORT for the person.
- The product is broken: fix the product, replay, report.
- Most walks fail at their first step: the target did not come up (look at the not_run
  reason and the service log), not N regressions.
- A walk that passes on one machine and fails on another depends on that machine (a tool
  the app detects, the person's files): take the app's own way to the state the walk checks
  (a switch, a setup step), so it gives the same result everywhere.

Never edit an expectation just to make a walk pass. Flaky walks are quarantined after 3
flakes in their last 10 results and come back after 5 passes in a row (a quarantined walk
is still replayed, never blocks): fix the walk (a missing wait), do not retry it green.

## What the sandbox does and does not contain

It keeps your credentials (environment, HOME, AWS files) and the person's working tree
away from the replayed app, and keeps the browser off the network. It does not contain the
app's own server-side network calls or the repo's own files (a repo-local `.env` is part of
the copy): replaying is as trusted as running the repo's tests. Replay runs only when you or
the person ask for it.

On a machine with an instance metadata service (a Hive, any cloud machine) the service
would hand the instance's credentials to the replayed app, so every process of the replay
runs as a separate replay user the host's firewall keeps off it, one replay at a time.
Where that user is not set up (a Hive before its next update), the walks are `not_run:
runtime unavailable` with the reason, never run without it.

Not covered by a walk: native windows, menus and dialogs of desktop apps, mobile apps,
colour and contrast, timing races, live third-party services.
