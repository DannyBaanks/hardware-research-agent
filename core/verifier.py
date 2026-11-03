from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import statistics
import math
import re

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

        # 2. Require a SHA-256 digest for every run before comparing results.
        hash_pattern = re.compile(r"^[0-9a-fA-F]{64}$")
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
                reason="Every run must provide a valid 64-character SHA-256 output hash",
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
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in measurements
        ):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="Throughput measurements must be finite positive numbers",
                median_speedup_factor=1.0,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )
        
        base_median = statistics.median(base_throughputs)
        inter_median = statistics.median(inter_throughputs)

        speedup = inter_median / base_median if base_median > 0 else 1.0

        # 4. Check Negative Control Invariance
        if (
            isinstance(negative_control_delta, bool)
            or not isinstance(negative_control_delta, (int, float))
            or not math.isfinite(negative_control_delta)
        ):
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.0,
                reason="A finite negative_control_delta from an executed control is required",
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=False,
            )

        if abs(negative_control_delta) > 0.03: # More than 3% drift on negative control
            return VerificationResult(
                verdict=VerificationVerdict.NOT_DEMONSTRATED,
                confidence=0.5,
                reason=f"Confounding detected: negative control drifted by {negative_control_delta*100:.1f}% (limit: 3%)",
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

        # 6. Legitimate Demonstrated Uplift
        if speedup >= 1.03:
            return VerificationResult(
                verdict=VerificationVerdict.DEMONSTRATED,
                confidence=0.95,
                reason=f"Statistically significant physical uplift of {(speedup-1)*100:.1f}% verified with mathematical parity",
                median_speedup_factor=speedup,
                output_hash_invariant=True,
                negative_control_invariant=True
            )

        # No safe gain found / within noise floor
        return VerificationResult(
            verdict=VerificationVerdict.NOT_DEMONSTRATED,
            confidence=0.90,
            reason=f"No significant performance delta observed (speedup: {speedup:.3f}x within noise threshold)",
            median_speedup_factor=speedup,
            output_hash_invariant=True,
            negative_control_invariant=True
        )
