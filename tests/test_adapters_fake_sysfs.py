# SPDX-License-Identifier: MIT
"""Adapter behaviour against fake sysfs/procfs trees, so results do not depend on the host."""
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from adapters.adapters import AmdXdnaDiscoveryAdapter, _smi_number
from adapters.ada_adapter import ADA_UNLOCK_DEVICE_IDS, AdaUnlockAdapter
from adapters.base import CapabilityStatus
from adapters.rea_adapter import ReaAdapter
from core.cli import cmd_ada


def _write(root: Path, rel: str, text: str):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _pci(root: Path, slot: str, vendor: str, device: str, pci_class: str, power_state="D0"):
    base = f"/sys/bus/pci/devices/{slot}"
    _write(root, f"{base}/vendor", vendor + "\n")
    _write(root, f"{base}/device", device + "\n")
    _write(root, f"{base}/class", pci_class + "\n")
    _write(root, f"{base}/power_state", power_state + "\n")


class FakeTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write(self.root, "/dev/nvidia0", "")

    def tearDown(self):
        self._tmp.cleanup()

    def adapter(self):
        return AdaUnlockAdapter(ada_path=str(self.root / "no-repo"), sysfs_root=str(self.root))


class TestAdaProbe(FakeTree):
    def test_28a1_laptop_matches_and_audio_function_is_ignored(self):
        _pci(self.root, "0000:01:00.0", "0x10de", "0x28a1", "0x030000")
        _pci(self.root, "0000:01:00.1", "0x10de", "0x22be", "0x040300")
        probe = self.adapter().probe()
        self.assertEqual(probe.status, CapabilityStatus.SUPPORTED)
        self.assertEqual(probe.device_id, "0x10de:0x28a1")
        self.assertEqual(probe.telemetry["pci_slot"], "0000:01:00.0")

    def test_non_ada_gpu_reports_its_own_id_not_the_audio_function(self):
        _pci(self.root, "0000:01:00.0", "0x10de", "0x2520", "0x030000")  # GA106M
        _pci(self.root, "0000:01:00.1", "0x10de", "0x228e", "0x040300")
        probe = self.adapter().probe()
        self.assertEqual(probe.status, CapabilityStatus.UNSUPPORTED)
        self.assertEqual(probe.device_id, "0x10de:0x2520")

    def test_0x2820_is_not_a_module_target(self):
        # AD106M RTX 4070 Laptop: the kernel module has no profile for it.
        _pci(self.root, "0000:01:00.0", "0x10de", "0x2820", "0x030000")
        self.assertEqual(self.adapter().probe().status, CapabilityStatus.UNSUPPORTED)

    def test_id_list_matches_the_fork_kernel_headers_when_present(self):
        repo = Path(os.environ.get(
            "ADA_UNLOCK_HOME",
            Path.home() / "Development" / "gpu-unlock-hp" / "gpu-unlock-ada",
        )) / "kernel" / "gpu_unlock"
        headers = [repo / "prep_610.h", repo / "arch_610.h"]
        if not all(h.is_file() for h in headers):
            self.skipTest("gpu-unlock-ada checkout not available")
        import re
        ids = set()
        for header in headers:
            for m in re.finditer(r"#define GU_AD\w+\s+0x([0-9a-f]{4})u", header.read_text()):
                ids.add("0x" + m.group(1))
        self.assertEqual(ids, set(ADA_UNLOCK_DEVICE_IDS))


class TestAdaD3cold(FakeTree):
    def setUp(self):
        super().setUp()
        _pci(self.root, "0000:01:00.0", "0x10de", "0x28a1", "0x030000", power_state="D0")

    def test_reads_dpm_from_procfs_like_the_real_driver(self):
        _write(self.root, "/proc/driver/nvidia/params",
               "ResmanDebugLevel: 4294967295\nDynamicPowerManagement: 0\n"
               "DynamicPowerManagementVideoMemoryThreshold: 200\n")
        d3 = self.adapter().read_d3cold_status()
        self.assertEqual(d3["dynamic_power_management_raw"], "0")
        self.assertEqual(d3["source"], "/proc/driver/nvidia/params")
        self.assertTrue(d3["driver_parameter_indicates_prevention"])
        self.assertFalse(d3["hardware_state_verified"])
        self.assertEqual(d3["pci_power_state_kernel_reported"], "D0")

    def test_fine_grained_dpm_does_not_indicate_prevention(self):
        _write(self.root, "/proc/driver/nvidia/params", "DynamicPowerManagement: 2\n")
        d3 = self.adapter().read_d3cold_status()
        self.assertFalse(d3["driver_parameter_indicates_prevention"])
        self.assertEqual(d3["status"], "PARAMETER_DOES_NOT_INDICATE_PREVENTION")

    def test_threshold_key_is_not_mistaken_for_dpm(self):
        _write(self.root, "/proc/driver/nvidia/params",
               "DynamicPowerManagementVideoMemoryThreshold: 0\n")
        self.assertEqual(self.adapter().read_d3cold_status()["status"], "NOT_EXPOSED")

    def test_missing_parameter_is_not_reported_as_misconfiguration(self):
        adapter = self.adapter()
        output = StringIO()
        with patch("core.cli.AdaUnlockAdapter", return_value=adapter), \
                patch("adapters.ada_adapter.shutil.which", return_value=None), \
                redirect_stdout(output):
            cmd_ada(Namespace())
        result = json.loads(output.getvalue())
        self.assertEqual(result["d3cold_analysis"]["status"], "NOT_EXPOSED")
        self.assertIn("could not be read", result["recommendation"])
        self.assertNotIn("does not indicate", result["recommendation"])
        self.assertEqual(result["recommendation_status"], "NOT_DEMONSTRATED")


