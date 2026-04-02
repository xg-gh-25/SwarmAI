"""Tests for jobs.estimation_learner — EMA-based job duration prediction.

Verifies:
- CategoryModel EMA update formula
- Prediction gated by min_samples
- Persistence to JSON file
- Confidence scoring
- EstimationLearner integration
"""

import json
import pytest
from pathlib import Path


def test_category_model_default_prediction():
    """Before min_samples, predict returns base estimate unchanged."""
    from jobs.estimation_learner import CategoryModel

    model = CategoryModel()
    assert model.predict(300.0) == 300.0


def test_category_model_prediction_after_samples():
    """After min_samples recordings, prediction adjusts by EMA factor."""
    from jobs.estimation_learner import CategoryModel

    model = CategoryModel()
    # Record 5 samples where actual is 2x predicted
    for _ in range(5):
        model.update(predicted=100.0, actual=200.0)

    # Factor should have moved toward 2.0
    result = model.predict(100.0)
    assert result > 100.0, "Prediction should be adjusted upward"
    assert result < 250.0, "Prediction shouldn't overshoot"


def test_category_model_ema_convergence():
    """EMA converges toward actual/predicted ratio over many samples."""
    from jobs.estimation_learner import CategoryModel

    model = CategoryModel()
    # Record 50 samples where actual is always 1.5x predicted
    for _ in range(50):
        model.update(predicted=100.0, actual=150.0)

    # Factor should be very close to 1.5
    result = model.predict(100.0)
    assert abs(result - 150.0) < 5.0, f"Expected ~150, got {result}"


def test_category_model_confidence_increases_with_samples():
    """Confidence grows with sample count."""
    from jobs.estimation_learner import CategoryModel

    model = CategoryModel()
    conf_0 = model.confidence
    for _ in range(10):
        model.update(predicted=100.0, actual=100.0)
    conf_10 = model.confidence

    assert conf_10 > conf_0


def test_category_model_confidence_drops_with_errors():
    """Confidence decreases when predictions are consistently wrong."""
    from jobs.estimation_learner import CategoryModel

    good = CategoryModel()
    bad = CategoryModel()
    for _ in range(20):
        good.update(predicted=100.0, actual=100.0)  # perfect
        bad.update(predicted=100.0, actual=300.0)  # 3x off

    assert good.confidence > bad.confidence


def test_category_model_zero_predicted_is_safe():
    """update() handles predicted=0 without division error."""
    from jobs.estimation_learner import CategoryModel

    model = CategoryModel()
    model.update(predicted=0.0, actual=100.0)  # should not crash
    assert model.samples == 0  # should skip the update


def test_learner_predict_and_record(tmp_path: Path):
    """EstimationLearner round-trip: record then predict."""
    from jobs.estimation_learner import EstimationLearner

    path = tmp_path / "learner.json"
    learner = EstimationLearner(path)

    # Before any data, returns base
    assert learner.predict("morning-inbox", 300.0) == 300.0

    # Record enough samples
    for _ in range(6):
        learner.record("morning-inbox", predicted=300.0, actual=150.0)

    # Now prediction should be adjusted downward
    result = learner.predict("morning-inbox", 300.0)
    assert result < 300.0


def test_learner_persistence(tmp_path: Path):
    """Learner state survives save/load cycle."""
    from jobs.estimation_learner import EstimationLearner

    path = tmp_path / "learner.json"
    learner1 = EstimationLearner(path)
    for _ in range(6):
        learner1.record("self-tune", predicted=200.0, actual=100.0)

    # New learner from same file
    learner2 = EstimationLearner(path)
    result = learner2.predict("self-tune", 200.0)
    assert result < 200.0, "Loaded learner should have adjusted factor"


def test_learner_separate_categories(tmp_path: Path):
    """Different categories have independent models."""
    from jobs.estimation_learner import EstimationLearner

    path = tmp_path / "learner.json"
    learner = EstimationLearner(path)

    for _ in range(6):
        learner.record("fast-job", predicted=100.0, actual=50.0)
        learner.record("slow-job", predicted=100.0, actual=300.0)

    fast = learner.predict("fast-job", 100.0)
    slow = learner.predict("slow-job", 100.0)
    assert fast < slow, "Different categories should diverge"


def test_learner_file_not_found(tmp_path: Path):
    """Learner initializes cleanly when file doesn't exist."""
    from jobs.estimation_learner import EstimationLearner

    path = tmp_path / "nonexistent" / "learner.json"
    learner = EstimationLearner(path)
    assert learner.predict("anything", 42.0) == 42.0


def test_learner_corrupted_file(tmp_path: Path):
    """Learner handles corrupted JSON gracefully."""
    from jobs.estimation_learner import EstimationLearner

    path = tmp_path / "learner.json"
    path.write_text("not json {{{")
    learner = EstimationLearner(path)
    assert learner.predict("anything", 42.0) == 42.0
