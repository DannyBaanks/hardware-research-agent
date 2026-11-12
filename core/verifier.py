from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import statistics
import math
import re

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MIN_UPLIFT_FACTOR = 1.03
NEGATIVE_CONTROL_LIMIT = 0.03
# The probability, if the intervention did nothing, of ranks at least this
# favourable. Three runs per side can reach exactly 0.05, and only when every
# intervention run beats every baseline run.
SIGNIFICANCE_LEVEL = 0.05
MAX_RUNS_PER_SIDE = 64


@lru_cache(maxsize=None)
def _rank_arrangements(m: int, n: int) -> Tuple[int, ...]:
    """Counts orderings of m intervention and n baseline runs by their U statistic."""
    if m == 0 or n == 0:
        return (1,)
    counts = [0] * (m * n + 1)
    # The largest run is either an intervention run, which beats all n baseline
    # runs, or a baseline run, which adds nothing.
    for u, ways in enumerate(_rank_arrangements(m - 1, n)):
        counts[u + n] += ways
    for u, ways in enumerate(_rank_arrangements(m, n - 1)):
        counts[u] += ways
    return tuple(counts)


def exact_rank_p_value(baseline: List[float], intervention: List[float]) -> float:
    """One-sided exact Mann-Whitney p-value that intervention runs are larger.

    A tie counts against the claim, so equal measurements can never help it. The
    result depends only on the order of the runs, which is why one fast outlier
    cannot carry a verdict the way it moves a mean.
    """
    if not baseline or not intervention:
        raise ValueError("Both groups need at least one run")
    if len(baseline) > MAX_RUNS_PER_SIDE or len(intervention) > MAX_RUNS_PER_SIDE:
        raise ValueError(f"At most {MAX_RUNS_PER_SIDE} runs per side are supported")
    wins = sum(1 for after in intervention for before in baseline if after > before)
    counts = _rank_arrangements(len(intervention), len(baseline))
    return sum(counts[wins:]) / sum(counts)


