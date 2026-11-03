# SPDX-License-Identifier: MIT
import unittest
from unittest.mock import patch, MagicMock
from adapters.base import CapabilityStatus
from adapters.rea_adapter import ReaAdapter
from adapters.ada_adapter import AdaUnlockAdapter
from core.sentinel import SentinelGate, SentinelVerdict


class TestReaAdapter(unittest.TestCase):
    def test_rea_probe_when_path_exists(self):
        adapter = ReaAdapter(rea_path="/home/danny/Development/rea")
        probe = adapter.probe()
        self.assertIn(probe.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        if probe.status == CapabilityStatus.SUPPORTED:
            self.assertEqual(probe.device_id, "rea_binary_analysis_suite")

    def test_rea_inspect_missing_file_fails_safely(self):
        adapter = ReaAdapter()
        res = adapter.inspect_binary("/nonexistent/file/path/test.bin")
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("does not exist", res["error"])


class TestAdaUnlockAdapter(unittest.TestCase):
    def test_ada_probe_and_telemetry(self):
        adapter = AdaUnlockAdapter()
        probe = adapter.probe()
        self.assertIn(probe.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.UNSUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        telemetry = adapter.read_telemetry()
        self.assertIn("d3cold_status", telemetry)

    def test_ada_d3cold_analysis_format(self):
        adapter = AdaUnlockAdapter()
        d3cold = adapter.read_d3cold_status()
        self.assertIn("status", d3cold)


class TestSentinelIntegration(unittest.TestCase):
    def test_sentinel_blocks_smu_and_msr(self):
        gate = SentinelGate()
        plan_msr = {
            "experiment_id": "test_msr",
            "risk_tier": 0,
            "workload": {"command": "wrmsr 0x199 0x2000"}
        }
        res = gate.evaluate_plan(plan_msr)
        self.assertEqual(res.verdict, SentinelVerdict.DENIED)

    def test_sentinel_approves_safe_python_benchmark(self):
        gate = SentinelGate()
        plan_bench = {
            "experiment_id": "test_bench",
            "risk_tier": 0,
            "workload": {"command": "python3 benchmark.py --reps 3"}
        }
        res = gate.evaluate_plan(plan_bench)
        self.assertEqual(res.verdict, SentinelVerdict.AUTHORIZED)


if __name__ == "__main__":
    unittest.main()
