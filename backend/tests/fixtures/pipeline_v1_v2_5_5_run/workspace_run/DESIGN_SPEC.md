# Design Spec

## Chosen approach

swap

## Design

**Workflow:** call add

**Architecture:** module

**Flow:** call -> validate; invalid -> error; valid -> sum

## Properties

- **P1** — sum is returned

## Failure modes

- **F1** — bad input

## Tasks

- **T1** — swap the operands

## Exit criteria

- **X1** — backend/tests/test_x.py::test_add
- **X2** — backend/tests/test_x.py::test_add

## Consumers

- {"contract": "add", "id": "K1", "paths": ["backend/core/mod.py"], "pattern": "add", "readers": ["backend/tests/test_x.py"]}

<!-- rendered from design_spec.json sha256:e1698eb82ec0eee44e545258d51c386f7c005508b3b54dca2ce4320c95906d63 -->
