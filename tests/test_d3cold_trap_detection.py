import json
import unittest
from pathlib import Path
from core.verifier import AntiFakeVerifier, VerificationVerdict

class TestD3ColdTrapDetection(unittest.TestCase):
    def setUp(self):
        self.verifier = AntiFakeVerifier()
        self.fixtures_dir = Path(__file__).parent.parent / "fixtures" / "ada_d3cold_false_positive"

    def test_d3cold_false_positive_is_rejected(self):
        with open(self.fixtures_dir / "mock_telemetry.json") as f:
            telem = json.load(f)
        with open(self.fixtures_dir / "mock_benchmark.json") as f:
            bench = json.load(f)

        result = self.verifier.verify_experiment(
            baseline_runs=bench["baseline_runs"],
            intervention_runs=bench["intervention_runs"],
            software_telemetry=telem,
            negative_control_delta=bench["negative_control_delta"]
        )

        # The verifier MUST reject this claim as REFUTED because software claimed unlocked but throughput was stock!
        self.assertEqual(result.verdict, VerificationVerdict.REFUTED)
        self.assertIn("SOFTWARE_GETTER_FALSE_POSITIVE", result.reason)
        self.assertLess(result.median_speedup_factor, 1.05)

if __name__ == "__main__":
    unittest.main()
