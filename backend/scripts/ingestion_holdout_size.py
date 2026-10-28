"""Exact CDF holdout confidence bounds and power sizing.

- ``clopper_pearson_lower`` computes a one-sided exact lower bound.
- ``minimal_n`` finds the first sample size reaching the requested power.
- ``observed_rate`` / ``label_error`` (§14.1 T5): a majority label is wrong with
  probability ``e``, independent of whether the judge is right, so the rate the
  holdout can see is p(1-e) + (1-p)e. ``power`` and ``minimal_n`` take
  ``label_error``; ``render_noise_table`` shows every row at one noise level (T5 found
  93% precision unprovable at e = 4.2%; the preregistered bar is now 88%).
"""

from __future__ import annotations

import math

# (metric, n, k, assumed true rate, threshold, label error). Write precision: the bar is
# 88% at the measured 4.2% should_write label error (§14.1 T5, r16.10); 93% is not
# provable at that noise. The other rows' label error has not been measured (0 here).
ROWS = (
    ("write precision", 220, 202, 0.97, 0.88, 0.042),
    ("write recall", 180, 145, 0.83, 0.75, 0.0),
    ("DDD routing", 150, 129, 0.88, 0.80, 0.0),
    ("D1 missing why", 130, 123, 0.96, 0.90, 0.0),
    ("duplicate unchanged", 90, 86, 0.97, 0.90, 0.0),
)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1e-300 if abs(d) < 1e-300 else d
    d = 1.0 / d
    h = d
    for m in range(1, 401):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _ibeta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _beta_ppf(probability: float, a: float, b: float) -> float:
    """Invert regularized beta by monotone bisection."""
    low, high = 0.0, 1.0
    for _ in range(100):
        mid = (low + high) / 2.0
        if _ibeta(mid, a, b) < probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def clopper_pearson_lower(k: int, n: int, alpha: float = 0.05) -> float:
    """Return the exact one-sided ``1-alpha`` binomial lower bound."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("require n > 0 and 0 <= k <= n")
    if k == 0:
        return 0.0
    return _beta_ppf(alpha, float(k), float(n - k + 1))


def required_successes(n: int, threshold: float, alpha: float = 0.05) -> int:
    """Find the smallest k whose exact lower bound reaches threshold."""
    for k in range(n + 1):
        if clopper_pearson_lower(k, n, alpha) >= threshold:
            return k
    return n + 1


def binomial_tail(n: int, k: int, probability: float) -> float:
    """Return P[X >= k] for X~Binomial(n, probability)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return math.fsum(math.comb(n, value) * probability**value * (1.0 - probability)**(n - value)
                     for value in range(k, n + 1))


def observed_rate(true_rate: float, label_error: float) -> float:
    """Agreement rate a holdout sees when each label is wrong with ``label_error``."""
    if not 0.0 <= label_error < 0.5:
        raise ValueError("label_error must be in [0, 0.5)")
    return true_rate * (1.0 - label_error) + (1.0 - true_rate) * label_error


def power(n: int, assumed_rate: float, threshold: float, alpha: float = 0.05,
          label_error: float = 0.0) -> float:
    """Probability that an exact lower-bound test passes, with noisy labels."""
    return binomial_tail(n, required_successes(n, threshold, alpha),
                         observed_rate(assumed_rate, label_error))


def minimal_n(assumed_rate: float, threshold: float, *, target_power: float = 0.80,
              alpha: float = 0.05, maximum: int = 10_000, label_error: float = 0.0) -> int:
    """Return the first n whose exact test has at least target power."""
    if observed_rate(assumed_rate, label_error) <= threshold:
        raise ValueError("observed rate is at or below the threshold: no n can pass")
    for n in range(1, maximum + 1):
        if power(n, assumed_rate, threshold, alpha, label_error) >= target_power:
            return n
    raise ValueError("no sample size reached target power")


def required_true_rate(n: int, threshold: float, label_error: float, *,
                       target_power: float = 0.80, alpha: float = 0.05) -> float | None:
    """Smallest true rate (0.001 grid) that passes with ``target_power`` at ``n``."""
    for step in range(int(threshold * 1000), 1001):
        rate = step / 1000
        if power(n, rate, threshold, alpha, label_error) >= target_power:
            return rate
    return None


def max_label_error(n: int, assumed_rate: float, threshold: float, *,
                    target_power: float = 0.80, alpha: float = 0.05) -> float:
    """Largest label error (0.001 grid) at which ``n`` still has ``target_power``."""
    best = 0.0
    for step in range(0, 500):
        error = step / 1000
        if power(n, assumed_rate, threshold, alpha, error) < target_power:
            break
        best = error
    return best


def render_table() -> str:
    """Render the preregistered §14 table and reproducible calculations."""
    lines = [
        "| metric | n | k | assumed true rate | threshold | label error | lower 95% | power | minimal n |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, n, k, assumed, threshold, error in ROWS:
        lines.append(
            f"| {name} | {n} | {k} | {assumed:.2f} | {threshold:.2f} | {error:.3f} | "
            f"{clopper_pearson_lower(k, n):.4f} | "
            f"{power(n, assumed, threshold, label_error=error):.4f} | "
            f"{minimal_n(assumed, threshold, label_error=error)} |"
        )
    return "\n".join(lines)


def render_noise_table(label_error: float = 0.042) -> str:
    """§14.1 T5: each preregistered row under ``label_error`` noisy majority labels."""
    lines = [
        f"| metric | n | assumed true | observed at e={label_error:.3f} | power | "
        "true rate needed at n | max e at n |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, n, _k, assumed, threshold, _error in ROWS:
        needed = required_true_rate(n, threshold, label_error)
        lines.append(
            f"| {name} | {n} | {assumed:.2f} | {observed_rate(assumed, label_error):.4f} | "
            f"{power(n, assumed, threshold, label_error=label_error):.4f} | "
            f"{'none' if needed is None else f'{needed:.3f}'} | "
            f"{max_label_error(n, assumed, threshold):.3f} |"
        )
    return "\n".join(lines)


def main() -> int:
    """Print the fixed holdout table and the T5 label-noise table."""
    print(render_table())
    print()
    print(render_noise_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
