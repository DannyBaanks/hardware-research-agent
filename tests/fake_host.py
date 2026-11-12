"""A host that exists only inside a temporary directory.

The suite must reach the same verdict on a laptop, in a container and on a CI
runner. A test that reads the machine's own /sys, state directory or
powerprofilesctl is measuring that machine, not the code, so runners under test
are built over this fake surface instead.
"""

import fcntl
import os
from pathlib import Path

from core.rollback import RollbackContract

CPUFREQ = "sys/devices/system/cpu/cpu0/cpufreq"
BOOST = "sys/devices/system/cpu/cpufreq/boost"
PLATFORM_PROFILE = "sys/firmware/acpi/platform_profile"

# CI sets this where the real sandbox is installed, so a capability that silently
# went missing there fails the job instead of hiding behind a skip.
REQUIRE_SANDBOX = os.environ.get("IHRA_REQUIRE_SANDBOX") == "1"

MEMFD_SEALS_AVAILABLE = hasattr(os, "memfd_create") and all(
    hasattr(fcntl, name)
    for name in (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_SEAL",
        "F_SEAL_SHRINK",
        "F_SEAL_GROW",
        "F_SEAL_WRITE",
    )
)


def make_fake_sysfs(root: Path, profile: str = "performance") -> str:
    """Writes the monitored CPU power surface under `root` and returns it as a sysfs root."""
    root = Path(root)
    cpufreq = root / CPUFREQ
    cpufreq.mkdir(parents=True, exist_ok=True)
    (cpufreq / "scaling_driver").write_text("amd-pstate-epp\n", encoding="ascii")
    (cpufreq / "scaling_governor").write_text("performance\n", encoding="ascii")
    (cpufreq / "energy_performance_preference").write_text(profile + "\n", encoding="ascii")
    boost = root / BOOST
    boost.parent.mkdir(parents=True, exist_ok=True)
    boost.write_text("1\n", encoding="ascii")
    platform = root / PLATFORM_PROFILE
    platform.parent.mkdir(parents=True, exist_ok=True)
    platform.write_text(profile + "\n", encoding="ascii")
    return str(root)


def hermetic_contract(root: Path) -> RollbackContract:
    """A rollback contract that observes the fake surface and declares no restoration."""
    return RollbackContract(sysfs_root=make_fake_sysfs(Path(root) / "fake-host"))


def skip_or_fail(test_case, reason: str):
    """Skips on a host without the capability; fails where CI promised to provide it."""
    if REQUIRE_SANDBOX:
        test_case.fail(f"IHRA_REQUIRE_SANDBOX=1 but {reason}")
    test_case.skipTest(reason)
