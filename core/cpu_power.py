# SPDX-License-Identifier: MIT
"""
Read-only CPU power-state check.

Looks for the "hidden switches" that made a real laptop look slower than it is
(docs/HISTORY_REA_CPU_01.md): CPU boost disabled by a local config, running on
battery, a power-saving profile, and background load contaminating benchmarks.
It never writes anything. Every hint is a reason to measure, not proof of a gain.
"""

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class CpuPowerState:
    scaling_driver: Optional[str]
    governor: Optional[str]
    energy_performance_preference: Optional[str]
    boost_enabled: Optional[bool]
    boost_control: Optional[str]
    current_max_mhz: Optional[int]
    hardware_max_mhz: Optional[int]
    platform_profile: Optional[str]
    on_ac_power: Optional[bool]
    physical_cores: Optional[int]
    logical_cpus: int
    busy_percent: Optional[float]
    hints: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class CpuPowerInspector:
    def __init__(self, sysfs_root: str = "", sample_seconds: float = 0.5):
        self.root = sysfs_root
        self.sample_seconds = sample_seconds

    def _p(self, path: str) -> str:
        return self.root + path

    def _boost(self):
        """Return (enabled, control_path). Generic cpufreq knob first, then Intel's inverted one."""
        generic = "/sys/devices/system/cpu/cpufreq/boost"
        value = _read(self._p(generic))
        if value in ("0", "1"):
            return value == "1", generic
        intel = "/sys/devices/system/cpu/intel_pstate/no_turbo"
        value = _read(self._p(intel))
        if value in ("0", "1"):
            return value == "0", intel
        return None, None

    def _on_ac(self) -> Optional[bool]:
        base = self._p("/sys/class/power_supply")
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return None
        seen_external = False
        for name in names:
            kind = _read(os.path.join(base, name, "type"))
            if kind in ("Mains", "USB", "USB_PD", "USB_C"):
                seen_external = True
                if _read(os.path.join(base, name, "online")) == "1":
                    return True
        if seen_external:
            return False
        # Desktops often expose no power_supply at all.
        return None

    def _physical_cores(self) -> Optional[int]:
        base = self._p("/sys/devices/system/cpu")
        cores = set()
        try:
            for name in os.listdir(base):
                if not (name.startswith("cpu") and name[3:].isdigit()):
                    continue
                topo = os.path.join(base, name, "topology")
                core = _read(os.path.join(topo, "core_id"))
                pkg = _read(os.path.join(topo, "physical_package_id"))
                if core is not None:
                    cores.add((pkg, core))
        except OSError:
            return None
        return len(cores) or None

    def _busy_percent(self) -> Optional[float]:
        def snap():
            line = (_read(self._p("/proc/stat")) or "").splitlines()
            if not line or not line[0].startswith("cpu "):
                return None
            vals = [int(x) for x in line[0].split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals), idle
        a = snap()
        if a is None:
            return None
        time.sleep(self.sample_seconds)
        b = snap()
        if b is None or b[0] == a[0]:
            return None
        return round(100.0 * (1 - (b[1] - a[1]) / (b[0] - a[0])), 1)

    def inspect(self) -> CpuPowerState:
        cf = "/sys/devices/system/cpu/cpu0/cpufreq/"
        boost, control = self._boost()
        cur_max = _int(_read(self._p(cf + "cpuinfo_max_freq")))
        hw_max = _int(_read(self._p(cf + "amd_pstate_max_freq"))) or cur_max
        logical = os.cpu_count() or 0
        if self.root:
            try:
                logical = sum(1 for n in os.listdir(self._p("/sys/devices/system/cpu"))
                              if n.startswith("cpu") and n[3:].isdigit())
            except OSError:
                pass
        state = CpuPowerState(
            scaling_driver=_read(self._p(cf + "scaling_driver")),
            governor=_read(self._p(cf + "scaling_governor")),
            energy_performance_preference=_read(self._p(cf + "energy_performance_preference")),
            boost_enabled=boost,
            boost_control=control,
            current_max_mhz=cur_max // 1000 if cur_max else None,
            hardware_max_mhz=hw_max // 1000 if hw_max else None,
            platform_profile=_read(self._p("/sys/firmware/acpi/platform_profile")),
            on_ac_power=self._on_ac(),
            physical_cores=self._physical_cores(),
            logical_cpus=logical,
            busy_percent=self._busy_percent(),
        )
        state.hints = self._hints(state)
        return state

    @staticmethod
    def _hints(s: CpuPowerState) -> List[Dict[str, str]]:
        hints = []
        if s.boost_enabled is False:
            hints.append({
                "id": "BOOST_DISABLED",
                "detail": f"CPU boost is off ({s.boost_control}). Check who disabled it "
                          "(/etc/tmpfiles.d, systemd units, tlp/auto-cpufreq) before assuming a hardware limit.",
            })
        if s.on_ac_power is False:
            hints.append({
                "id": "ON_BATTERY",
                "detail": "Running on battery: laptops cap package power, so all-core results are not comparable to AC.",
            })
        if s.platform_profile in ("low-power", "quiet", "cool"):
            hints.append({
                "id": "POWER_SAVING_PROFILE",
                "detail": f"Platform profile is '{s.platform_profile}', which lowers sustained clocks.",
            })
        if s.busy_percent is not None and s.busy_percent >= 15:
            hints.append({
                "id": "BACKGROUND_LOAD",
                "detail": f"CPU is {s.busy_percent}% busy before any benchmark; close background jobs or results will be contaminated.",
            })
        return hints