def group_statistics(values: List[float]) -> Dict[str, Any]:
    """Median with the spread a single headline number hides (AF-06)."""
    ordered = sorted(values)
    result = {
        "runs": len(ordered),
        "median": statistics.median(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "iqr": None,
        "stdev": None,
    }
    if len(ordered) >= 2:
        quartiles = statistics.quantiles(ordered, n=4, method="inclusive")
        result["iqr"] = quartiles[2] - quartiles[0]
        result["stdev"] = statistics.stdev(ordered)
    return result


def _positive_throughputs(runs) -> Optional[List[float]]:
    try:
        values = [run["throughput"] for run in runs]
        normalized = [
            float(value)
            for value in values
            if not isinstance(value, bool) and isinstance(value, (int, float))
        ]
    except (KeyError, TypeError, OverflowError, ValueError):
        return None
    if len(normalized) != len(values) or any(
        not math.isfinite(value) or value <= 0 for value in normalized
    ):
        return None
    return normalized


def negative_control_delta_from_runs(
    control_baseline, control_intervention, digest_pattern=SHA256_PATTERN
) -> Tuple[Optional[float], Optional[str]]:
    """Derives the control drift from executed control runs, or says why it cannot.

    A delta computed here is an observation. A delta typed into an evidence file
    is a statement, and nothing in IHRA can tell the two apart afterwards.
    """
    if not isinstance(control_baseline, list) or not isinstance(control_intervention, list):
        return None, "Negative control runs must be two lists"
    if len(control_baseline) < 3 or len(control_intervention) < 3:
        return None, "Negative control needs at least 3 runs per side"
    runs = control_baseline + control_intervention
    if any(
        not isinstance(run, dict)
        or not isinstance(run.get("output_sha256"), str)
        or not digest_pattern.fullmatch(run["output_sha256"])
        for run in runs
    ):
        return None, "Every negative control run must provide a valid output digest"
    if len({run["output_sha256"].lower() for run in runs}) > 1:
        return None, "Negative control output digest is not invariant"
    before = _positive_throughputs(control_baseline)
    after = _positive_throughputs(control_intervention)
    if before is None or after is None:
        return None, "Negative control throughput must be finite positive numbers"
    delta = statistics.median(after) / statistics.median(before) - 1
    if not math.isfinite(delta):
        return None, "Negative control ratio is outside the finite numeric range"
    return delta, None

class VerificationVerdict(Enum):
    DEMONSTRATED = "DEMONSTRATED"
    REFUTED = "REFUTED"
    NOT_DEMONSTRATED = "NOT_DEMONSTRATED"
    CALCULATION_CORRUPT = "CALCULATION_CORRUPT"

@dataclass
class VerificationResult:
    verdict: VerificationVerdict
    confidence: float
    reason: str
    median_speedup_factor: float
    output_hash_invariant: bool
    negative_control_invariant: bool
    # Filled once the measurements are known to be usable numbers.
    p_value: Optional[float] = None
    baseline_statistics: Optional[Dict[str, Any]] = None
    intervention_statistics: Optional[Dict[str, Any]] = None

class AntiFakeVerifier:
    """
    Evaluates benchmark executions against adversarial artifacts.
    Enforces the Triangulation Rule and detects false-positive software traps.
    """

    def verify_experiment(
        self,
        baseline_runs: List[Dict[str, Any]],
        intervention_runs: List[Dict[str, Any]],
        software_telemetry: Dict[str, Any],
        negative_control_delta: Optional[float] = None,
        output_digest_pattern=SHA256_PATTERN,
    ) -> VerificationResult:
        result = self._verify(
            baseline_runs,
            intervention_runs,
            software_telemetry,
            negative_control_delta,
            output_digest_pattern,
        )
        # Spread is reported with every verdict that got as far as real numbers,
        # so a refusal still shows how noisy the evidence was.
        if isinstance(baseline_runs, list) and isinstance(intervention_runs, list):
            before = _positive_throughputs(baseline_runs)
            after = _positive_throughputs(intervention_runs)
            if before and after:
                result.baseline_statistics = group_statistics(before)
                result.intervention_statistics = group_statistics(after)
        return result

    def _verify(
        self,
        baseline_runs,
        intervention_runs,
        software_telemetry,
        negative_control_delta,
        output_digest_pattern,
    ) -> VerificationResult:

        if (
            not isinstance(baseline_runs, list)
            or not isinstance(intervention_runs, list)
            or not isinstance(software_telemetry, dict)
        ):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Baseline runs, intervention runs, and software telemetry must be structured inputs",
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=False,
            )
        
        # 1. Check repetition count (Reject Best-of-N peak hunting)
        if len(baseline_runs) < 3 or len(intervention_runs) < 3:
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Insufficient repetitions: minimum 3 required for statistical validity",
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=True
            )

        if len(baseline_runs) > MAX_RUNS_PER_SIDE or len(intervention_runs) > MAX_RUNS_PER_SIDE:
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason=f"Too many repetitions: at most {MAX_RUNS_PER_SIDE} runs per side are supported",
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=False,
            )

        # 2. Require an output digest for every run before comparing results.
        #    SHA-256 unless the caller names the weaker format its evidence carries.
        hash_pattern = output_digest_pattern
        all_runs = baseline_runs + intervention_runs
        if any(
            not isinstance(run, dict)
            or not isinstance(run.get("output_sha256"), str)
            or not hash_pattern.fullmatch(run["output_sha256"])
            for run in all_runs
        ):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason=(
                    "Every run must provide a valid 64-character SHA-256 output hash"
                    if hash_pattern is SHA256_PATTERN
                    else "Every run must provide a valid output digest"
                ),
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=False,
            )

        baseline_hashes = {r["output_sha256"].lower() for r in baseline_runs}
        intervention_hashes = {r["output_sha256"].lower() for r in intervention_runs}

        if len(baseline_hashes) > 1 or len(intervention_hashes) > 1:
            return VerificationResult(
                verdict=VerificationVerdict.CALCULATION_CORRUPT,
                confidence=1.0,
                reason="Mathematical non-determinism detected across runs of the same configuration",
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=True
            )

        if baseline_hashes != intervention_hashes:
            return VerificationResult(
                verdict=VerificationVerdict.CALCULATION_CORRUPT,
                confidence=1.0,
                reason="Calculation corruption: output SHA-256 hash diverged between baseline and intervention",
                median_speedup_factor=1.0,
                output_hash_invariant=False,
                negative_control_invariant=True
            )

        # 3. Validate measurements and compute medians.
        try:
            base_throughputs = [r["throughput"] for r in baseline_runs]
            inter_throughputs = [r["throughput"] for r in intervention_runs]
        except KeyError:
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Every run must provide a throughput measurement",
                median_speedup_factor=1.0,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )

        measurements = base_throughputs + inter_throughputs
        try:
            normalized_measurements = [
                float(value)
                for value in measurements
                if not isinstance(value, bool) and isinstance(value, (int, float))
            ]
        except (OverflowError, ValueError):
            normalized_measurements = []
        if (
            len(normalized_measurements) != len(measurements)
            or any(not math.isfinite(value) or value <= 0 for value in normalized_measurements)
        ):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Throughput measurements must be finite positive numbers",
                median_speedup_factor=1.0,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )

        base_median = statistics.median(normalized_measurements[:len(base_throughputs)])
        inter_median = statistics.median(normalized_measurements[len(base_throughputs):])

        speedup = inter_median / base_median if base_median > 0 else 1.0
        if not math.isfinite(speedup):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Throughput ratio is outside the finite numeric range",
                median_speedup_factor=1.0,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )

        # 4. Check Negative Control Invariance
        try:
            normalized_control_delta = (
                float(negative_control_delta)
                if not isinstance(negative_control_delta, bool)
                and isinstance(negative_control_delta, (int, float))
                else None
            )
        except (OverflowError, ValueError):
            normalized_control_delta = None
        if normalized_control_delta is None or not math.isfinite(normalized_control_delta):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="A finite negative_control_delta from an executed control is required",
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )

        if abs(normalized_control_delta) > NEGATIVE_CONTROL_LIMIT:  # More than 3% drift
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.5,
                reason=f"Confounding detected: negative control drifted by {normalized_control_delta*100:.1f}% (limit: 3%)",
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=False
            )

        # 5. Detect The Ada D3cold Trap (Software Getter False Positive)
        # If software getter claims "unlocked" / "2x", but physical throughput is stock (speedup < 1.05)
        sw_claims_unlock = software_telemetry.get("software_getter_unlocked", False) or \
                            software_telemetry.get("reported_rate") in ("0 0 0", "full", "unlocked")
        
        if sw_claims_unlock and speedup < 1.05:
            return VerificationResult(
                verdict=VerificationVerdict.REFUTED,
                confidence=1.0,
                reason="SOFTWARE_GETTER_FALSE_POSITIVE: Driver reports unlocked (0 0 0), but physical throughput is stock (speedup < 1.05x)",
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=True
            )

        # 6. A median that moved is only an uplift if the runs agree on it.
        #    Two noisy groups can differ by more than the threshold by chance.
        base_values = normalized_measurements[:len(base_throughputs)]
        inter_values = normalized_measurements[len(base_throughputs):]
        p_value = exact_rank_p_value(base_values, inter_values)
        if speedup >= MIN_UPLIFT_FACTOR and p_value > SIGNIFICANCE_LEVEL:
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=1.0 - p_value,
                reason=(
                    f"Median moved {(speedup-1)*100:.1f}% but the runs overlap: "
                    f"NOISE_NOT_EXCLUDED (exact rank test p={p_value:.3f}, limit {SIGNIFICANCE_LEVEL})"
                ),
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=True,
                p_value=p_value,
            )

        # 7. Legitimate Demonstrated Uplift
        if speedup >= MIN_UPLIFT_FACTOR:
            return VerificationResult(
                verdict=VerificationVerdict.DEMONSTRATED,
                confidence=1.0 - p_value,
                reason=(
                    f"Median throughput uplift of {(speedup-1)*100:.1f}% observed with supplied "
                    f"mathematical parity evidence (exact rank test p={p_value:.3f})"
                ),
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=True,
                p_value=p_value,
            )

        # No safe gain found / within noise floor
        return VerificationResult(
            verdict=VerificationVerdict.NOT_DEMONSTRATED,
            confidence=0.90,
            reason=f"No uplift at or above the configured threshold observed (median speedup: {speedup:.3f}x)",
            median_speedup_factor=speedup,
            output_hash_invariant=True,
            negative_control_invariant=True,
            p_value=p_value,
        )
