import unittest
from core.verifier import AntiFakeVerifier, VerificationVerdict

class TestEvidenceVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = AntiFakeVerifier()
        self.hash_a = "a" * 64
        self.hash_b = "b" * 64

    def test_calculation_corruption_is_flagged(self):
        # Baseline produces hash A, but intervention produces hash B (corrupted math)
        baseline = [
            {"throughput": 100.0, "output_sha256": self.hash_a},
            {"throughput": 100.2, "output_sha256": self.hash_a},
            {"throughput": 99.8, "output_sha256": self.hash_a}
        ]
        intervention = [
            {"throughput": 150.0, "output_sha256": self.hash_b},
            {"throughput": 150.2, "output_sha256": self.hash_b},
            {"throughput": 149.8, "output_sha256": self.hash_b}
        ]
        res = self.verifier.verify_experiment(baseline, intervention, {}, negative_control_delta=0.0)
        self.assertEqual(res.verdict, VerificationVerdict.CALCULATION_CORRUPT)
        self.assertIn("Calculation corruption", res.reason)

    def test_negative_control_drift_rejects_claim(self):
        baseline = [
            {"throughput": 100.0, "output_sha256": self.hash_a},
            {"throughput": 100.0, "output_sha256": self.hash_a},
            {"throughput": 100.0, "output_sha256": self.hash_a}
        ]
        intervention = [
            {"throughput": 110.0, "output_sha256": self.hash_a},
            {"throughput": 110.0, "output_sha256": self.hash_a},
            {"throughput": 110.0, "output_sha256": self.hash_a}
        ]
        # Negative control drifted by 8% (e.g. clock drifted)
        res = self.verifier.verify_experiment(baseline, intervention, {}, negative_control_delta=0.08)
        self.assertEqual(res.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("negative control drifted", res.reason)

    def test_valid_uplift_with_parity_is_demonstrated(self):
        baseline = [
            {"throughput": 100.0, "output_sha256": self.hash_a},
            {"throughput": 100.1, "output_sha256": self.hash_a},
            {"throughput": 99.9, "output_sha256": self.hash_a}
        ]
        intervention = [
            {"throughput": 106.0, "output_sha256": self.hash_a},
            {"throughput": 106.2, "output_sha256": self.hash_a},
            {"throughput": 105.8, "output_sha256": self.hash_a}
        ]
        res = self.verifier.verify_experiment(baseline, intervention, {}, negative_control_delta=0.001)
        self.assertEqual(res.verdict, VerificationVerdict.DEMONSTRATED)
        self.assertGreaterEqual(res.median_speedup_factor, 1.05)

    def test_missing_hash_in_any_run_is_not_demonstrated(self):
        baseline = [{"throughput": 100.0, "output_sha256": self.hash_a} for _ in range(3)]
        intervention = [{"throughput": 105.0, "output_sha256": self.hash_a} for _ in range(3)]
        del intervention[1]["output_sha256"]

        result = self.verifier.verify_experiment(
            baseline, intervention, {}, negative_control_delta=0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("Every run must provide", result.reason)

    def test_missing_negative_control_is_not_demonstrated(self):
        baseline = [{"throughput": 100.0, "output_sha256": self.hash_a} for _ in range(3)]
        intervention = [{"throughput": 100.0, "output_sha256": self.hash_a} for _ in range(3)]

        result = self.verifier.verify_experiment(baseline, intervention, {})
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("negative_control_delta", result.reason)

if __name__ == "__main__":
    unittest.main()
