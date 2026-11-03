import unittest
from core.verifier import AntiFakeVerifier, VerificationVerdict

class TestEvidenceVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = AntiFakeVerifier()

    def test_calculation_corruption_is_flagged(self):
        # Baseline produces hash A, but intervention produces hash B (corrupted math)
        baseline = [
            {"throughput": 100.0, "output_sha256": "aaaa1111"},
            {"throughput": 100.2, "output_sha256": "aaaa1111"},
            {"throughput": 99.8, "output_sha256": "aaaa1111"}
        ]
        intervention = [
            {"throughput": 150.0, "output_sha256": "bbbb2222"},
            {"throughput": 150.2, "output_sha256": "bbbb2222"},
            {"throughput": 149.8, "output_sha256": "bbbb2222"}
        ]
        res = self.verifier.verify_experiment(baseline, intervention, {})
        self.assertEqual(res.verdict, VerificationVerdict.CALCULATION_CORRUPT)
        self.assertIn("Calculation corruption", res.reason)

    def test_negative_control_drift_rejects_claim(self):
        baseline = [
            {"throughput": 100.0, "output_sha256": "aaaa1111"},
            {"throughput": 100.0, "output_sha256": "aaaa1111"},
            {"throughput": 100.0, "output_sha256": "aaaa1111"}
        ]
        intervention = [
            {"throughput": 110.0, "output_sha256": "aaaa1111"},
            {"throughput": 110.0, "output_sha256": "aaaa1111"},
            {"throughput": 110.0, "output_sha256": "aaaa1111"}
        ]
        # Negative control drifted by 8% (e.g. clock drifted)
        res = self.verifier.verify_experiment(baseline, intervention, {}, negative_control_delta=0.08)
        self.assertEqual(res.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("negative control drifted", res.reason)

    def test_valid_uplift_with_parity_is_demonstrated(self):
        baseline = [
            {"throughput": 100.0, "output_sha256": "aaaa1111"},
            {"throughput": 100.1, "output_sha256": "aaaa1111"},
            {"throughput": 99.9, "output_sha256": "aaaa1111"}
        ]
        intervention = [
            {"throughput": 106.0, "output_sha256": "aaaa1111"},
            {"throughput": 106.2, "output_sha256": "aaaa1111"},
            {"throughput": 105.8, "output_sha256": "aaaa1111"}
        ]
        res = self.verifier.verify_experiment(baseline, intervention, {}, negative_control_delta=0.001)
        self.assertEqual(res.verdict, VerificationVerdict.DEMONSTRATED)
        self.assertGreaterEqual(res.median_speedup_factor, 1.05)

if __name__ == "__main__":
    unittest.main()
