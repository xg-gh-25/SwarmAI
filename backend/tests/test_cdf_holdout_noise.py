"""CDF §14.1 T5: holdout sizing under noisy majority labels (scripts/ingestion_holdout_size.py).

What is tested (numbers are exact-binomial outputs of the script, pinned here):
- ``label_error=0`` reproduces the preregistered table (write precision power 0.8177).
- At 4.2% label error, write precision at n=210 and true 97% has power 0.041: the 93%
  bound cannot be shown, and no true rate up to 100% reaches 80% power at n=210.
- The largest label error n=210 tolerates for write precision is 0 on the 0.001 grid.
- A rate the noise pushes to or below the threshold has no sample size (raises).
- ``observed_rate`` matches p(1-e) + (1-p)e and rejects e >= 0.5.
"""
from __future__ import annotations

import pytest

from scripts import ingestion_holdout_size as h


def test_noise_free_matches_the_preregistered_table() -> None:
    assert round(h.power(210, 0.97, 0.93), 4) == 0.8177
    assert round(h.power(210, 0.97, 0.93, label_error=0.0), 4) == 0.8177


def test_write_precision_is_not_provable_at_4_2_percent_noise() -> None:
    assert round(h.observed_rate(0.97, 0.042), 4) == 0.9305
    assert round(h.power(210, 0.97, 0.93, label_error=0.042), 4) == 0.0409
    assert h.required_true_rate(210, 0.93, 0.042) is None
    assert h.max_label_error(210, 0.97, 0.93) == 0.0


def test_no_sample_size_when_noise_drags_the_rate_below_the_bar() -> None:
    with pytest.raises(ValueError):
        h.minimal_n(0.96, 0.93, label_error=0.042)


def test_observed_rate_formula_and_bounds() -> None:
    assert h.observed_rate(0.9, 0.1) == pytest.approx(0.9 * 0.9 + 0.1 * 0.1)
    assert h.observed_rate(1.0, 0.0) == 1.0
    with pytest.raises(ValueError):
        h.observed_rate(0.9, 0.5)


def test_holdout_output_is_pinned():
    assert h.render_table() == """| metric | n | k | assumed true rate | threshold | label error | lower 95% | power | minimal n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| write precision | 220 | 202 | 0.97 | 0.88 | 0.042 | 0.8811 | 0.8061 | 218 |
| write recall | 180 | 145 | 0.83 | 0.75 | 0.000 | 0.7506 | 0.8350 | 167 |
| DDD routing | 150 | 129 | 0.88 | 0.80 | 0.000 | 0.8047 | 0.8126 | 135 |
| D1 missing why | 130 | 123 | 0.96 | 0.90 | 0.000 | 0.9012 | 0.8491 | 116 |
| duplicate unchanged | 90 | 86 | 0.97 | 0.90 | 0.000 | 0.9012 | 0.8659 | 76 |"""
