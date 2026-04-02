"""EMA-based job execution time estimation learner.

Tracks per-category (job type) execution duration using exponential
moving average.  After ``MIN_SAMPLES`` observations, predictions adjust
toward the observed actual/predicted ratio.

Inspired by IronClaw's ``EstimationLearner`` — simple, effective, no
external dependencies.

Key public symbols:

- ``CategoryModel``      — Single category's EMA state.
- ``EstimationLearner``  — Multi-category learner with JSON persistence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger("swarm.jobs.estimation_learner")

# EMA smoothing factor — 0.1 gives ~90% weight to history, 10% to new.
ALPHA = 0.1

# Don't adjust predictions until we have enough observations.
MIN_SAMPLES = 5


@dataclass
class CategoryModel:
    """EMA model for a single job category (e.g. 'morning-inbox')."""

    time_factor: float = 1.0
    samples: int = 0
    error_rate: float = 0.0

    def update(self, predicted: float, actual: float) -> None:
        """Record one observation and update the EMA factor."""
        if predicted <= 0:
            return
        ratio = actual / predicted
        self.time_factor = (1 - ALPHA) * self.time_factor + ALPHA * ratio
        self.samples += 1
        # Track prediction error for confidence scoring
        adjusted = predicted * self.time_factor
        error = abs(actual - adjusted) / max(actual, 1.0)
        self.error_rate = (1 - ALPHA) * self.error_rate + ALPHA * error

    def predict(self, base_estimate: float) -> float:
        """Return adjusted estimate, or *base_estimate* if too few samples."""
        if self.samples < MIN_SAMPLES:
            return base_estimate
        return base_estimate * self.time_factor

    @property
    def confidence(self) -> float:
        """Confidence score 0.0–1.0 based on sample count and error rate."""
        sample_conf = min(self.samples / 20.0, 1.0)
        error_conf = 1.0 - min(self.error_rate, 0.5)
        return sample_conf * error_conf


class EstimationLearner:
    """Multi-category learner with JSON file persistence.

    Each job type (``morning-inbox``, ``self-tune``, etc.) gets its own
    :class:`CategoryModel`.  State is saved after every :meth:`record`
    call to ``~/.swarm-ai/estimation_learner.json`` (configurable).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._models: dict[str, CategoryModel] = {}
        self._load()

    def predict(self, category: str, base_estimate: float) -> float:
        """Return adjusted estimate for *category*."""
        model = self._models.get(category)
        if model is None:
            return base_estimate
        return model.predict(base_estimate)

    def record(
        self, category: str, predicted: float, actual: float
    ) -> None:
        """Record an observation and persist."""
        if category not in self._models:
            self._models[category] = CategoryModel()
        self._models[category].update(predicted, actual)
        self._save()

    # -- Persistence --------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._models = {
                k: CategoryModel(**v) for k, v in data.items()
            }
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.warning(
                "Cannot load estimation learner from %s: %s", self._path, exc
            )
            self._models = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {k: asdict(v) for k, v in self._models.items()},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "Cannot save estimation learner to %s: %s", self._path, exc
            )
