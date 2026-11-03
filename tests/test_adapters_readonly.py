import unittest
from adapters import (
    LinuxInventoryAdapter,
    LinuxCpuPerfAdapter,
    NvidiaTelemetryAdapter,
    AmdXdnaDiscoveryAdapter,
    CapabilityStatus
)

class TestAdaptersReadOnly(unittest.TestCase):
    def test_linux_inventory_probe(self):
        adapter = LinuxInventoryAdapter()
        res = adapter.probe()
        self.assertIn(res.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        telem = adapter.read_telemetry()
        self.assertIsInstance(telem, dict)

    def test_linux_cpu_perf_probe(self):
        adapter = LinuxCpuPerfAdapter()
        res = adapter.probe()
        self.assertIn(res.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        profile = adapter.get_current_profile()
        self.assertIsInstance(profile, str)

    def test_nvidia_telemetry_probe(self):
        adapter = NvidiaTelemetryAdapter()
        res = adapter.probe()
        self.assertIn(res.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        telem = adapter.read_telemetry()
        self.assertIsInstance(telem, dict)

    def test_amd_xdna_discovery_probe(self):
        adapter = AmdXdnaDiscoveryAdapter()
        res = adapter.probe()
        self.assertIn(res.status, (CapabilityStatus.SUPPORTED, CapabilityStatus.NOT_APPLICABLE))
        telem = adapter.read_telemetry()
        self.assertIsInstance(telem, dict)

if __name__ == "__main__":
    unittest.main()
