# SwarmAI Backend — Test & Dev Shortcuts
#
# Usage:
#   make test          → fast tests only (skip PBT+slow), 4 workers  (~10s)
#   make test-all      → full suite, 4 workers                       (~40s)
#   make test-pbt      → property-based tests only                   (~25s)
#   make test-lf       → re-run last failures only                   (<5s)
#   make test-file F=  → run a single test file                      (<5s)
#   make test-grep K=  → run tests matching keyword (-k)             (varies)
#   make test-ci       → CI mode: full suite, 100 Hypothesis examples
#   make lint          → type-check + import sort check
#   make dev           → start backend in dev mode (port 8000)
#
# All test targets use pytest-xdist (-n 4) by default.
# Override workers: make test N=2
# Run single-process: make test N=0

SHELL := /bin/bash
PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest

# Parallel workers (0 = disable xdist)
N ?= 4

# Hypothesis profile
PROFILE ?= default

# Common pytest flags
_XDIST := $(if $(filter 0,$(N)),,-n $(N))
_COMMON := $(_XDIST) --tb=short -q

# ── Test targets ─────────────────────────────────────────────

.PHONY: test test-all test-pbt test-lf test-file test-grep test-ci lint dev help

## Fast tests: skip PBT and slow (unit tests only, ~10s)
test:
	$(PYTEST) $(_COMMON) -m "not pbt and not slow"

## Full suite: all 1800+ tests (~40s with 4 workers)
test-all:
	HYPOTHESIS_PROFILE=$(PROFILE) $(PYTEST) $(_COMMON)

## Property-based tests only (~25s)
test-pbt:
	HYPOTHESIS_PROFILE=$(PROFILE) $(PYTEST) $(_COMMON) -m pbt

## Re-run only last failures (fastest iteration loop)
test-lf:
	$(PYTEST) $(_COMMON) --lf

## Single file: make test-file F=tests/test_foo.py
test-file:
	$(PYTEST) $(_COMMON) $(F)

## Keyword filter: make test-grep K=session_unit
test-grep:
	$(PYTEST) $(_COMMON) -k "$(K)"

## CI mode: full suite with 100 Hypothesis examples per property
test-ci:
	HYPOTHESIS_PROFILE=ci $(PYTEST) $(_COMMON) --tb=long

## Type-check (requires pyright/mypy in venv)
lint:
	@if command -v $(PYTHON) -m pyright &>/dev/null; then \
		$(PYTHON) -m pyright .; \
	else \
		echo "pyright not installed, skipping type check"; \
	fi

## Start backend in dev mode
dev:
	$(PYTHON) -m uvicorn main:app --reload --port 8000

## Show this help
help:
	@echo "SwarmAI Backend — Test & Dev Shortcuts"
	@echo ""
	@echo "  make test          Fast tests (skip PBT+slow), ~10s"
	@echo "  make test-all      Full suite, ~40s"
	@echo "  make test-pbt      Property-based tests only"
	@echo "  make test-lf       Re-run last failures"
	@echo "  make test-file F=  Single file"
	@echo "  make test-grep K=  Keyword filter"
	@echo "  make test-ci       CI mode (100 examples)"
	@echo "  make lint          Type-check"
	@echo "  make dev           Dev server (port 8000)"
	@echo ""
	@echo "  Override workers:  make test N=2"
	@echo "  Single-process:    make test N=0"
