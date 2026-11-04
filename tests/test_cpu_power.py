# SPDX-License-Identifier: MIT
"""CPU power-state hints against fake sysfs trees (read-only; never touches the host)."""
import tempfile
import unittest
from pathlib import Path

from core.cpu_power import CpuPowerInspector


def _w(root: Path, rel: str, text: str):
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n")


class CpuPowerTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        cf = "/sys/devices/system/cpu/cpu0/cpufreq/"
        _w(self.root, cf + "scaling_driver", "amd-pstate-epp")
        _w(self.root, cf + "scaling_governor", "performance")
        _w(self.root, cf + "cpuinfo_max_freq", "4301000")
        _w(self.root, cf + "amd_pstate_max_freq", "5017834")
        _w(self.root, "/sys/firmware/acpi/platform_profile", "performance")
        for cpu in range(4):  # 2 cores x 2 threads
            _w(self.root, f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id", str(cpu // 2))
            _w(self.root, f"/sys/devices/system/cpu/cpu{cpu}/topology/physical_package_id", "0")
        _w(self.root, "/sys/class/power_supply/ACAD/type", "Mains")
        _w(self.root, "/sys/class/power_supply/ACAD/online", "1")
        _w(self.root, "/sys/class/power_supply/BAT0/type", "Battery")
        _w(self.root, "/proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0")

    def tearDown(self):
        self._tmp.cleanup()

    def inspect(self):
        return CpuPowerInspector(sysfs_root=str(self.root), sample_seconds=0).inspect()

    def ids(self, state):
        return {h["id"] for h in state.hints}

    def test_disabled_amd_boost_is_flagged_with_the_hidden_headroom(self):
        _w(self.root, "/sys/devices/system/cpu/cpufreq/boost", "0")
        s = self.inspect()
        self.assertFalse(s.boost_enabled)
        self.assertEqual((s.current_max_mhz, s.hardware_max_mhz), (4301, 5017))
        self.assertIn("BOOST_DISABLED", self.ids(s))

    def test_intel_no_turbo_is_inverted(self):
        _w(self.root, "/sys/devices/system/cpu/intel_pstate/no_turbo", "1")
        s = self.inspect()
        self.assertFalse(s.boost_enabled)
        self.assertIn("BOOST_DISABLED", self.ids(s))
        _w(self.root, "/sys/devices/system/cpu/intel_pstate/no_turbo", "0")
        self.assertTrue(self.inspect().boost_enabled)

    def test_enabled_boost_on_ac_has_no_hints(self):
        _w(self.root, "/sys/devices/system/cpu/cpufreq/boost", "1")
        s = self.inspect()
        self.assertTrue(s.boost_enabled)
        self.assertTrue(s.on_ac_power)
        self.assertEqual(self.ids(s), set())

    def test_battery_and_low_power_profile_are_flagged(self):
        _w(self.root, "/sys/class/power_supply/ACAD/online", "0")
        _w(self.root, "/sys/firmware/acpi/platform_profile", "low-power")
        s = self.inspect()
        self.assertFalse(s.on_ac_power)
        self.assertEqual(self.ids(s), {"ON_BATTERY", "POWER_SAVING_PROFILE"})

    def test_usb_c_charger_counts_as_ac(self):
        _w(self.root, "/sys/class/power_supply/ACAD/online", "0")
        _w(self.root, "/sys/class/power_supply/usbc/type", "USB")
        _w(self.root, "/sys/class/power_supply/usbc/online", "1")
        self.assertTrue(self.inspect().on_ac_power)

    def test_desktop_without_power_supply_is_unknown_not_battery(self):
        for name in ("ACAD", "BAT0"):
            for f in (self.root / "sys/class/power_supply" / name).iterdir():
                f.unlink()
            (self.root / "sys/class/power_supply" / name).rmdir()
        s = self.inspect()
        self.assertIsNone(s.on_ac_power)
        self.assertNotIn("ON_BATTERY", self.ids(s))

    def test_physical_cores_come_from_topology_not_threads_over_two(self):
        s = self.inspect()
        self.assertEqual((s.physical_cores, s.logical_cpus), (2, 4))
        for cpu in range(4):  # no SMT: every thread is its own core
            _w(self.root, f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id", str(cpu))
        self.assertEqual(self.inspect().physical_cores, 4)

    def test_background_load_hint_uses_two_samples(self):
        inspector = CpuPowerInspector(sysfs_root=str(self.root), sample_seconds=0)
        samples = iter(["cpu  100 0 100 800 0 0 0 0 0 0", "cpu  180 0 100 820 0 0 0 0 0 0"])
        original = inspector._busy_percent

        def fake_busy():
            a, b = next(samples), next(samples)
            (self.root / "proc/stat").write_text(a)
            parse = lambda line: (sum(map(int, line.split()[1:])), int(line.split()[4]))
            (ta, ia), (tb, ib) = parse(a), parse(b)
            return round(100 * (1 - (ib - ia) / (tb - ta)), 1)
        inspector._busy_percent = fake_busy
        s = inspector.inspect()
        inspector._busy_percent = original
        self.assertEqual(s.busy_percent, 80.0)
        self.assertIn("BACKGROUND_LOAD", self.ids(s))

    def test_never_writes(self):
        _w(self.root, "/sys/devices/system/cpu/cpufreq/boost", "0")
        before = {p: p.read_text() for p in self.root.rglob("*") if p.is_file()}
        self.inspect()
        after = {p: p.read_text() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
