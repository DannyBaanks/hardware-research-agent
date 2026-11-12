import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import cli
from core.evidence import (
    FORMAT_BOOST_MATRIX,
    FORMAT_EVIDENCE_JSON,
    MAX_EVIDENCE_BYTES,
    EvidenceError,
    evaluate_evidence,
    evaluate_evidence_file,
)
from core.verifier import (
    AntiFakeVerifier,
    VerificationVerdict,
    exact_rank_p_value,
    negative_control_delta_from_runs,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 64
ORDER = [0, 1, 1, 0, 0, 1]  # the counterbalanced order run_boost_matrix.py uses


def runs(values, digest=HASH):
    return [{"throughput": value, "output_sha256": digest} for value in values]


def matrix_bytes(rates, on_ac=True, extra_rows=(), hashes=None):
    """A matrix_*.jsonl as run_boost_matrix.py writes it.

    `rates` maps (mode, threads) to (boost-off rates, boost-on rates), three each.
    """
    keys = {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}
    lines = [
        {
            "env": {
                "date": "2026-11-12T10:00:00+0000",
                "kernel": "6.8.0-test",
                "cpu": "Test CPU",
                "boost_knob": "/sys/devices/system/cpu/cpufreq/boost",
                "original": "1",
                "on_ac": True,
                "platform_profile": "balanced",
                "other_boost_owners": [],
            }
        }
    ]
    seen = {group: [0, 0] for group in rates}
    for block, boost in enumerate(ORDER, 1):
        for (mode, threads), by_state in rates.items():
            index = seen[(mode, threads)][boost]
            seen[(mode, threads)][boost] += 1
            ac = on_ac(block) if callable(on_ac) else on_ac
            lines.append(
                {
                    "mode": mode,
                    "vector_bits": 256,
                    "threads": threads,
                    "seconds": 2.0,
                    keys[mode]: by_state[boost][index],
                    "hash": (hashes or {}).get((mode, threads, boost), "00000000deadbeef"),
                    "exit": 0,
                    "peak_mhz": 4900 if boost else 3800,
                    "temp_after_c": 70.0,
                    "block": block,
                    "boost": boost,
                    "start_temp_c": 55.0,
                    "on_ac": ac,
                }
            )
    lines.extend(extra_rows)
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


REAL_GAIN = {
    ("int", 1): ([4.00, 4.02, 3.99], [4.64, 4.66, 4.62]),
    ("int", 12): ([40.0, 40.3, 39.8], [43.4, 43.6, 43.1]),
    ("triad", 12): ([31.2, 31.3, 31.1], [31.3, 31.4, 31.2]),
}


class TestRankStatistics(unittest.TestCase):
    def test_three_separated_runs_reach_exactly_the_significance_level(self):
        self.assertAlmostEqual(exact_rank_p_value([1, 2, 3], [4, 5, 6]), 1 / 20)

    def test_one_overlapping_run_is_not_significant(self):
        self.assertAlmostEqual(exact_rank_p_value([1, 2, 3], [2.5, 5, 6]), 2 / 20)

    def test_ties_never_help_the_claim(self):
        self.assertEqual(exact_rank_p_value([1, 1, 1], [1, 1, 1]), 1.0)
        self.assertGreater(exact_rank_p_value([1, 2, 3], [3, 5, 6]), 1 / 20)

    def test_more_runs_tolerate_one_overlap(self):
        baseline = [100, 101, 99, 100.5, 99.5, 100.2]
        intervention = [106, 105.5, 100.8, 106.3, 105.9, 106.1]
        self.assertLess(exact_rank_p_value(baseline, intervention), 0.05)

    def test_group_sizes_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "At most"):
            exact_rank_p_value([1.0] * 65, [2.0] * 3)


