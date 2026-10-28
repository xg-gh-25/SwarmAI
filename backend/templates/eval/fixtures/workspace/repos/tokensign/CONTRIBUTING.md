# Contributing

- Run the tests with `make test` (it runs `pytest -q`).
- Lint with `make lint` (`ruff check .`).
- A change to the token format needs a test in `tests/test_compat.py` that verifies a
  token produced by the previous release, so old tokens keep working.
