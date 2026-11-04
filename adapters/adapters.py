import os
import shutil
import subprocess
from typing import Dict, Any
from .base import HardwareAdapter, CapabilityStatus, AdapterProbeResult


def _smi_number(value):
    """nvidia-smi prints '[N/A]' for fields a GPU does not report (e.g. laptop power limits)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

class LinuxInventoryAdapter(HardwareAdapter):
    name = "linux_inventory"
    is_read_only = True

    def probe(self) -> AdapterProbeResult:
        if not os.path.exists("/proc/cpuinfo"):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="/proc/cpuinfo not available (non-Linux environment)"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="linux_host_root"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        info = {"cpu_model": "unknown", "cores": 0}
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name") and info["cpu_model"] == "unknown":
                        info["cpu_model"] = line.split(":", 1)[1].strip()
                    if line.startswith("processor"):
                        info["cores"] += 1
        except Exception as e:
            info["error"] = str(e)
        return info


class LinuxCpuPerfAdapter(HardwareAdapter):
    name = "linux_cpu_perf"
    is_read_only = False

    def probe(self) -> AdapterProbeResult:
        has_ctl = shutil.which("powerprofilesctl") is not None
        has_sysfs = os.path.exists("/sys/firmware/acpi/platform_profile")
        if not (has_ctl or has_sysfs):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="Neither powerprofilesctl nor platform_profile sysfs interface found"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="acpi_platform_profile"
        )

    def get_current_profile(self) -> str:
        if shutil.which("powerprofilesctl"):
            try:
                res = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                pass
        pp_path = "/sys/firmware/acpi/platform_profile"
        if os.path.exists(pp_path):
            try:
                with open(pp_path) as f:
                    return f.read().strip()
            except Exception:
                pass
        return "unknown"


class NvidiaTelemetryAdapter(HardwareAdapter):
    name = "nvidia_telemetry"
    is_read_only = True

    def probe(self) -> AdapterProbeResult:
        if shutil.which("nvidia-smi") is None and not os.path.exists("/dev/nvidia0"):
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="NVIDIA drivers or devices not detected on host"
            )
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id="nvidia_gpu_adapter"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,pstate,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=3
                )
                if res.returncode == 0:
                    parts = [p.strip() for p in res.stdout.strip().split(",")]
                    return {
                        "gpu_name": parts[0] if len(parts) > 0 else "unknown",
                        "pstate": parts[1] if len(parts) > 1 else "unknown",
                        "temp_c": _smi_number(parts[2]) if len(parts) > 2 else None,
                        "power_w": _smi_number(parts[3]) if len(parts) > 3 else None,
                    }
            except Exception as e:
                return {"error": str(e)}
        return {"status": "device_present_no_smi"}


class AmdXdnaDiscoveryAdapter(HardwareAdapter):
    name = "amd_xdna_discovery"
    is_read_only = True

    def __init__(self, sysfs_root: str = ""):
        # Prefix for every sysfs/dev path; tests point it at a fake tree.
        self.sysfs_root = sysfs_root

    def _find_accel(self):
        """Return (accel_name, pci_slot, pci_dir) for the first AMD accel node, else None."""
        class_dir = self.sysfs_root + "/sys/class/accel"
        try:
            names = sorted(os.listdir(class_dir))
        except OSError:
            return None
        for name in names:
            dev_link = os.path.join(class_dir, name, "device")
            pci_dir = os.path.realpath(dev_link)
            try:
                with open(os.path.join(pci_dir, "vendor")) as f:
                    vendor = f.read().strip()
            except OSError:
                continue
            if vendor == "0x1022":
                return name, os.path.basename(pci_dir), pci_dir
        return None

    def probe(self) -> AdapterProbeResult:
        found = self._find_accel()
        if found is None:
            return AdapterProbeResult(
                status=CapabilityStatus.NOT_APPLICABLE,
                reason="No AMD accel device found under /sys/class/accel"
            )
        _, slot, pci_dir = found
        try:
            with open(os.path.join(pci_dir, "device")) as f:
                device = f.read().strip()
        except OSError:
            device = "unknown"
        return AdapterProbeResult(
            status=CapabilityStatus.SUPPORTED,
            device_id=f"amd_xdna_{slot}_1022:{device.replace('0x', '')}"
        )

    def read_telemetry(self) -> Dict[str, Any]:
        found = self._find_accel()
        if found is None:
            return {"status": "ABSENT"}
        name, slot, pci_dir = found
        fw = "unknown"
        try:
            with open(os.path.join(pci_dir, "fw_version")) as f:
                fw = f.read().strip()
        except OSError:
            pass
        driver_link = os.path.join(pci_dir, "driver")
        driver = os.path.basename(os.path.realpath(driver_link)) if os.path.islink(driver_link) else None
        return {
            "device_node": f"/dev/accel/{name}",
            "pci_slot": slot,
            "driver": driver,
            "firmware_version": fw,
            "userspace_xrt_installed": shutil.which("xrt-smi") is not None
        }
