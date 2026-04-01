# SwarmAI Backend — Test & Dev Shortcuts
#
# Usage:
#   make test          → fast tests only (skip PBT+slow)          (~10s)
#   make test-all      → full suite                                (~40s)
#   make test-pbt      → property-based tests only                 (~25s)
#   make test-lf       → re-run last failures only                 (<5s)
#   make test-file F=  → run a single test file                    (<5s)
#   make test-grep K=  → run tests matching keyword (-k)           (varies)
#   make test-ci       → CI mode: full suite, 100 Hypothesis examples
#   make dev           → start backend in dev mode (port 8000)
#
# xdist is controlled ONLY by pyproject.toml addopts (-n 4).
# conftest.py enforces memory-adaptive clamping — never bypassed.
# There is no N= override. One way to run tests. One source of truth.

SHELL := /bin/bash
PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest

# Hypothesis profile
PROFILE ?= default

# ── Test targets ─────────────────────────────────────────────

.PHONY: test test-all test-pbt test-lf test-file test-grep test-ci dev help

## Fast tests: skip PBT and slow (unit tests only, ~10s)
test:
	$(PYTEST) -m "not pbt and not slow"

## Full suite: all ~2000 tests (~40s with 4 workers)
test-all:
	HYPOTHESIS_PROFILE=$(PROFILE) $(PYTEST)

## Property-based tests only (~25s)
test-pbt:
	HYPOTHESIS_PROFILE=$(PROFILE) $(PYTEST) -m pbt

## Re-run only last failures (fastest iteration loop)
test-lf:
	$(PYTEST) --lf

## Single file: make test-file F=tests/test_foo.py
test-file:
	$(PYTEST) $(F)

## Keyword filter: make test-grep K=session_unit
test-grep:
	$(PYTEST) -k "$(K)"

## CI mode: full suite with 100 Hypothesis examples per property
test-ci:
	HYPOTHESIS_PROFILE=ci $(PYTEST) --tb=long

## Start backend in dev mode
dev:
	$(PYTHON) -m uvicorn main:app --reload --port 8000

## Show this help
help:
	@echo "SwarmAI Backend — Test & Dev Shortcuts"
	@echo ""
	@echo "  make test          Fast tests (skip PBT+slow)"
	@echo "  make test-all      Full suite"
	@echo "  make test-pbt      Property-based tests only"
	@echo "  make test-lf       Re-run last failures"
	@echo "  make test-file F=  Single file"
	@echo "  make test-grep K=  Keyword filter"
	@echo "  make test-ci       CI mode (100 examples)"
	@echo "  make dev           Dev server (port 8000)"
