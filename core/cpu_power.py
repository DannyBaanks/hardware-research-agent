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


# (control path, value meaning "boost on", value meaning "boost off"). Intel's knob is inverted.
BOOST_SWITCHES = (
    ("/sys/devices/system/cpu/cpufreq/boost", "1", "0"),
    ("/sys/devices/system/cpu/intel_pstate/no_turbo", "0", "1"),
)
EXTERNAL_SUPPLY_TYPES = ("Mains", "USB", "USB_PD", "USB_C")
# Daemons that rewrite the boost switch at runtime (on charger events, load, profiles...).
BOOST_DAEMONS = ("tlp", "auto-cpufreq", "tuned", "system76-power")


def boost_switch(root: str = ""):
    """(path, on_value, off_value) of the CPU boost switch, or (None, None, None).

    The one shared copy of this logic: core discovery and experiments/cpu-boost use it.
    recipes/smart-boost/ihra-smart-boost is a POSIX-sh mirror kept in parity by tests.
    """
    for path, on, off in BOOST_SWITCHES:
        if _read(root + path) in ("0", "1"):
            return path, on, off
    return None, None, None


def on_ac_power(root: str = "") -> Optional[bool]:
    """True on a charger, False on battery, None when unknown (typical desktop).

    Batteries with scope=Device (wireless mice, controllers, headsets) are ignored: they say
    nothing about how the computer itself is powered.
    """
    base = root + "/sys/class/power_supply"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    seen_external = False
    system_battery_status = []
    for name in names:
        d = os.path.join(base, name)
        if _read(os.path.join(d, "scope")) == "Device":
            continue
        kind = _read(os.path.join(d, "type"))
        if kind in EXTERNAL_SUPPLY_TYPES:
            seen_external = True
            if _read(os.path.join(d, "online")) == "1":
                return True
        elif kind == "Battery":
            system_battery_status.append(_read(os.path.join(d, "status")))
    if seen_external:
        return False
    if system_battery_status:
        # A laptop that exposes only its battery: Discharging means no charger.
        return "Discharging" not in system_battery_status
    # Desktops often expose no power_supply at all.
    return None


def boost_owners(root: str = "", check_services: bool = True) -> List[Dict[str, object]]:
    """Other things on this machine that write the boost switch. Read-only.

    runtime=True means it can flip the switch while something runs (udev rule, daemon);
    runtime=False means it only sets it at boot (tmpfiles rule, oneshot unit).
    """
    import glob
    import shutil
    import subprocess

    import re

    owners: List[Dict[str, object]] = []
    pattern = ("cpufreq/boost", "no_turbo")
    # A rule or unit usually calls a script (RUN+="/usr/local/sbin/x", ExecStart=/path/x):
    # look inside that script too, or e.g. a renamed smart-boost copy slips through.
    called = re.compile(r'(?:RUN\+?=\s*"|ExecStart=-?)\s*(/[^\s"]+)')

    def head(path: str) -> str:
        # Called targets may be binaries (snap rules): read a bounded prefix, ignore bad bytes.
        try:
            with open(path, errors="ignore") as f:
                return f.read(65536)
        except OSError:
            return ""

    def writes_boost(text: str) -> bool:
        if any(p in text for p in pattern):
            return True
        return any(any(p in head(root + target) for p in pattern)
                   for target in called.findall(text))

    for runtime, globs in ((True, ("/etc/udev/rules.d/*.rules", "/lib/udev/rules.d/*.rules")),
                           (False, ("/etc/tmpfiles.d/*", "/etc/systemd/system/*.service", "/etc/rc.local"))):
        for g in globs:
            for path in sorted(glob.glob(root + g)):
                if writes_boost(head(path)):
                    owners.append({"owner": path[len(root):], "runtime": runtime})
    if check_services and not root and shutil.which("systemctl"):
        for svc in BOOST_DAEMONS:
            try:
                active = subprocess.run(["systemctl", "is-active", "--quiet", svc],
                                        timeout=5).returncode == 0
            except (OSError, subprocess.SubprocessError):
                active = False
            if active:
                owners.append({"owner": f"{svc}.service (active)", "runtime": True})
    return owners


CPUFREQ_BASE = "/sys/devices/system/cpu/cpu0/cpufreq"
# The surface a Tier 1 intervention can leave changed. Deliberately excludes load
# and clock readings: those move on their own and would report drift nobody caused.
MONITORED_STATE_FIELDS = (
    "scaling_driver",
    "governor",
    "energy_performance_preference",
    "boost_enabled",
    "boost_control",
    "platform_profile",
)


def monitored_state(root: str = "") -> Dict[str, object]:
    """Reads the monitored CPU power surface. Read-only, and it never sleeps.

    Unlike the discovery helpers an unreadable surface is an error, not a None:
    a campaign that cannot observe the host state cannot claim it changed nothing.
    """
    base = root + CPUFREQ_BASE
    try:
        os.listdir(base)
    except OSError as exc:
        raise OSError(f"CPU power surface is unreadable at {base}: {exc}") from exc
    path, on, _ = boost_switch(root)
    state = {
        "scaling_driver": _read(base + "/scaling_driver"),
        "governor": _read(base + "/scaling_governor"),
        "energy_performance_preference": _read(base + "/energy_performance_preference"),
        "boost_enabled": None if path is None else _read(root + path) == on,
        "boost_control": path,
        "platform_profile": _read(root + "/sys/firmware/acpi/platform_profile"),
    }
    if all(state[field] is None for field in MONITORED_STATE_FIELDS):
        raise ValueError("No monitored CPU power state is readable")
    return state


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
        path, on, _ = boost_switch(self.root)
        if path is None:
            return None, None
        return _read(self._p(path)) == on, path

    def _on_ac(self) -> Optional[bool]:
        return on_ac_power(self.root)

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
