# SPDX-License-Identifier: MIT
import unittest
from unittest.mock import patch
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
import json
from adapters.base import CapabilityStatus
from adapters.rea_adapter import ReaAdapter
from adapters.ada_adapter import AdaUnlockAdapter
from core.cli import cmd_ada
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

    def test_ada_cli_never_claims_parameter_proves_hardware_safety(self):
        adapter = AdaUnlockAdapter()
        adapter.read_telemetry = lambda: {"d3cold_status": {"status": "configured"}}
        adapter.read_d3cold_status = lambda: {
            "driver_parameter_indicates_prevention": True,
            "hardware_state_verified": False,
        }
        output = StringIO()
        with patch("core.cli.AdaUnlockAdapter", return_value=adapter), redirect_stdout(output):
            cmd_ada(Namespace())

        result = json.loads(output.getvalue())
        self.assertEqual(result["recommendation_status"], "NOT_DEMONSTRATED")
        self.assertIn("NOT_DEMONSTRATED", result["recommendation"])


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

    def test_sentinel_approves_exact_read_only_command(self):
        gate = SentinelGate()
        plan_bench = {
            "experiment_id": "test_bench",
            "risk_tier": 0,
            "workload": {"command": "lscpu"}
        }
        res = gate.evaluate_plan(plan_bench)
        self.assertEqual(res.verdict, SentinelVerdict.AUTHORIZED)


if __name__ == "__main__":
    unittest.main()