class TestVerifierRejectsNoise(unittest.TestCase):
    def setUp(self):
        self.verifier = AntiFakeVerifier()

    def test_median_shift_inside_the_noise_is_not_demonstrated(self):
        # Medians 100 -> 104 clear the 3% threshold, but each group spans far more
        # than that. This exact input used to return DEMONSTRATED at 0.95.
        result = self.verifier.verify_experiment(
            runs([100, 80, 120]), runs([70, 104, 130]), {}, negative_control_delta=0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("NOISE_NOT_EXCLUDED", result.reason)
        self.assertAlmostEqual(result.median_speedup_factor, 1.04)
        self.assertGreater(result.p_value, 0.05)

    def test_single_fast_outlier_is_not_demonstrated(self):
        result = self.verifier.verify_experiment(
            runs([100, 100.1, 99.9]), runs([100.0, 140, 141]), {}, negative_control_delta=0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("NOISE_NOT_EXCLUDED", result.reason)

    def test_separated_uplift_reports_spread_and_p_value(self):
        result = self.verifier.verify_experiment(
            runs([100, 100.1, 99.9]), runs([106, 106.2, 105.8]), {}, negative_control_delta=0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.DEMONSTRATED)
        self.assertAlmostEqual(result.p_value, 0.05)
        self.assertAlmostEqual(result.confidence, 0.95)
        self.assertEqual(result.baseline_statistics["runs"], 3)
        self.assertAlmostEqual(result.baseline_statistics["median"], 100)
        self.assertAlmostEqual(result.intervention_statistics["min"], 105.8)
        self.assertGreater(result.intervention_statistics["stdev"], 0)
        self.assertGreater(result.intervention_statistics["iqr"], 0)

    def test_refusals_still_report_the_spread(self):
        result = self.verifier.verify_experiment(runs([100, 80, 120]), runs([70, 104, 130]), {})
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("negative_control_delta", result.reason)
        self.assertEqual(result.baseline_statistics["max"], 120)

    def test_too_many_runs_fail_closed(self):
        result = self.verifier.verify_experiment(
            runs([100.0] * 65), runs([110.0] * 65), {}, negative_control_delta=0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("Too many repetitions", result.reason)

    def test_default_digest_is_still_sha256(self):
        short = runs([100, 100.1, 99.9], digest="00000000deadbeef")
        result = self.verifier.verify_experiment(
            short, runs([110, 111, 112], digest="00000000deadbeef"), {}, 0.0
        )
        self.assertEqual(result.verdict, VerificationVerdict.NOT_DEMONSTRATED)
        self.assertIn("SHA-256", result.reason)


class TestNegativeControlFromRuns(unittest.TestCase):
    def test_delta_is_the_median_ratio(self):
        delta, issue = negative_control_delta_from_runs(
            runs([100, 101, 99]), runs([102, 103, 101])
        )
        self.assertIsNone(issue)
        self.assertAlmostEqual(delta, 0.02)

    def test_unusable_controls_yield_no_delta(self):
        cases = {
            "at least 3": (runs([100, 100]), runs([100, 100, 100])),
            "two lists": (None, runs([100, 100, 100])),
            "valid output digest": (runs([100, 100, 100], digest="xyz"), runs([100, 100, 100])),
            "not invariant": (runs([100, 100, 100]), runs([100, 100, 100], digest="b" * 64)),
            "finite positive": (runs([100, 0, 100]), runs([100, 100, 100])),
        }
        for fragment, (before, after) in cases.items():
            with self.subTest(fragment=fragment):
                delta, issue = negative_control_delta_from_runs(before, after)
                self.assertIsNone(delta)
                self.assertIn(fragment, issue)


class TestEvidenceObject(unittest.TestCase):
    def _evaluate(self, value, **kwargs):
        return evaluate_evidence(json.dumps(value).encode(), **kwargs)

    def test_bundled_fixtures_are_judged_from_their_own_bytes(self):
        report = evaluate_evidence_file(
            str(REPOSITORY_ROOT / "fixtures" / "no_safe_gain_found" / "mock_benchmark.json")
        )
        self.assertEqual(report["status"], "EVALUATED")
        self.assertEqual(report["format"], FORMAT_EVIDENCE_JSON)
        self.assertEqual(report["summary"], {"NOT_DEMONSTRATED": 1})
        self.assertFalse(report["host_accessed"])
        self.assertEqual(report["commands_executed"], 0)
        self.assertIn("caller-supplied", report["evidence_origin"])
        self.assertRegex(report["evidence_sha256"], r"^[0-9a-f]{64}$")

    def test_d3cold_trap_is_refuted_when_telemetry_travels_with_the_evidence(self):
        fixture = REPOSITORY_ROOT / "fixtures" / "ada_d3cold_false_positive"
        evidence = json.loads((fixture / "mock_benchmark.json").read_text())
        evidence["software_telemetry"] = json.loads((fixture / "mock_telemetry.json").read_text())
        report = self._evaluate(evidence)
        self.assertEqual(report["comparisons"][0]["verdict"], "REFUTED")
        self.assertIn("SOFTWARE_GETTER_FALSE_POSITIVE", report["comparisons"][0]["reason"])

    def test_control_runs_are_computed_and_outrank_a_declared_delta(self):
        report = self._evaluate(
            {
                "experiment_id": "EXP-1",
                "baseline_runs": runs([100, 100.1, 99.9]),
                "intervention_runs": runs([110, 110.2, 109.8]),
                # The file claims a quiet control; its own control runs say +10%.
                "negative_control_delta": 0.0,
                "control_baseline_runs": runs([50, 50.1, 49.9], digest="c" * 64),
                "control_intervention_runs": runs([55, 55.1, 54.9], digest="c" * 64),
            }
        )
        comparison = report["comparisons"][0]
        self.assertEqual(comparison["id"], "EXP-1")
        self.assertEqual(comparison["verdict"], "NOT_DEMONSTRATED")
        self.assertIn("negative control drifted", comparison["reason"])
        self.assertAlmostEqual(report["negative_control"]["delta"], 0.1)
        self.assertTrue(report["negative_control"]["ignored_declared_delta"])
        self.assertIn("computed", report["negative_control"]["source"])

    def test_quiet_executed_control_allows_a_demonstrated_gain(self):
        report = self._evaluate(
            {
                "baseline_runs": runs([100, 100.1, 99.9]),
                "intervention_runs": runs([110, 110.2, 109.8]),
                "control_baseline_runs": runs([50, 50.1, 49.9]),
                "control_intervention_runs": runs([50.1, 50.0, 50.2]),
            }
        )
        self.assertEqual(report["summary"], {"DEMONSTRATED": 1})

    def test_half_a_control_is_no_control(self):
        report = self._evaluate(
            {
                "baseline_runs": runs([100, 100.1, 99.9]),
                "intervention_runs": runs([110, 110.2, 109.8]),
                "control_baseline_runs": runs([50, 50.1, 49.9]),
            }
        )
        self.assertEqual(report["summary"], {"NOT_DEMONSTRATED": 1})
        self.assertIn("two lists", report["negative_control"]["issue"])

    def test_declared_delta_is_labelled_as_declared(self):
        report = self._evaluate(
            {
                "baseline_runs": runs([100, 100.1, 99.9]),
                "intervention_runs": runs([110, 110.2, 109.8]),
                "negative_control_delta": 0.001,
            }
        )
        self.assertEqual(report["summary"], {"DEMONSTRATED": 1})
        self.assertIn("declared", report["negative_control"]["source"])

    def test_unjudgeable_evidence_is_an_error_not_a_verdict(self):
        good = {"baseline_runs": [], "intervention_runs": []}
        cases = {
            "missing intervention_runs": json.dumps({"baseline_runs": []}).encode(),
            "unknown fields verdict": json.dumps(dict(good, verdict="DEMONSTRATED")).encode(),
            "Duplicate evidence key": b'{"baseline_runs": [], "baseline_runs": [], "intervention_runs": []}',
            "non-finite number": b'{"baseline_runs": [{"throughput": NaN}], "intervention_runs": []}',
            "must be a JSON object": b"[1, 2, 3]",
            "not valid UTF-8": b"\xff\xfe",
            "is empty": b"  \n",
            "software_telemetry must be an object": json.dumps(
                dict(good, software_telemetry="unlocked")
            ).encode(),
        }
        for fragment, data in cases.items():
            with self.subTest(fragment=fragment):
                with self.assertRaisesRegex(EvidenceError, fragment):
                    evaluate_evidence(data)

    def test_control_selector_is_refused_for_an_evidence_object(self):
        with self.assertRaisesRegex(EvidenceError, "boost matrix"):
            self._evaluate({"baseline_runs": [], "intervention_runs": []}, control_selector="int:1")


class TestBoostMatrixEvidence(unittest.TestCase):
    def test_gain_is_demonstrated_only_against_an_executed_control(self):
        data = matrix_bytes(REAL_GAIN)

        without = evaluate_evidence(data)
        self.assertEqual(without["format"], FORMAT_BOOST_MATRIX)
        self.assertEqual(without["summary"], {"NOT_DEMONSTRATED": 3})
        self.assertIn("--control", without["negative_control"]["hint"])
        single = next(item for item in without["comparisons"] if item["id"] == "int:1")
        self.assertAlmostEqual(single["median_speedup_factor"], 4.64 / 4.00)
        self.assertIn("negative_control_delta", single["reason"])

        report = evaluate_evidence(data, control_selector="triad:12")
        self.assertEqual(report["summary"], {"DEMONSTRATED": 2})
        self.assertEqual([item["id"] for item in report["comparisons"]], ["int:1", "int:12"])
        self.assertAlmostEqual(report["negative_control"]["delta"], 31.3 / 31.2 - 1)
        self.assertIn("triad:12", report["negative_control"]["source"])
        self.assertEqual(report["context"]["baseline"], "boost switch off")
        self.assertEqual(report["context"]["recorded_environment"]["cpu"], "Test CPU")
        for item in report["comparisons"]:
            self.assertEqual(item["baseline"]["runs"], 3)
            self.assertAlmostEqual(item["p_value"], 0.05)

    def test_noisy_job_is_not_demonstrated_beside_a_real_one(self):
        rates = dict(REAL_GAIN)
        rates[("fma", 12)] = ([100, 80, 120], [70, 104, 130])
        report = evaluate_evidence(matrix_bytes(rates), control_selector="triad:12")
        verdicts = {item["id"]: item["verdict"] for item in report["comparisons"]}
        self.assertEqual(
            verdicts,
            {"fma:12": "NOT_DEMONSTRATED", "int:1": "DEMONSTRATED", "int:12": "DEMONSTRATED"},
        )

    def test_drifting_control_blocks_every_claim(self):
        rates = dict(REAL_GAIN)
        rates[("triad", 12)] = ([31.2, 31.3, 31.1], [34.0, 34.1, 33.9])
        report = evaluate_evidence(matrix_bytes(rates), control_selector="triad:12")
        self.assertEqual(report["summary"], {"NOT_DEMONSTRATED": 2})
        for item in report["comparisons"]:
            self.assertIn("negative control drifted", item["reason"])

    def test_result_hash_change_between_states_is_corruption(self):
        data = matrix_bytes(REAL_GAIN, hashes={("int", 1, 1): "1111111111111111"})
        report = evaluate_evidence(data, control_selector="triad:12")
        verdicts = {item["id"]: item["verdict"] for item in report["comparisons"]}
        self.assertEqual(verdicts["int:1"], "CALCULATION_CORRUPT")
        self.assertEqual(verdicts["int:12"], "DEMONSTRATED")

    def test_failed_runs_are_excluded_and_counted(self):
        failed = {"mode": "int", "threads": 1, "exit": 139, "error": "exit 139; stderr: ",
                  "block": 6, "boost": 1, "start_temp_c": 55.0, "on_ac": True}
        rates = dict(REAL_GAIN)
        rates[("int", 1)] = ([4.00, 4.02, 3.99], [4.64, 4.66, 4.62])
        report = evaluate_evidence(
            matrix_bytes(rates, extra_rows=[failed]), control_selector="triad:12"
        )
        single = next(item for item in report["comparisons"] if item["id"] == "int:1")
        self.assertEqual(single["excluded_failed_runs"], 1)
        self.assertEqual(single["intervention"]["runs"], 3)

    def test_too_few_surviving_runs_is_not_demonstrated(self):
        lines = matrix_bytes(REAL_GAIN).decode().splitlines()
        kept = [
            line for line in lines
            if not ('"mode": "int"' in line and '"threads": 1,' in line and '"block": 6' in line)
        ]
        report = evaluate_evidence(("\n".join(kept) + "\n").encode(), control_selector="triad:12")
        single = next(item for item in report["comparisons"] if item["id"] == "int:1")
        self.assertEqual(single["verdict"], "NOT_DEMONSTRATED")
        self.assertIn("Insufficient repetitions", single["reason"])

    def test_charger_unplugged_mid_matrix_is_not_demonstrated(self):
        data = matrix_bytes(REAL_GAIN, on_ac=lambda block: block < 5)
        report = evaluate_evidence(data, control_selector="triad:12")
        self.assertEqual(report["summary"], {"NOT_DEMONSTRATED": 2})
        for item in report["comparisons"]:
            self.assertIn("POWER_SOURCE_MISMATCH", item["reason"])
        self.assertEqual(report["context"]["power_sources_seen"], ["False", "True"])

    def test_bad_control_selector_and_malformed_rows_are_errors(self):
        data = matrix_bytes(REAL_GAIN)
        with self.assertRaisesRegex(EvidenceError, "MODE:THREADS"):
            evaluate_evidence(data, control_selector="triad")
        with self.assertRaisesRegex(EvidenceError, "not a job in this file"):
            evaluate_evidence(data, control_selector="triad:4")
        with self.assertRaisesRegex(EvidenceError, "line 2 has no valid mode"):
            evaluate_evidence(b'{"env": {}}\n{"threads": 1}\n')
        with self.assertRaisesRegex(EvidenceError, "no boost state"):
            evaluate_evidence(b'{"env": {}}\n{"mode": "int", "threads": 1, "Gops_s": 4.0}\n')
        with self.assertRaisesRegex(EvidenceError, "neither"):
            evaluate_evidence(b'{"mode": "int"}\n{"mode": "int"}\n')
        with self.assertRaisesRegex(EvidenceError, "besides the control"):
            evaluate_evidence(
                matrix_bytes({("triad", 12): REAL_GAIN[("triad", 12)]}),
                control_selector="triad:12",
            )

    def test_real_script_rate_keys_match_the_adapter(self):
        # The adapter must not drift from the writer it claims to read.
        from core.evidence import BOOST_MATRIX_RATE_KEYS

        source = (
            REPOSITORY_ROOT / "experiments" / "cpu-boost" / "run_boost_matrix.py"
        ).read_text()
        self.assertIn(
            'keys = {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}', source
        )
        self.assertEqual(
            BOOST_MATRIX_RATE_KEYS, {"int": "Gops_s", "fma": "GFLOP_s", "triad": "GB_s"}
        )


class TestVerifyEvidenceCommand(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _command(self, path, control=None):
        output = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(output):
            try:
                cli.cmd_verify_evidence(SimpleNamespace(evidence=str(path), control=control))
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(output.getvalue())

    def test_judged_file_exits_zero_even_without_a_gain(self):
        path = self.root / "matrix.jsonl"
        path.write_bytes(matrix_bytes(REAL_GAIN))
        code, report = self._command(path)
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"], {"NOT_DEMONSTRATED": 3})

        code, report = self._command(path, control="triad:12")
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"], {"DEMONSTRATED": 2})

    def test_unjudgeable_files_exit_two_with_a_denial(self):
        missing = self.root / "missing.json"
        directory = self.root / "directory"
        directory.mkdir()
        garbage = self.root / "garbage.json"
        garbage.write_text("not json")
        target = self.root / "target.json"
        target.write_bytes(matrix_bytes(REAL_GAIN))
        link = self.root / "link.jsonl"
        link.symlink_to(target)
        oversized = self.root / "oversized.json"
        with oversized.open("wb") as handle:
            handle.truncate(MAX_EVIDENCE_BYTES + 1)

        for path, fragment in (
            (missing, "cannot be opened"),
            (directory, "regular file"),
            (garbage, "not valid JSON"),
            (link, "cannot be opened"),
            (oversized, "size limit"),
        ):
            with self.subTest(path=path.name):
                code, report = self._command(path)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "DENIED")
                self.assertEqual(report["verdict"], "DENY")
                self.assertIn(fragment, report["reason"])

    def test_verifying_runs_no_process_and_writes_nothing(self):
        path = self.root / "matrix.jsonl"
        path.write_bytes(matrix_bytes(REAL_GAIN))
        before = sorted(os.listdir(self.root))

        def forbidden(*args, **kwargs):
            raise AssertionError("verify-evidence must not start a process")

        with patch("subprocess.run", forbidden), patch("subprocess.Popen", forbidden):
            code, _ = self._command(path, control="triad:12")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(self.root)), before)

    def test_command_is_registered_on_the_cli(self):
        path = self.root / "matrix.jsonl"
        path.write_bytes(matrix_bytes(REAL_GAIN))
        output = io.StringIO()
        argv = ["ihra", "verify-evidence", str(path), "--control", "triad:12"]
        with patch("sys.argv", argv), contextlib.redirect_stdout(output):
            cli.main()
        self.assertEqual(json.loads(output.getvalue())["summary"], {"DEMONSTRATED": 2})


if __name__ == "__main__":
    unittest.main()
