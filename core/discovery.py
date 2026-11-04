import os
import shutil
import subprocess
from typing import Dict, Any, List
from dataclasses import dataclass

from .cpu_power import CpuPowerInspector

@dataclass
class HardwareInventory:
    cpu_model: str
    physical_cores: int
    logical_threads: int
    vector_isa: List[str]
    has_avx512: bool
    pci_devices: List[str]
    npu_present: bool
    platform_profile_current: str
    platform_profile_choices: List[str]

class HardwareDiscovery:
    """Non-invasive read-only discovery engine."""

    def discover(self) -> HardwareInventory:
        cpu_model = "unknown"
        logical_threads = 0
        vector_isa = []
        has_avx512 = False

        if os.path.exists("/proc/cpuinfo"):
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name") and cpu_model == "unknown":
                            cpu_model = line.split(":", 1)[1].strip()
                        if line.startswith("processor"):
                            logical_threads += 1
                        if line.startswith("flags"):
                            flags = line.split(":", 1)[1].strip().split()
                            for fl in ["avx", "avx2", "avx512f", "avx512vl", "avx512_vnni", "avx512_bf16"]:
                                if fl in flags and fl not in vector_isa:
                                    vector_isa.append(fl)
                            has_avx512 = "avx512f" in flags
            except Exception:
                pass

        # ACPI platform profile
        pp_curr = "unknown"
        pp_choices = []
        if os.path.exists("/sys/firmware/acpi/platform_profile"):
            try:
                with open("/sys/firmware/acpi/platform_profile") as f:
                    pp_curr = f.read().strip()
            except Exception:
                pass
        if os.path.exists("/sys/firmware/acpi/platform_profile_choices"):
            try:
                with open("/sys/firmware/acpi/platform_profile_choices") as f:
                    pp_choices = f.read().strip().split()
            except Exception:
                pass

        # NPU check
        npu_present = os.path.isdir("/sys/class/accel") and bool(os.listdir("/sys/class/accel"))

        # PCI summary
        pci_devs = []
        if shutil.which("lspci"):
            try:
                res = subprocess.run(["lspci"], capture_output=True, text=True, timeout=2)
                pci_devs = [l.strip() for l in res.stdout.strip().split("\n") if l.strip()]
            except Exception:
                pass

        return HardwareInventory(
            cpu_model=cpu_model,
            physical_cores=CpuPowerInspector()._physical_cores() or logical_threads or 1,
            logical_threads=logical_threads,
            vector_isa=vector_isa,
            has_avx512=has_avx512,
            pci_devices=pci_devs,
            npu_present=npu_present,
            platform_profile_current=pp_curr,
            platform_profile_choices=pp_choices
        )