class TestAdaModuleStatus(FakeTree):
    def test_absent_module(self):
        self.assertEqual(self.adapter().read_module_status(), {"loaded": False})

    def test_loaded_module_is_never_reported_as_unlocked(self):
        _write(self.root, "/sys/module/gpu_unlock/version", "1.1.1\n")
        _write(self.root, "/sys/module/gpu_unlock/parameters/ad107_experimental", "Y\n")
        _write(self.root, "/sys/module/gpu_unlock/parameters/targets", "0000:01:00.0\n")
        status = self.adapter().read_module_status()
        self.assertTrue(status["loaded"])
        self.assertEqual(status["version"], "1.1.1")
        self.assertEqual(status["parameters"]["targets"], "0000:01:00.0")
        self.assertFalse(status["unlock_state_verified"])


class TestSmiParsing(unittest.TestCase):
    def test_not_available_fields_become_none(self):
        self.assertIsNone(_smi_number("[N/A]"))
        self.assertIsNone(_smi_number("N/A"))
        self.assertEqual(_smi_number("2.77"), 2.77)


class TestXdnaDiscovery(unittest.TestCase):
    def test_finds_npu_through_accel_class_on_any_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pci = root / "sys/devices/pci0000:00/0000:00:08.2/0000:c4:00.1"
            pci.mkdir(parents=True)
            (pci / "vendor").write_text("0x1022\n")
            (pci / "device").write_text("0x17f0\n")
            (pci / "fw_version").write_text("1.5.2.380\n")
            drv = root / "sys/bus/pci/drivers/amdxdna"
            drv.mkdir(parents=True)
            (pci / "driver").symlink_to(drv)
            accel = root / "sys/class/accel/accel0"
            accel.mkdir(parents=True)
            (accel / "device").symlink_to(pci)
            adapter = AmdXdnaDiscoveryAdapter(sysfs_root=str(root))
            self.assertEqual(adapter.probe().status, CapabilityStatus.SUPPORTED)
            tel = adapter.read_telemetry()
            self.assertEqual(tel["pci_slot"], "0000:c4:00.1")
            self.assertEqual(tel["driver"], "amdxdna")
            self.assertEqual(tel["firmware_version"], "1.5.2.380")

    def test_absent_npu(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AmdXdnaDiscoveryAdapter(sysfs_root=tmp)
            self.assertEqual(adapter.probe().status, CapabilityStatus.NOT_APPLICABLE)
            self.assertEqual(adapter.read_telemetry(), {"status": "ABSENT"})


class TestReaToolNames(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(delete=False)
        self._tmp.close()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def test_mcp_only_malbolge_tools_are_rejected_before_running_anything(self):
        with patch("adapters.rea_adapter.subprocess.run") as run:
            for name in ("inspect_malbolge", "trace-malbolge", "rea_execute"):
                res = ReaAdapter().inspect_binary(self._tmp.name, tool_name=name)
                self.assertEqual(res["status"], "ERROR")
                self.assertIn("MCP", res["error"])
            run.assert_not_called()

    def test_flag_like_tool_names_are_rejected(self):
        with patch("adapters.rea_adapter.subprocess.run") as run:
            res = ReaAdapter().inspect_binary(self._tmp.name, tool_name="--mcp")
            self.assertEqual(res["status"], "ERROR")
            run.assert_not_called()

    def test_snake_case_is_normalised_and_target_is_absolute(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rea_home = tmp.name
        os.makedirs(os.path.join(rea_home, "scripts"))
        Path(rea_home, "scripts", "rea.mjs").write_text("")
        with patch("adapters.rea_adapter.shutil.which", return_value="/usr/bin/node"), \
                patch("adapters.rea_adapter.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            run.return_value.stderr = ""
            rel = os.path.relpath(self._tmp.name)
            res = ReaAdapter(rea_path=rea_home).inspect_binary(rel, tool_name="binary_overview")
        argv = run.call_args.args[0]
        self.assertEqual(res["status"], "OK")
        self.assertEqual(argv[2], "binary-overview")
        self.assertTrue(os.path.isabs(argv[3]))

    def test_bare_npx_is_not_reported_as_rea(self):
        def which(name):
            return "/usr/bin/npx" if name == "npx" else None
        with patch("adapters.rea_adapter.shutil.which", side_effect=which):
            probe = ReaAdapter(rea_path="/nonexistent-rea").probe()
        self.assertEqual(probe.status, CapabilityStatus.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
