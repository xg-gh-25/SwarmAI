# TEST ref — Single-Platform Compile Trap

Read when the changeset is cross-platform (trigger below).

## Single-Platform Compile Trap (BLOCKING for cross-platform changes) `[MUST]` <!-- R-test-005 -->

**Trigger:** the changeset adds a `#[cfg(...)]` / `#ifdef` / platform-conditional
symbol, OR touches code that compiles per target (Rust, C/C++, Go build tags,
platform-specific imports).

**The trap:** a compile check on ONE platform (`cargo check` on the dev Mac) passes
while another target FAILS. Example: a `#[cfg(target_os="macos")]` function referenced
by an un-gated caller breaks the Windows build (E0425). The green local check hides it
(RP40).

**Rule:** a single-platform compile success may NOT be reported as a fully-green TEST
result for a cross-platform changeset. Either:

1. Run the cross-target check locally if the toolchain exists (`cargo check --target
   <other>`). In a run, invoke it through the runner:
   `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode probe --name cargo-check-<target> -- <wrapper> --target <other>`
   where `<wrapper>` runs `cargo check --target <other>` and prints
   `{"findings": [...]}` with one finding (stable id = error code + path) per compile
   error. For a check that needs more than ~95 s, add `--deadline <s>` and raise the
   alarm to deadline + 10. OR
2. Mark the result explicitly: `"platform_verified": ["macos"],
   "platform_pending_ci": ["windows", "linux"]` and surface it as a known gap in
   DELIVER. The CI matrix is the definitive check, not the local build.

Never write `all_pass: true` / "compiles" unqualified when only one of N targets was
actually checked. Record which targets were checked in `exit_evidence.platforms`.
