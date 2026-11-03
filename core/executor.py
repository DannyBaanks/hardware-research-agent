import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Tuple

from .verifier import AntiFakeVerifier


@dataclass(frozen=True)
class DryRunReceipt:
    experiment_id: str
    fixture_id: str
    replay_status: str
    verdict: str
    reason: str
    median_speedup_factor: float
    simulation_only: bool
    hardware_accessed: bool
    commands_executed: int
    system_state_changed: bool
    rollback_status: str
    trace: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class DryRunExecutor:
    """Replays bundled synthetic evidence without running workloads or touching the host."""

    FIXTURES = {
        "ada_d3cold_false_positive": "ada_d3cold_false_positive",
        "no_safe_gain_found": "no_safe_gain_found",
    }
    MAX_FIXTURE_BYTES = 1024 * 1024

    def __init__(self):
        self.fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        self.verifier = AntiFakeVerifier()

    def _load_fixture_file(self, fixture_id: str, filename: str) -> Dict[str, object]:
        path = self.fixtures_dir / self.FIXTURES[fixture_id] / filename
        if path.stat().st_size > self.MAX_FIXTURE_BYTES:
            raise ValueError(f"Fixture file exceeds {self.MAX_FIXTURE_BYTES} bytes: {filename}")
        with path.open(encoding="utf-8") as fixture_file:
            data = json.load(fixture_file)
        if not isinstance(data, dict):
            raise ValueError(f"Fixture file must contain a JSON object: {filename}")
        return data

    def replay_fixture(self, fixture_id: str) -> DryRunReceipt:
        if fixture_id not in self.FIXTURES:
            raise ValueError(f"Unknown fixture: {fixture_id}")

        benchmark = self._load_fixture_file(fixture_id, "mock_benchmark.json")
        telemetry = self._load_fixture_file(fixture_id, "mock_telemetry.json")
        verification = self.verifier.verify_experiment(
            baseline_runs=benchmark.get("baseline_runs"),
            intervention_runs=benchmark.get("intervention_runs"),
            software_telemetry=telemetry,
            negative_control_delta=benchmark.get("negative_control_delta"),
        )

        return DryRunReceipt(
            experiment_id=f"DRY_RUN_{fixture_id.upper()}",
            fixture_id=fixture_id,
            replay_status="COMPLETED",
            verdict=verification.verdict.value,
            reason=verification.reason,
            median_speedup_factor=verification.median_speedup_factor,
            simulation_only=True,
            hardware_accessed=False,
            commands_executed=0,
            system_state_changed=False,
            rollback_status="NOT_REQUIRED_NO_STATE_CHANGE",
            trace=(
                "FIXTURE_LOADED",
                "SYNTHETIC_EVIDENCE_REPLAYED",
                "VERIFIER_COMPLETED",
                "NO_HOST_ACTIONS_PERFORMED",
            ),
        )
